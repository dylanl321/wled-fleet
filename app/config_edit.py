from __future__ import annotations


def apply_led_counts(cfg: dict, buses: list[dict]) -> dict:
    """Update LED output lengths in a cfg object.

    Wi-Fi and AP blocks are left untouched. Callers upload the whole cfg.json
    because WLED rebuilds LED buses from that file on boot. A partial settings
    form post can wipe the SSID when fields are omitted.
    """
    hw = cfg.setdefault("hw", {})
    if not isinstance(hw, dict):
        raise ValueError("Controller config has no hardware block.")
    led = hw.setdefault("led", {})
    if not isinstance(led, dict):
        raise ValueError("Controller config has no LED block.")
    ins = led.get("ins")
    if not isinstance(ins, list) or not ins:
        raise ValueError("Controller has no LED outputs in its config.")
    if len(ins) != len(buses):
        raise ValueError("LED output count does not match the controller.")
    total = 0
    for entry, patch in zip(ins, buses):
        if not isinstance(entry, dict):
            raise ValueError("Unexpected LED output config.")
        start = int(patch["start"])
        length = int(patch["len"])
        if start < 0 or length < 1 or start + length > 8192:
            raise ValueError("LED range is out of bounds.")
        entry["start"] = start
        entry["len"] = length
        total += length
    led["total"] = total
    return cfg
