from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
STATIC = ROOT / "app" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
FX_JS = (STATIC / "fx.js").read_text(encoding="utf-8")
CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
SW_JS = (STATIC / "sw.js").read_text(encoding="utf-8")


def test_no_tab_bar_and_two_real_screens():
    # Home and the Paint sheet; Find and Maintenance are pushed screens.
    assert "tab-slot" not in HTML and "data-pane" not in HTML
    assert 'id="sheet"' in HTML and 'id="zones"' in HTML and 'id="screen"' in HTML
    assert "function openMaintenance(" in APP_JS and "function openFind(" in APP_JS


def test_chrome_has_no_accent_color():
    # Every hue on screen is a pixel. Amber is reserved for "needs attention", red for destructive.
    colors = set(re.findall(r"#[0-9A-Fa-f]{6}\b", CSS))
    allowed = {"#0B0B0D", "#E8E8EA", "#8A8A90", "#141416", "#151518", "#1C1C20", "#FF453A", "#FFB340", "#121215",
               "#2A2A2F", "#5A5A62", "#1E1E22", "#4A4A50", "#3A3A40", "#FFB000"}
    assert colors <= allowed, colors - allowed


def test_ribbons_follow_the_layout_spec():
    assert "--ribbon-h: 44px" in CSS and "--ribbon-gap: 8px" in CSS and "--ribbon-r: 12px" in CSS
    assert "border-radius: 24px 24px 0 0" in CSS
    assert "width: 88px" in CSS


def test_writes_are_optimistic_and_go_over_the_one_socket():
    assert "/api/stream" in APP_JS
    assert 'send({ t: "apply"' in APP_JS
    assert "function predict(" in APP_JS
    assert "function commit(" in APP_JS


def test_scrub_previews_are_throttled_and_commit_on_settle():
    assert "const PREVIEW_MS = 150;" in APP_JS
    assert "const PREVIEW_MANY_MS = 400;" in APP_JS
    assert "const MANY = 8;" in APP_JS
    assert "previewing ${count} lights" in APP_JS
    assert "const COMMIT_TT = 4;" in APP_JS


def test_gestures_are_wired():
    assert "Math.abs(dx) > 12 && Math.abs(dy) < 6" in APP_JS  # brightness drag threshold
    assert "function powerSwipe(" in APP_JS
    assert "function startRename(" in APP_JS
    assert "function startReorder(" in APP_JS
    assert "function identify(" in APP_JS
    assert "bindUndoGesture" in APP_JS


def test_cache_first_relaunch():
    assert "function loadCache()" in APP_JS and "function saveCache()" in APP_JS
    assert "10 * 60 * 1000" in APP_JS  # stale frames render desaturated


def test_effect_sketches_cover_the_starter_looks():
    for family in ("candle", "aurora", "ocean", "twinkle", "rainbow", "fire", "breathe", "chase"):
        assert f'"{family}"' in FX_JS


def test_service_worker_replaces_previous_static_assets():
    assert 'const CACHE = "lumen-1";' in SW_JS
    assert '"/static/fx.js"' in SW_JS
    assert "self.skipWaiting()" in SW_JS
    assert "self.clients.claim()" in SW_JS
