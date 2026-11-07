from app.discovery import (
    hosts_for_cidr,
    is_wled_info,
    mac_from_properties,
    normalize_mac,
    parse_extra_hosts,
    parse_info,
    parse_node_targets,
    parse_subnets,
    select_services,
    service_priority,
    validate_subnet,
)


def test_normalize_mac_formats():
    assert normalize_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa:bb:cc:dd:ee:ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("nope") is None
    assert normalize_mac("") is None


def test_parse_info_uses_probed_address_and_release():
    facts = parse_info(
        {
            "ver": "16.0.1",
            "name": "Dig-Uno Shelf",
            "mac": "a1b2c3d4e5f0",
            "arch": "ESP32",
            "rel": "Dig-Uno-V3",
            "brand": "WLED",
            "product": "FOSS",
            "core": "v3.3.6",
            "ip": "10.0.0.9",
            "leds": {"count": 150, "pwr": 10},
            "wifi": {"rssi": -61},
        },
        "192.168.1.40",
        80,
    )
    assert facts.mac == "a1:b2:c3:d4:e5:f0"
    assert facts.ip == "192.168.1.40"
    assert facts.arch == "esp32"
    assert facts.release_name == "Dig-Uno-V3"
    assert facts.led_count == 150
    assert facts.rssi == -61
    assert facts.version == "16.0.1"


def test_parse_info_rejects_missing_mac_and_non_wled():
    assert not is_wled_info({"hello": 1})
    assert not is_wled_info({"ver": "1.0"})
    assert is_wled_info({"ver": "0.14.4", "mac": "aabbccddeeff"})
    try:
        parse_info({"ver": "0.14.4", "arch": "esp32", "leds": {"count": 1}}, "127.0.0.1", 80)
    except ValueError as exc:
        assert "MAC" in str(exc)
    else:
        raise AssertionError("expected missing mac to fail")


def test_subnet_limits():
    assert validate_subnet("192.168.1.0/24") == "192.168.1.0/24"
    hosts = hosts_for_cidr("192.168.1.0/30")
    assert hosts == ["192.168.1.1", "192.168.1.2"]
    assert hosts_for_cidr("127.0.0.1/32") == ["127.0.0.1"]
    try:
        validate_subnet("10.0.0.0/16")
    except ValueError as exc:
        assert "/22" in str(exc)
    else:
        raise AssertionError("expected large subnet to fail")
    good, errors = parse_subnets("192.168.1.0/24, 10.1.0.0/8")
    assert good == ["192.168.1.0/24"]
    assert errors


def test_extra_hosts_and_nodes():
    hosts, errors = parse_extra_hosts("shelf:9091, http://porch/json, 10.0.0.8")
    assert errors == []
    assert hosts == [("shelf", 9091), ("porch", 80), ("10.0.0.8", 80)]
    targets = parse_node_targets(
        {"nodes": [{"ip": "192.168.1.20"}, {"ip": "0.0.0.0"}, {"ip": "mock-porch:9092"}, {"name": "skip"}]}
    )
    assert targets == [("192.168.1.20", 80), ("mock-porch", 9092)]


def test_mdns_priority_and_mac_property():
    assert mac_from_properties({"mac": "AABBCCDDEEFF"}) == "aa:bb:cc:dd:ee:ff"
    assert service_priority("_wled._tcp.local.", "Anything") == 0
    assert service_priority("_http._tcp.local.", "WLED-Kitchen._http._tcp.local.") == 1
    assert service_priority("_http._tcp.local.", "Printer._http._tcp.local.") == 2
    selected = select_services(
        [
            {"name": f"printer-{i}", "type": "_http._tcp.local."}
            for i in range(70)
        ]
        + [{"name": "WLED-1._wled._tcp.local.", "type": "_wled._tcp.local."}],
        http_limit=64,
    )
    assert any(item["type"] == "_wled._tcp.local." for item in selected)
    assert len(selected) == 65
