from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

_MAC_RE = re.compile(r"^[0-9a-f]{12}$")


@dataclass
class DeviceFacts:
    mac: str
    ip: str
    port: int
    name: str
    arch: str | None = None
    core: str | None = None
    version: str | None = None
    release_name: str | None = None
    brand: str | None = None
    product: str | None = None
    led_count: int | None = None
    rssi: int | None = None
    info: dict | None = None


def normalize_mac(value: str | None) -> str | None:
    if not value:
        return None
    hexed = re.sub(r"[^0-9a-fA-F]", "", value)
    if not _MAC_RE.match(hexed.lower()):
        return None
    hexed = hexed.lower()
    return ":".join(hexed[i : i + 2] for i in range(0, 12, 2))


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def is_wled_info(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    if _text(data.get("ver")) is None:
        return False
    if normalize_mac(_text(data.get("mac"))):
        return True
    leds = data.get("leds")
    return isinstance(leds, dict) and _text(data.get("arch")) is not None


def parse_info(data: dict, ip: str, port: int) -> DeviceFacts:
    if not is_wled_info(data):
        raise ValueError("response is not a WLED info document")
    mac = normalize_mac(_text(data.get("mac")))
    if mac is None:
        raise ValueError("WLED info has no MAC address")
    leds = data.get("leds") if isinstance(data.get("leds"), dict) else {}
    wifi = data.get("wifi") if isinstance(data.get("wifi"), dict) else {}
    release = _text(data.get("rel")) or _text(data.get("release"))
    arch = _text(data.get("arch"))
    return DeviceFacts(
        mac=mac,
        ip=ip,
        port=port,
        name=_text(data.get("name")) or "WLED",
        arch=arch.lower() if arch else None,
        core=_text(data.get("core")),
        version=_text(data.get("ver")),
        release_name=release,
        brand=_text(data.get("brand")),
        product=_text(data.get("product")),
        led_count=_int(leds.get("count") if isinstance(leds, dict) else None),
        rssi=_int(wifi.get("rssi") if isinstance(wifi, dict) else None),
        info=data,
    )


def validate_subnet(cidr: str) -> str:
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise ValueError(f"{cidr} is not an IPv4 network") from exc
    if network.version != 4:
        raise ValueError(f"{cidr} is not an IPv4 network")
    if network.prefixlen < 22:
        raise ValueError(f"{cidr} is larger than /22; split it or use mDNS")
    return str(network)


def hosts_for_cidr(cidr: str) -> list[str]:
    network = ipaddress.ip_network(validate_subnet(cidr), strict=False)
    if network.prefixlen >= 31:
        return [str(ip) for ip in network]
    return [str(ip) for ip in network.hosts()]


def parse_subnets(text: str) -> tuple[list[str], list[str]]:
    good: list[str] = []
    errors: list[str] = []
    for part in re.split(r"[\s,]+", text.strip()):
        if not part:
            continue
        try:
            good.append(validate_subnet(part))
        except ValueError as exc:
            errors.append(str(exc))
    return good, errors


def parse_host(spec: str) -> tuple[str, int]:
    value = spec.strip()
    if not value:
        raise ValueError("empty host")
    if "://" in value:
        parsed = urlparse(value)
        if not parsed.hostname:
            raise ValueError(f"invalid host {spec}")
        port = parsed.port or 80
        if not 1 <= port <= 65535:
            raise ValueError(f"invalid port in {spec}")
        return parsed.hostname, port
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            raise ValueError(f"invalid host {spec}")
        host = value[1:end]
        rest = value[end + 1 :]
        if rest.startswith(":"):
            port = int(rest[1:])
            if not 1 <= port <= 65535:
                raise ValueError(f"invalid port in {spec}")
            return host, port
        return host, 80
    if value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)
        if port_text.isdigit():
            port = int(port_text)
            if not 1 <= port <= 65535:
                raise ValueError(f"invalid port in {spec}")
            return host, port
    return value, 80


