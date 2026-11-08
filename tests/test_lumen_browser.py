"""Phone-sized Lumen UI against the WLED simulator, in Chromium and WebKit."""

import threading
import time

import httpx
import pytest
import uvicorn

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.main import create_app
from mock.wled_mock import MockSpec
from tests.support import Server, free_port, start_mock, stop_mock


@pytest.fixture()
def house(tmp_path):
    porch = MockSpec(
        mac="aa11bb22cc33",
        name="Dig-Uno Porch",
        mdns="porch-ui",
        arch="esp32",
        release="Dig-Uno-V3",
        version="0.15.1",
        leds=120,
        buses=((0, 80, 16), (80, 40, 1)),
        reboot_seconds=0.15,
    )
    shelf = MockSpec(
        mac="bb22cc33dd44",
        name="Dig-Uno Shelf",
        mdns="shelf-ui",
        arch="esp8266",
        release="ESP8266",
        version="0.14.4",
        leds=60,
        reboot_seconds=0.15,
    )
    porch_server, porch_port = start_mock(porch)
    shelf_server, shelf_port = start_mock(shelf)
    port = free_port()
    settings = Settings(
        data_dir=tmp_path,
        port=port,
        scheduler_enabled=False,
        extra_hosts=f"127.0.0.1:{porch_port},127.0.0.1:{shelf_port}",
    )
    app = create_app(settings)
    server = Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    ready = None
    while time.monotonic() < deadline:
        try:
            ready = httpx.get(f"http://127.0.0.1:{port}/api/lumen/home", timeout=1).json()
        except httpx.HTTPError:
            time.sleep(0.05)
            continue
        if len(ready.get("pending") or []) >= 3:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"home did not discover the simulators: {ready}")
    try:
        yield {"url": f"http://127.0.0.1:{port}", "porch": porch_port, "shelf": shelf_port}
    finally:
        server.should_exit = True
        server.force_exit = True
        stop_mock(porch_server)
        stop_mock(shelf_server)


def _writes(port):
    return httpx.get(f"http://127.0.0.1:{port}/debug/ws", timeout=2).json()["writes"]


def _settle_writes(port, quiet=0.2, timeout=4.0):
    """Wait until the controller has had no new state writes for `quiet` seconds.

    A preview that is still in flight when the test samples the log shows up
    late, next to the following preview, and the gap looks shorter than the
    preview interval.
    """
    deadline = time.monotonic() + timeout
    previous = len(_writes(port))
    stable = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.02)
        count = len(_writes(port))
        if count != previous:
            previous = count
            stable = time.monotonic()
            continue
        if time.monotonic() - stable >= quiet:
            return
    raise AssertionError("controller writes did not settle")


