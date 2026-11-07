import asyncio

import httpx

from app.config import Settings
from app.db import Database
from app.errors import FleetError
from app.fleet import Fleet
from mock.wled_mock import MockSpec
from tests.support import esp8266_image, esp_image, start_mock, stop_mock


def test_mock_is_discovered_backed_up_and_updated(tmp_path):
    porch_server, porch_port = start_mock(
        MockSpec(
            mac="b2c3d4e5f607",
            name="Dig-Uno Porch",
            mdns="dig-uno-porch",
            arch="esp8266",
            release="ESP8266",
            version="0.14.4",
            leds=60,
            reboot_seconds=0.25,
            rssi=-70,
        )
    )
    shelf_server, shelf_port = start_mock(
        MockSpec(
            mac="a1b2c3d4e5f0",
            name="Dig-Uno Shelf",
            mdns="dig-uno-shelf",
            arch="esp32",
            release="Dig-Uno-V3",
            version="16.0.1",
            leds=150,
            peers=[f"127.0.0.1:{porch_port}"],
            reboot_seconds=0.25,
        )
    )

    async def scenario():
        settings = Settings(
            data_dir=tmp_path,
            scheduler_enabled=False,
            extra_hosts=f"127.0.0.1:{shelf_port}",
            backup_retention=10,
        )
        db = Database(tmp_path / "fleet.db")
        try:
            async with httpx.AsyncClient() as http:
                fleet = Fleet(db, settings, http)
                summary = await fleet.scan_once(full=True)
                assert summary["added"] == 2
                devices = {row["mac"]: row for row in db.list_devices()}
                shelf_mac = "a1:b2:c3:d4:e5:f0"
                porch_mac = "b2:c3:d4:e5:f6:07"
                assert devices[shelf_mac]["led_count"] == 150
                assert devices[porch_mac]["arch"] == "esp8266"
                assert db.list_backups(shelf_mac)[0]["reason"] == "discover"
                assert not (await fleet.backup_device(shelf_mac, "manual")).created

                await fleet.rename(shelf_mac, "Shelf lights", "shelf-lights")
                renamed = db.get_device(shelf_mac)
                assert renamed["name"] == "Shelf lights"
                assert renamed["mdns"] == "shelf-lights"

                state = await fleet.set_state(shelf_mac, {"on": False, "bri": 40})
                assert state["on"] is False
                assert state["bri"] == 40
                await fleet.set_color(shelf_mac, 0, [0, 40, 255], True)
                live = await fleet.live(shelf_mac)
                assert live["online"] is True
                assert live["state"]["seg"][0]["col"][0] == [0, 40, 255]
                assert live["ssid"] == "House"
                assert {item["name"] for item in live["presets"]} >= {"Warm evening", "Night"}

                await fleet.set_leds(shelf_mac, [{"start": 0, "len": 120}])
                assert db.get_device(shelf_mac)["led_count"] == 120

                wrong = fleet.save_firmware(esp8266_image(), "WLED_0.14.4_ESP8266.bin", "upload")
                try:
                    await fleet.update_firmware(shelf_mac, wrong["id"], force=False)
                except FleetError as exc:
                    assert exc.status == 409
                else:
                    raise AssertionError("mismatched firmware was flashed")
                assert db.get_device(shelf_mac)["version"] == "16.0.1"

                try:
                    fleet.save_firmware(esp_image(0), "WLED_0.14.4_ESP8266.bin", "upload")
                except FleetError as exc:
                    assert "header" in str(exc)
                else:
                    raise AssertionError("conflicting image was stored")

                good = fleet.save_firmware(esp_image(0), "WLED_16.0.2_Dig-Uno-V3.bin", "upload")
                before = len(db.list_backups(shelf_mac))
                result = await fleet.update_firmware(shelf_mac, good["id"], force=False)
                assert result["version"] == "16.0.2"
                assert db.get_device(shelf_mac)["version"] == "16.0.2"
                # The pre-update snapshot matched the latest backup, so it was not stored twice.
                assert len(db.list_backups(shelf_mac)) == before
                assert before >= 1
        finally:
            db.close()

    try:
        asyncio.run(scenario())
    finally:
        stop_mock(shelf_server)
        stop_mock(porch_server)