def parse_extra_hosts(text: str) -> tuple[list[tuple[str, int]], list[str]]:
    hosts: list[tuple[str, int]] = []
    errors: list[str] = []
    for part in re.split(r"[\s,]+", text.strip()):
        if not part:
            continue
        try:
            hosts.append(parse_host(part))
        except ValueError as exc:
            errors.append(str(exc))
    return hosts, errors


def decode_properties(properties: dict) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in properties.items():
        name = key.decode(errors="replace") if isinstance(key, bytes) else str(key)
        if value is None:
            decoded[name] = ""
        elif isinstance(value, bytes):
            decoded[name] = value.decode(errors="replace")
        else:
            decoded[name] = str(value)
    return decoded


def mac_from_properties(properties: dict[str, str]) -> str | None:
    for key in ("mac", "MAC", "id"):
        mac = normalize_mac(properties.get(key))
        if mac:
            return mac
    return None


def service_priority(service_type: str, name: str) -> int:
    if "_wled._tcp" in service_type:
        return 0
    if "wled" in name.lower():
        return 1
    return 2


def select_services(records: list[dict], http_limit: int = 64) -> list[dict]:
    ranked = sorted(records, key=lambda record: (service_priority(record["type"], record["name"]), record["name"]))
    selected: list[dict] = []
    extras = 0
    for record in ranked:
        if service_priority(record["type"], record["name"]) >= 2:
            if extras >= http_limit:
                continue
            extras += 1
        selected.append(record)
    return selected


def parse_node_targets(payload: object) -> list[tuple[str, int]]:
    if not isinstance(payload, dict):
        return []
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return []
    targets: list[tuple[str, int]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        raw = node.get("ip")
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text or text == "0.0.0.0":
            continue
        try:
            targets.append(parse_host(text))
        except ValueError:
            continue
    return targets


def preset_entries(presets: object) -> list[dict]:
    if not isinstance(presets, dict):
        return []
    items: list[dict] = []
    for key, value in presets.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        name = value.get("n")
        items.append(
            {
                "id": int(key),
                "name": name.strip() if isinstance(name, str) and name.strip() else f"Preset {key}",
                "playlist": isinstance(value.get("playlist"), dict),
            }
        )
    items.sort(key=lambda item: item["id"])
    return items


def bus_summaries(cfg: object) -> list[dict]:
    if not isinstance(cfg, dict):
        return []
    led = cfg.get("hw", {})
    if not isinstance(led, dict):
        return []
    led = led.get("led", {})
    if not isinstance(led, dict):
        return []
    ins = led.get("ins")
    if not isinstance(ins, list):
        return []
    rows: list[dict] = []
    for entry in ins:
        if not isinstance(entry, dict):
            continue
        pins = entry.get("pin") if isinstance(entry.get("pin"), list) else []
        pin = pins[0] if pins else None
        rows.append(
            {
                "start": int(entry.get("start") or 0),
                "len": int(entry.get("len") or 0),
                "pin": pin if isinstance(pin, int) else None,
            }
        )
    return rows


def network_labels(cfg: object) -> dict[str, str | None]:
    ssid = None
    ap_ssid = None
    mdns = None
    name = None
    if isinstance(cfg, dict):
        identity = cfg.get("id") if isinstance(cfg.get("id"), dict) else {}
        mdns = _text(identity.get("mdns"))
        name = _text(identity.get("name"))
        nw = cfg.get("nw") if isinstance(cfg.get("nw"), dict) else {}
        ins = nw.get("ins") if isinstance(nw, dict) else None
        if isinstance(ins, list) and ins and isinstance(ins[0], dict):
            ssid = _text(ins[0].get("ssid"))
        ap = cfg.get("ap") if isinstance(cfg.get("ap"), dict) else {}
        ap_ssid = _text(ap.get("ssid")) if isinstance(ap, dict) else None
    return {"ssid": ssid, "ap_ssid": ap_ssid, "mdns": mdns, "name": name}