def _wait_for_commit(port, after, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in _writes(port):
            if row["t"] > after and row["body"].get("tt") == 4:
                return row
        time.sleep(0.02)
    raise AssertionError("scrub did not commit")


def _preview_times(port):
    times = []
    for row in _writes(port):
        body = row["body"]
        segments = body.get("seg") or []
        if body.get("tt") != 0 or not segments or any(segment.get("i") for segment in segments):
            continue
        if any("fx" in segment or "bri" in segment for segment in segments):
            times.append(row["t"])
    return times


@pytest.mark.parametrize("browser_name", ["chromium", "webkit"])
def test_phone_flow_on_one_screen(house, browser_name):
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, browser_name)
        try:
            browser = browser_type.launch()
        except Exception as exc:
            pytest.skip(f"{browser_name} is not installed: {exc}")
        device = playwright.devices["iPhone 12"]
        context = browser.new_context(**device)
        context.add_init_script(
            """
            Object.defineProperty(navigator, "standalone", { configurable: true, get: () => true });
            const original = window.matchMedia.bind(window);
            window.matchMedia = (query) => {
              if (String(query).includes("display-mode")) {
                const standalone = String(query).includes("standalone");
                return {
                  matches: standalone,
                  media: query,
                  addEventListener() {},
                  removeEventListener() {},
                  addListener() {},
                  removeListener() {},
                  dispatchEvent() { return false; },
                };
              }
              return original(query);
            };
            """
        )
        page = context.new_page()
        page.goto(house["url"], wait_until="domcontentloaded")
        page.wait_for_selector("[data-status-alert]", timeout=8000)
        assert page.locator("html").evaluate("el => el.classList.contains('standalone')")
        assert page.locator("[data-screen='home'][data-active='true'] [data-light]").count() == 0
        page.locator("[data-status-alert]").click()
        page.wait_for_selector("[data-screen='maintenance'][data-active='true']")
        assert page.locator("[data-screen='home'][data-active='true']").count() == 0
        page.get_by_role("button", name="Set up").first.click()
        page.wait_for_selector("[data-screen='find'][data-active='true'] [data-ruler]")
        page.locator("[data-tick='25']").first.click()
        page.wait_for_function("() => document.querySelector('[data-span-count]')?.textContent === '25'")
        painted = None
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            for row in _writes(house["porch"]):
                segments = row["body"].get("seg") or []
                if segments and segments[0].get("i"):
                    painted = segments[0]["i"]
                    break
            if painted:
                break
            time.sleep(0.05)
        assert painted is not None
        # Segment-relative ranges: the output is cleared, then LEDs 25-50 are lit.
        triples = [tuple(painted[index : index + 3]) for index in range(0, len(painted), 3)]
        assert any(low == 25 and high == 50 for low, high, _color in triples), triples
        page.get_by_label("Light name").first.fill("Porch rail")
        page.get_by_role("button", name="New zone").first.click()
        page.get_by_label("Zone name").fill("Front of house")
        page.get_by_role("button", name="Done").first.click()
        page.wait_for_selector("[data-screen='home'][data-active='true'] [data-light-name='Porch rail']")
        page.locator("[data-screen='home'] [data-light-name='Porch rail']").click()
        page.wait_for_selector("[data-paint][data-open='true']")
        assert page.locator("[data-screen='home'][data-active='true']").count() == 1
        assert page.locator("[data-screen][data-active='true']").count() == 1

        _settle_writes(house["porch"])
        before = len(_preview_times(house["porch"]))
        tray = page.locator("[data-look-tray]")
        box = tray.bounding_box()
        assert box
        start_x = box["x"] + box["width"] * 0.75
        y = box["y"] + box["height"] * 0.4
        page.mouse.move(start_x, y)
        page.mouse.down()
        for step in range(1, 14):
            page.mouse.move(start_x - step * 22, y)
            page.wait_for_timeout(90)
        rows = _writes(house["porch"])
        stamp = rows[-1]["t"] if rows else 0
        page.mouse.up()
        _wait_for_commit(house["porch"], stamp)
        _settle_writes(house["porch"])
        times = _preview_times(house["porch"])[before:]
        assert len(times) >= 3, times
        gaps = [times[index + 1] - times[index] for index in range(len(times) - 1)]
        assert min(gaps) >= 0.12, gaps
        commits = [row for row in _writes(house["porch"]) if row["body"].get("tt") == 4]
        assert commits, "scrub should commit with a 400 ms transition"

        ribbon = page.locator("[data-screen='home'] [data-light-name='Porch rail']").locator("xpath=..")
        ribbon_box = ribbon.bounding_box()
        page.mouse.move(ribbon_box["x"] + 24, ribbon_box["y"] + 22)
        page.mouse.down()
        page.mouse.move(ribbon_box["x"] + 40, ribbon_box["y"] + 22)
        page.wait_for_timeout(80)
        page.mouse.move(ribbon_box["x"] + 160, ribbon_box["y"] + 24, steps=8)
        page.wait_for_timeout(250)
        page.mouse.up()
        page.wait_for_selector("[data-undo]", timeout=3000)
        page.locator("[data-undo]").click()
        page.wait_for_timeout(200)

        assert page.evaluate("() => window.__screenViolations") == 0
        page.wait_for_timeout(120)
        page.route("**/api/lumen/home", lambda route: route.abort())
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("[data-screen='home'] [data-light-name='Porch rail']")
        assert page.locator("[data-screen][data-active='true']").count() == 1
        cached = page.evaluate("() => !!(window.__lumenCache && window.__lumenCache.lights && window.__lumenCache.lights.length)")
        assert cached
        browser.close()
