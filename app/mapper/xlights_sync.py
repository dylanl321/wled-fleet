"""Diff the fleet registry against an xLights show, and apply the gaps.

xlights_networks.xml holds the controllers (name, IP, DDP, MaxChannels).
xlights_rgbeffects.xml holds each model's Controller attribute. A preview
changes nothing. apply=true writes only after a timestamped copy of each file.

What gets written: a placeholder whose channel count is 1, a fleet board the
show does not list yet, an IP that no longer matches the board's name, and a
model with no controller when one match is unambiguous. A model with two
possible controllers is listed and left alone.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from app.mapper.controllers import channels_for_order

NETWORKS = "xlights_networks.xml"
EFFECTS = "xlights_rgbeffects.xml"
UNASSIGNED = {"", "none", "no controller", "unassigned"}


def sync_show(show_dir: Path, controllers: list[dict], fixtures: list[dict] | None, *, apply: bool) -> dict:
    folder = Path(show_dir).expanduser()
    networks_path = folder / NETWORKS
    effects_path = folder / EFFECTS
    if not networks_path.is_file():
        raise FileNotFoundError(f"{NETWORKS} is not in the xLights show folder.")
    networks_text = networks_path.read_text(encoding="utf-8")
    effects_text = effects_path.read_text(encoding="utf-8") if effects_path.is_file() else None
    diff = build_diff(networks_text, effects_text, controllers, fixtures or [])
    result = {
        "apply": apply,
        "wrote": False,
        "backups": [],
        "show": str(folder),
        **diff,
    }
    if not apply or not _needs_write(diff):
        return result
    backups = []
    for path in (networks_path, effects_path):
        if path.is_file():
            backups.append(str(_backup(path)))
    updated_networks, updated_effects = apply_diff(networks_text, effects_text, diff, controllers)
    networks_path.write_text(updated_networks, encoding="utf-8")
    if updated_effects is not None and effects_path.is_file():
        effects_path.write_text(updated_effects, encoding="utf-8")
    result["wrote"] = True
    result["backups"] = backups
    return result


def build_diff(networks_xml: str, effects_xml: str | None, controllers: list[dict], fixtures: list[dict]) -> dict:
    root = _parse(networks_xml)
    listed = [_summarise_controller(node) for node in _controller_nodes(root)]
    matched_ids: set[str] = set()
    placeholders = []
    ip_changes = []
    channel_drift = []
    unchanged = []
    unknown = []
    for item in listed:
        fleet, how = match_controller(item, controllers)
        if fleet is None:
            unknown.append({"name": item["name"], "ip": item["ip"], "reason": how})
            continue
        matched_ids.add(str(fleet["id"]))
        channels = registry_channels(fleet)
        entry = {
            "name": item["name"],
            "fleet": fleet["name"],
            "id": fleet["id"],
            "match": how,
            "channels": item["channels"],
            "registryChannels": channels,
            "ip": item["ip"],
            "registryIp": fleet["ip"],
        }
        if item["channels"] <= 1 and channels > 1:
            placeholders.append(entry)
        elif item["channels"] != channels:
            channel_drift.append(entry)
        if item["ip"] and fleet.get("ip") and item["ip"] != fleet["ip"]:
            ip_changes.append({"name": item["name"], "id": fleet["id"], "from": item["ip"], "to": fleet["ip"]})
        if item["channels"] > 1 and item["channels"] == channels and item["ip"] == fleet.get("ip"):
            unchanged.append(item["name"])
    missing = [
        {
            "name": controller["name"],
            "id": controller["id"],
            "ip": controller["ip"],
            "channels": registry_channels(controller),
            "protocol": "DDP",
        }
        for controller in controllers
        if str(controller["id"]) not in matched_ids
    ]
    models = _model_diff(effects_xml, controllers, fixtures)
    return {
        "controllers": {
            "placeholders": placeholders,
            "ipChanges": ip_changes,
            "channelDrift": channel_drift,
            "missing": missing,
            "unchanged": unchanged,
            "unknown": unknown,
        },
        "models": models,
    }


def drift_summary(diff: dict) -> dict:
    controllers = diff["controllers"]
    models = diff["models"]
    placeholders = len(controllers["placeholders"])
    missing = len(controllers["missing"])
    ip_changes = len(controllers["ipChanges"])
    unassigned = len(models["assign"]) + len(models["ambiguous"]) + len(models["unmatched"])
    ambiguous = len(models["ambiguous"])
    in_sync = placeholders == 0 and missing == 0 and ip_changes == 0 and not models["assign"] and ambiguous == 0
    return {
        "inSync": in_sync,
        "placeholders": placeholders,
        "missing": missing,
        "ipChanges": ip_changes,
        "unassigned": unassigned,
        "ambiguous": ambiguous,
        "channelDrift": len(controllers["channelDrift"]),
    }


def apply_diff(
    networks_xml: str,
    effects_xml: str | None,
    diff: dict,
    controllers: list[dict],
) -> tuple[str, str | None]:
    root = _parse(networks_xml)
    by_name = {item["name"]: item for item in diff["controllers"]["placeholders"]}
    ips = {item["name"]: item["to"] for item in diff["controllers"]["ipChanges"]}
    for node in _controller_nodes(root):
        summary = _summarise_controller(node)
        if summary["name"] in by_name:
            _set_channels(node, int(by_name[summary["name"]]["registryChannels"]))
        if summary["name"] in ips:
            _set_ip(node, ips[summary["name"]])
    parent = root if _local(root.tag) == "networks" else root.find("networks") or root
    next_id = _next_id(root)
    for item in diff["controllers"]["missing"]:
        parent.append(_new_controller(item, next_id))
        next_id += 1
    networks_out = _tostring(root)
    if effects_xml is None or not diff["models"]["assign"]:
        return networks_out, effects_xml
    effects = _parse(effects_xml)
    wanted = {item["model"]: item["controller"] for item in diff["models"]["assign"]}
    for node in _model_nodes(effects):
        name = (node.attrib.get("name") or "").strip()
        if name in wanted and _controller_of(node) is None:
            node.attrib["Controller"] = wanted[name]
    return networks_out, _tostring(effects)


def match_controller(item: dict, controllers: list[dict]) -> tuple[dict | None, str]:
    name = item["name"].strip().lower()
    by_name = [controller for controller in controllers if str(controller.get("name") or "").strip().lower() == name]
    if len(by_name) == 1:
        return by_name[0], "name"
    if len(by_name) > 1:
        return None, "ambiguous-name"
    ip = item.get("ip") or ""
    if ip:
        by_ip = [controller for controller in controllers if str(controller.get("ip") or "") == ip]
        if len(by_ip) == 1:
            return by_ip[0], "ip"
        if len(by_ip) > 1:
            return None, "ambiguous-ip"
    return None, "unmatched"


def registry_channels(controller: dict) -> int:
    total = 0
    for output in controller.get("outputs") or []:
        total += int(output.get("pixelCount") or 0) * channels_for_order(str(output.get("colorOrder") or "GRB"))
    return total


def registry_pixels(controller: dict) -> int:
    return sum(int(output.get("pixelCount") or 0) for output in controller.get("outputs") or [])


def _model_diff(effects_xml: str | None, controllers: list[dict], fixtures: list[dict]) -> dict:
    if effects_xml is None:
        return {"assign": [], "ambiguous": [], "unmatched": [], "assigned": [], "zeroPixels": [], "missingFile": True}
    root = _parse(effects_xml)
    assign = []
    ambiguous = []
    unmatched = []
    assigned = []
    zero = []
    for node in _model_nodes(root):
        name = (node.attrib.get("name") or "").strip() or "(unnamed)"
        pixels = _model_pixels(node)
        if pixels == 0:
            zero.append(name)
        current = _controller_of(node)
        if current is not None:
            assigned.append({"model": name, "controller": current})
            continue
        choice, candidates, reason = _match_model(name, pixels, controllers, fixtures)
        if choice is not None:
            assign.append({"model": name, "controller": choice, "reason": reason})
        elif len(candidates) > 1:
            ambiguous.append({"model": name, "candidates": candidates})
        else:
            unmatched.append({"model": name, "reason": "no pixels" if pixels == 0 else "no single controller matches"})
    return {
        "assign": assign,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "assigned": assigned,
        "zeroPixels": zero,
        "missingFile": False,
    }


def _match_model(
    name: str,
    pixels: int,
    controllers: list[dict],
    fixtures: list[dict],
) -> tuple[str | None, list[str], str]:
    found: dict[str, str] = {}
    for controller in controllers:
        if str(controller.get("name") or "").strip().lower() == name.strip().lower():
            found[str(controller["name"])] = "name"
    fixture_hits = _fixture_controllers(name, fixtures, controllers)
    if len(fixture_hits) == 1:
        found.setdefault(fixture_hits[0], "fixture")
    elif len(fixture_hits) > 1:
        for hit in fixture_hits:
            found.setdefault(hit, "fixture")
    if pixels > 0:
        sized = [str(controller["name"]) for controller in controllers if registry_pixels(controller) == pixels]
        if len(sized) == 1:
            found.setdefault(sized[0], "pixels")
        elif len(sized) > 1:
            for hit in sized:
                found.setdefault(hit, "pixels")
    names = sorted(found)
    if len(names) == 1:
        only = names[0]
        return only, names, found[only]
    return None, names, ""


def _fixture_controllers(name: str, fixtures: list[dict], controllers: list[dict]) -> list[str]:
    wanted = name.strip().lower()
    names: list[str] = []
    known = {str(controller["id"]): str(controller["name"]) for controller in controllers}
    by_name = {str(controller["name"]).strip().lower(): str(controller["name"]) for controller in controllers}
    for fixture in fixtures:
        if str(fixture.get("name") or "").strip().lower() != wanted:
            continue
        for run in fixture.get("runs") or []:
            if not isinstance(run, dict):
                continue
            ident = str(run.get("controllerId") or "")
            label = known.get(ident) or by_name.get(ident.strip().lower())
            if label and label not in names:
                names.append(label)
    return names


def _controller_nodes(root: ET.Element) -> list[ET.Element]:
    found = [node for node in root.iter() if _local(node.tag) == "controller"]
    if found:
        return found
    return [
        node
        for node in root.iter()
        if _local(node.tag) == "network" and (node.attrib.get("NetworkType") or node.attrib.get("Description"))
    ]


def _summarise_controller(node: ET.Element) -> dict:
    if _local(node.tag) == "network":
        channels = _int_attr(node, "MaxChannels", 0)
        return {
            "name": (node.attrib.get("Description") or node.attrib.get("Name") or "").strip(),
            "ip": (node.attrib.get("ComPort") or node.attrib.get("IP") or "").strip(),
            "protocol": (node.attrib.get("NetworkType") or "").strip(),
            "channels": channels,
        }
    children = [child for child in list(node) if _local(child.tag) == "network"]
    channels = sum(_int_attr(child, "MaxChannels", 0) for child in children)
    if not children and node.attrib.get("Channels"):
        channels = _int_attr(node, "Channels", 0)
    ip = (node.attrib.get("IP") or "").strip()
    if not ip and children:
        ip = (children[0].attrib.get("ComPort") or "").strip()
    protocol = (node.attrib.get("Protocol") or "").strip()
    if not protocol and children:
        protocol = (children[0].attrib.get("NetworkType") or "").strip()
    return {
        "name": (node.attrib.get("Name") or node.attrib.get("Description") or "").strip(),
        "ip": ip,
        "protocol": protocol,
        "channels": channels,
    }


def _set_channels(node: ET.Element, channels: int) -> None:
    if _local(node.tag) == "network":
        node.attrib["MaxChannels"] = str(channels)
        return
    children = [child for child in list(node) if _local(child.tag) == "network"]
    if children:
        children[0].attrib["MaxChannels"] = str(channels)
        for extra in children[1:]:
            extra.attrib["MaxChannels"] = "0"
    if "Channels" in node.attrib:
        node.attrib["Channels"] = str(channels)


def _set_ip(node: ET.Element, ip: str) -> None:
    if _local(node.tag) == "network":
        node.attrib["ComPort"] = ip
        return
    node.attrib["IP"] = ip
    for child in list(node):
        if _local(child.tag) == "network":
            child.attrib["ComPort"] = ip


def _new_controller(item: dict, ident: int) -> ET.Element:
    node = ET.Element(
        "Controller",
        {
            "Id": str(ident),
            "Name": str(item["name"]),
            "Description": "",
            "Type": "Ethernet",
            "Vendor": "WLED",
            "Model": "WLED",
            "Variant": "Generic ESP32",
            "AutoSize": "0",
            "FromBase": "0",
            "ActiveState": "Active",
            "AutoLayout": "1",
            "AutoUpload": "0",
            "SuppressDuplicates": "0",
            "Monitor": "1",
            "IP": str(item["ip"]),
            "Protocol": "DDP",
            "FPPProxy": "",
            "Priority": "100",
            "Version": "1",
            "Expanded": "FALSE",
            "UPS": "FALSE",
            "ForceLocalIP": "",
        },
    )
    ET.SubElement(
        node,
        "network",
        {
            "ChannelsPerPacket": "1440",
            "KeepChannelNumbers": "0",
            "ComPort": str(item["ip"]),
            "BaudRate": "1",
            "NetworkType": "DDP",
            "MaxChannels": str(int(item["channels"])),
            "Enabled": "Yes",
        },
    )
    return node


def _next_id(root: ET.Element) -> int:
    highest = 0
    for node in _controller_nodes(root):
        try:
            highest = max(highest, int(float(node.attrib.get("Id") or "0")))
        except ValueError:
            continue
    return highest + 1


def _model_nodes(root: ET.Element) -> list[ET.Element]:
    found = []
    for parent in root.iter():
        if _local(parent.tag) != "models":
            continue
        for child in list(parent):
            if _local(child.tag) == "model":
                found.append(child)
    return found


def _controller_of(node: ET.Element) -> str | None:
    raw = node.attrib.get("Controller")
    if raw is None:
        raw = node.attrib.get("controller")
    if raw is None:
        return None
    text = str(raw).strip()
    if text.lower() in UNASSIGNED:
        return None
    return text


def _model_pixels(node: ET.Element) -> int:
    for key in ("NodesPerString", "PixelCount", "parm2"):
        if node.attrib.get(key) not in (None, ""):
            return max(0, _int_attr(node, key, 0))
    custom = node.attrib.get("CustomModel") or ""
    if custom.strip():
        return sum(1 for token in custom.replace("|", ";").replace(",", ";").split(";") if token.strip().isdigit() and int(token) > 0)
    return 0


def _needs_write(diff: dict) -> bool:
    controllers = diff["controllers"]
    return bool(controllers["placeholders"] or controllers["ipChanges"] or controllers["missing"] or diff["models"]["assign"])


def _backup(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = path.with_name(f"{path.name}.{stamp}.bak")
    counter = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.{stamp}.{counter}.bak")
        counter += 1
    dest.write_bytes(path.read_bytes())
    return dest


def _parse(text: str) -> ET.Element:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("That xLights file is not usable.")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError("That xLights file could not be read.") from exc


def _tostring(root: ET.Element) -> str:
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def _local(tag: str) -> str:
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    return tag.lower()


def _int_attr(node: ET.Element, name: str, default: int) -> int:
    raw = node.attrib.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default
