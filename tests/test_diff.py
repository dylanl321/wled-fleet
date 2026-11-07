from app.config_edit import apply_led_counts
from app.diffutil import json_diff, mask_secrets


def test_mask_and_diff():
    before = {"id": {"name": "Porch"}, "nw": {"ins": [{"ssid": "House", "psk": "secret"}]}, "hw": {"led": {"total": 60}}}
    after = {"id": {"name": "Porch strip"}, "nw": {"ins": [{"ssid": "House", "psk": "secret"}]}, "hw": {"led": {"total": 90}}}
    masked = mask_secrets(before)
    assert masked["nw"]["ins"][0]["psk"] == "••••"
    assert masked["nw"]["ins"][0]["ssid"] == "House"
    changes = json_diff(mask_secrets(before), mask_secrets(after))
    paths = {item["path"]: item for item in changes}
    assert paths["id.name"]["before"] == "Porch"
    assert paths["id.name"]["after"] == "Porch strip"
    assert paths["hw.led.total"]["after"] == 90
    assert "nw.ins[0].psk" not in paths


def test_led_edit_keeps_wifi_and_pins():
    cfg = {
        "nw": {"ins": [{"ssid": "House", "psk": "secret"}]},
        "ap": {"ssid": "WLED-AP", "psk": "wled1234"},
        "hw": {"led": {"total": 30, "maxpwr": 850, "ins": [{"start": 0, "len": 30, "pin": [16], "type": 22}]}},
    }
    apply_led_counts(cfg, [{"start": 0, "len": 120}])
    assert cfg["hw"]["led"]["total"] == 120
    assert cfg["hw"]["led"]["ins"][0]["len"] == 120
    assert cfg["hw"]["led"]["ins"][0]["pin"] == [16]
    assert cfg["nw"]["ins"][0]["psk"] == "secret"
    assert cfg["ap"]["ssid"] == "WLED-AP"
    try:
        apply_led_counts(cfg, [{"start": 0, "len": 10}, {"start": 10, "len": 10}])
    except ValueError as exc:
        assert "count" in str(exc)
    else:
        raise AssertionError("expected bus count mismatch")
