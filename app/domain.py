"""Lumen domain rules shared by the server and the tests.

Nothing here opens a socket. Segment writes, Look resolution, preview
coalescing, and the migration plan are pure so they can be checked without a
controller.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# WLED's JSON `tt` is in units of 100 ms.
COMMIT_MS = 400
PREVIEW_MS = 0
PREVIEW_INTERVAL_S = 0.15
PREVIEW_INTERVAL_MANY_S = 0.40
MANY_LIGHTS = 8
SCRUB_PAUSE_S = 2.0
VISIBLE_FPS = 10
HIDDEN_FPS = 1
PHONE_TICK_S = 0.1
MAX_WIRE_LEDS = 120
STALE_FRAME_S = 10 * 60


def transition_units(milliseconds: int) -> int:
    """Convert a duration into WLED's 100 ms transition units."""
    if milliseconds <= 0:
        return 0
    units = int(round(milliseconds / 100))
    return max(0, min(255, units))


@dataclass(frozen=True)
class BoardProfile:
    esp8266: bool
    live_fps: int
    max_sockets: int
    write_gap: float


def board_profile(arch: str | None) -> BoardProfile:
    """ESP8266 keeps a single socket, a slower peek, and batched writes."""
    if arch and "8266" in arch.lower():
        return BoardProfile(esp8266=True, live_fps=5, max_sockets=2, write_gap=0.2)
    return BoardProfile(esp8266=False, live_fps=10, max_sockets=4, write_gap=0.0)


def parse_live_binary(data: bytes) -> list[list[int]] | None:
    """Decode a WLED peek frame.

    Version 1 is a strip: ``L``, ``1``, then RGB. Version 2 is a matrix:
    ``L``, ``2``, width, height, then RGB. White is already folded in.
    """
    if len(data) < 2 or data[0] != ord("L"):
        return None
    version = data[1]
    if version == 1:
        offset = 2
    elif version == 2 and len(data) >= 4:
        offset = 4
    else:
        return None
    body = data[offset:]
    count = len(body) // 3
    return [[body[index], body[index + 1], body[index + 2]] for index in range(0, count * 3, 3)]


def _linear(channel: int) -> float:
    return (max(0, min(255, channel)) / 255) ** 2.2


def _encoded(value: float) -> int:
    return max(0, min(255, int(round((max(0.0, value) ** (1 / 2.2)) * 255))))


def downsample_linear(colors: list[list[int]], width: int) -> list[list[int]]:
    """Average buckets in linear light so dim pixels survive a long strip."""
    if width <= 0 or not colors:
        return []
    if len(colors) <= width:
        return [list(color[:3]) for color in colors]
    step = len(colors) / width
    sampled: list[list[int]] = []
    for index in range(width):
        start = int(index * step)
        stop = max(start + 1, int((index + 1) * step))
        total = [0.0, 0.0, 0.0]
        count = stop - start
        for color in colors[start:stop]:
            for channel in range(3):
                total[channel] += _linear(int(color[channel]))
        sampled.append([_encoded(total[channel] / count) for channel in range(3)])
    return sampled


def slice_frame(frame: list[list[int]], start: int, stop: int, strip_len: int) -> list[list[int]]:
    """Cut one output out of a board frame, stretching if the board downsampled."""
    if stop <= start or not frame:
        return []
    length = max(strip_len, stop, 1)
    if len(frame) == length:
        return [list(color[:3]) for color in frame[start:stop]]
    span = stop - start
    chosen: list[list[int]] = []
    for offset in range(span):
        source = int((start + offset) / length * len(frame))
        source = min(max(source, 0), len(frame) - 1)
        chosen.append(list(frame[source][:3]))
    return chosen


def resolve_name(name: str, fallback: str, catalog: list[str]) -> tuple[int, str, bool]:
    """Resolve an effect or palette by name. Returns index, name, used_fallback."""

    def find(wanted: str) -> tuple[int, str] | None:
        needle = wanted.strip().lower()
        if not needle:
            return None
        for index, item in enumerate(catalog):
            if item.strip().lower() == needle:
                return index, item
        return None

    direct = find(name)
    if direct is not None:
        return direct[0], direct[1], False
    substituted = find(fallback)
    if substituted is not None:
        return substituted[0], substituted[1], True
    solid = find("Solid")
    if solid is not None:
        return solid[0], solid[1], True
    if catalog:
        return 0, catalog[0], True
    return 0, name or "Solid", True


def resolve_look(look: dict, effects: list[str], palettes: list[str]) -> dict:
    effect_index, effect_name, effect_fallback = resolve_name(
        str(look.get("effect") or "Solid"),
        str(look.get("fallback") or "Solid"),
        effects,
    )
    palette_index, palette_name, palette_fallback = resolve_name(
        str(look.get("palette") or "Default"),
        "Default",
        palettes,
    )
    colors = look.get("colors") if isinstance(look.get("colors"), list) else [[255, 214, 170]]
    cleaned: list[list[int]] = []
    for color in colors[:3]:
        if isinstance(color, list) and len(color) >= 3:
            cleaned.append([max(0, min(255, int(color[0]))), max(0, min(255, int(color[1]))), max(0, min(255, int(color[2])))])
    while len(cleaned) < 3:
        cleaned.append([0, 0, 0])
    return {
        "fx": effect_index,
        "effect": effect_name,
        "pal": palette_index,
        "palette": palette_name,
        "fallback": effect_fallback or palette_fallback,
        "colors": cleaned,
        "sx": max(0, min(255, int(look.get("sx") or 0))),
        "ix": max(0, min(255, int(look.get("ix") or 0))),
    }


@dataclass(frozen=True)
class LightRef:
    id: int
    mac: str
    segment_id: int
    start: int
    stop: int
    on: bool = True
    bri: int = 255


def _segment(light: LightRef, *, bounds: bool) -> dict:
    segment = {"id": light.segment_id}
    if bounds:
        segment["start"] = light.start
        segment["stop"] = max(light.start + 1, light.stop)
    return segment


def build_targeted_state(
    board_lights: list[LightRef],
    selected_ids: set[int],
    *,
    on: bool | None = None,
    bri: int | None = None,
    col: list[list[int]] | None = None,
    fx: int | None = None,
    pal: int | None = None,
    sx: int | None = None,
    ix: int | None = None,
    tt_ms: int | None = None,
) -> dict:
    """Write segment fields for the selected lights of one board.

    Top-level ``on`` and ``bri`` are included only when every Light of the
    board is selected. A Dig-Uno output can then be off while its sibling runs.
    """
    if not board_lights:
        return {}
    selected = {light.id for light in board_lights if light.id in selected_ids}
    if not selected:
        return {}
    everyone = selected == {light.id for light in board_lights}
    bounds = len(board_lights) > 1
    segments: list[dict] = []
    for light in board_lights:
        if light.id not in selected:
            continue
        segment = _segment(light, bounds=bounds)
        if on is not None:
            segment["on"] = bool(on)
        if bri is not None and not everyone:
            segment["bri"] = max(0, min(255, int(bri)))
        if col is not None:
            segment["col"] = col
        if fx is not None:
            segment["fx"] = int(fx)
        if pal is not None:
            segment["pal"] = int(pal)
        if sx is not None:
            segment["sx"] = max(0, min(255, int(sx)))
        if ix is not None:
            segment["ix"] = max(0, min(255, int(ix)))
        segments.append(segment)
    payload: dict = {}
    if tt_ms is not None:
        payload["tt"] = transition_units(tt_ms)
    if segments:
        payload["seg"] = segments
    if everyone:
        if on is not None:
            payload["on"] = bool(on)
        if bri is not None:
            payload["bri"] = max(0, min(255, int(bri)))
    return payload


def build_span_state(
    light: LightRef,
    led_start: int,
    led_stop: int,
    others: list[LightRef],
    *,
    only: bool,
    color: str = "FFE7B0",
) -> dict:
    """Light a counted span on one output. ``only`` addresses every output."""
    start = max(0, min(led_start, led_stop))
    stop = max(start + 1, max(led_start, led_stop))
    span = max(0, light.stop - light.start)
    start = min(start, max(span - 1, 0))
    stop = min(max(stop, start + 1), max(span, 1))
    segment = {
        "id": light.segment_id,
        "start": light.start,
        "stop": max(light.start + 1, light.stop),
        "on": True,
        "bri": 255,
        "fx": 0,
        "sx": 0,
        "ix": 0,
        "frz": True,
        "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
        # WLED's "i" indexes the segment's own buffer, so it counts from the segment start.
        "i": [start, stop, color],
    }
    payload: dict = {"tt": 0, "seg": [segment]}
    if only:
        for other in others:
            payload["seg"].append(
                {
                    "id": other.segment_id,
                    "start": other.start,
                    "stop": max(other.start + 1, other.stop),
                    "on": False,
                }
            )
        payload["on"] = True
        payload["bri"] = 255
    return payload


def effective_brightness(master: int, segment_bri: int | None) -> int:
    segment = 255 if segment_bri is None else max(0, min(255, int(segment_bri)))
    return max(0, min(255, int(round(max(0, min(255, master)) * segment / 255))))


class WriteSequencer:
    """One board's preview/commit stream.

    Only the newest preview is allowed in flight. A commit drops queued
    previews, and a result applies only when it is still the latest request,
    so a late preview cannot overwrite a commit.
    """

    def __init__(self) -> None:
        self.latest = 0
        self.commit_seq = 0
        self._inflight: int | None = None
        self._queued: tuple[int, str, dict] | None = None

    def submit(self, kind: str, body: dict) -> dict:
        if kind not in {"preview", "commit"}:
            raise ValueError(f"unknown write kind {kind}")
        self.latest += 1
        seq = self.latest
        dropped: list[int] = []
        if kind == "commit":
            self.commit_seq = seq
            if self._queued is not None and self._queued[1] == "preview":
                dropped.append(self._queued[0])
            self._queued = None
            self._inflight = seq
            return {"seq": seq, "send": body, "dropped": dropped}
        if self._inflight is not None:
            if self._queued is not None:
                dropped.append(self._queued[0])
            self._queued = (seq, kind, body)
            return {"seq": seq, "send": None, "dropped": dropped}
        self._inflight = seq
        return {"seq": seq, "send": body, "dropped": dropped}

    def finished(self, seq: int) -> dict | None:
        if self._inflight == seq:
            self._inflight = None
        queued = self._queued
        self._queued = None
        if queued is None:
            return None
        qseq, kind, body = queued
        if kind == "preview" and qseq < self.commit_seq:
            return {"seq": qseq, "send": None, "dropped": [qseq]}
        self._inflight = qseq
        return {"seq": qseq, "send": body, "dropped": []}

    def accepts(self, seq: int) -> bool:
        return seq == self.latest


class BatchWriter:
    """Hold writes so an ESP8266 sees at most one every `gap` seconds.

    Newer payloads replace anything still waiting. The clock is passed in.
    """

    def __init__(self, gap: float):
        self.gap = gap
        self.pending: tuple[int, dict] | None = None
        self.last_sent = -1e9

    def push(self, now: float, seq: int, body: dict) -> list[tuple[int, dict]]:
        self.pending = (seq, body)
        return self.flush(now)

    def flush(self, now: float) -> list[tuple[int, dict]]:
        if self.pending is None:
            return []
        if now - self.last_sent < self.gap:
            return []
        item = self.pending
        self.pending = None
        self.last_sent = now
        return [item]


def should_send(visible: bool, tick: int, last_tick: int, source_fps: int) -> bool:
    """Phone fan-out: ~10 fps for a visible ribbon, 1 fps otherwise.

    `tick` advances at 10 Hz. A 5 fps board never emits faster than its source.
    """
    interval = 1 if visible else max(1, int(round(VISIBLE_FPS / HIDDEN_FPS)))
    if source_fps <= 5:
        interval = max(interval, int(round(VISIBLE_FPS / max(source_fps, 1))))
    if last_tick < 0:
        return True
    return tick - last_tick >= interval


@dataclass
class PlannedZone:
    key: str
    name: str
    position: int
    source_group_id: int | None


@dataclass
class PlannedLight:
    mac: str
    output_index: int
    segment_id: int
    name: str
    start: int
    stop: int
    named: bool
    zone_key: str | None
    position: int


@dataclass
class MigrationPlan:
    zones: list[PlannedZone] = field(default_factory=list)
    lights: list[PlannedLight] = field(default_factory=list)


def _buses_or_whole(buses: list[dict] | None, led_count: int | None) -> list[dict]:
    usable = []
    for bus in buses or []:
        length = bus.get("len")
        if isinstance(length, int) and length > 0:
            start = bus.get("start") if isinstance(bus.get("start"), int) else 0
            usable.append({"index": len(usable), "start": start, "len": length})
    if usable:
        return usable
    length = led_count if isinstance(led_count, int) and led_count > 0 else 1
    return [{"index": 0, "start": 0, "len": length}]


def migration_plan(devices: list[dict], groups: list[dict], buses_by_mac: dict[str, list[dict]]) -> MigrationPlan:
    """Turn existing devices into Lights and existing groups into Zones.

    Forgotten devices are skipped. A device in several groups keeps the
    earliest group. Device rows and backups are not part of the plan.
    """
    ordered = sorted(groups, key=lambda group: (int(group.get("position") or 0), int(group.get("id") or 0)))
    zones: list[PlannedZone] = []
    mac_zone: dict[str, str] = {}
    for index, group in enumerate(ordered):
        key = f"group-{group.get('id')}"
        zones.append(
            PlannedZone(
                key=key,
                name=str(group.get("name") or "Zone"),
                position=index,
                source_group_id=int(group["id"]) if group.get("id") is not None else None,
            )
        )
        for mac in group.get("macs") or []:
            mac_zone.setdefault(str(mac), key)
    lights: list[PlannedLight] = []
    positions: dict[str | None, int] = {}
    active = [device for device in devices if not device.get("forgotten")]
    active.sort(key=lambda device: str(device.get("name") or "").lower())
    for device in active:
        mac = str(device["mac"])
        buses = _buses_or_whole(buses_by_mac.get(mac), device.get("led_count"))
        zone_key = mac_zone.get(mac)
        for bus in buses:
            slot = positions.get(zone_key, 0)
            positions[zone_key] = slot + 1
            name = str(device.get("name") or "Light")
            if len(buses) > 1:
                name = f"{name} {bus['index'] + 1}"
            lights.append(
                PlannedLight(
                    mac=mac,
                    output_index=int(bus["index"]),
                    segment_id=int(bus["index"]),
                    name=name[:32],
                    start=int(bus["start"]),
                    stop=int(bus["start"]) + int(bus["len"]),
                    named=True,
                    zone_key=zone_key,
                    position=slot,
                )
            )
    return MigrationPlan(zones=zones, lights=lights)


def sync_plan(existing: list[dict], buses: list[dict], device_name: str, led_count: int | None) -> dict:
    """Update Lights from a board's outputs without dropping a named Light.

    An output split by the segment finder holds several Lights. They are kept,
    moved with the output if it moved, and clamped to its new end. If the
    output shrank past a cut, the split collapses back into its first Light.
    """
    usable = _buses_or_whole(buses, led_count)
    by_output: dict[int, list[dict]] = {}
    for light in existing:
        by_output.setdefault(int(light["output_index"]), []).append(light)
    upserts: list[dict] = []
    delete_ids: list[int] = []
    for bus in usable:
        index = int(bus["index"])
        start = int(bus["start"])
        stop = start + int(bus["len"])
        pieces = sorted(by_output.get(index, []), key=lambda light: (int(light["start"]), int(light["id"])))
        if len(pieces) > 1:
            shift = start - int(pieces[0]["start"])
            bounds = [int(piece["start"]) + shift for piece in pieces[1:]]
            if all(start < cut < stop for cut in bounds):
                edges = [start, *bounds, stop]
                for piece, low, high in zip(pieces, edges, edges[1:]):
                    upserts.append(_keep(piece, index, int(piece["segment_id"]), low, high))
                continue
            delete_ids.extend(int(piece["id"]) for piece in pieces[1:])
            pieces = pieces[:1]
        if pieces:
            upserts.append(_keep(pieces[0], index, index if len(by_output.get(index, [])) <= 1 else int(pieces[0]["segment_id"]), start, stop))
            continue
        label = device_name if len(usable) == 1 else f"{device_name} {bus['index'] + 1}"
        upserts.append(
            {
                "id": None,
                "output_index": index,
                "segment_id": index,
                "start": start,
                "stop": stop,
                "name": label[:32],
                "named": False,
                "zone_id": existing[0].get("zone_id") if existing else None,
                "position": index,
            }
        )
    keep = {int(bus["index"]) for bus in usable}
    delete_ids.extend(
        int(light["id"])
        for light in existing
        if int(light["output_index"]) not in keep and not light.get("named")
    )
    return {"upserts": upserts, "delete_ids": delete_ids}


def _keep(light: dict, output_index: int, segment_id: int, start: int, stop: int) -> dict:
    return {
        "id": light["id"],
        "output_index": output_index,
        "segment_id": segment_id,
        "start": start,
        "stop": stop,
        "name": light["name"],
        "named": bool(light["named"]),
        "zone_id": light.get("zone_id"),
        "position": light.get("position") or 0,
    }


# ----- show outputs ---------------------------------------------------------------

# Colors an LED renders clearly and that stay apart from each other. Past the
# list, the same colors come back dashed, then dotted, so every output differs.
OUTPUT_COLORS = [
    ("Red", "FF0000"),
    ("Orange", "FF6A00"),
    ("Yellow", "FFE000"),
    ("Lime", "8CFF00"),
    ("Green", "00FF30"),
    ("Teal", "00FFC0"),
    ("Cyan", "00B4FF"),
    ("Blue", "0028FF"),
    ("Purple", "8C00FF"),
    ("Magenta", "FF00E0"),
    ("Pink", "FF5A8C"),
    ("White", "FFFFFF"),
]
OUTPUT_PATTERNS = [("solid", 1, 0), ("dashed", 3, 2), ("dotted", 1, 2)]


def output_marks(count: int) -> list[dict]:
    """Color and pattern for each of `count` outputs, in order; neighbours differ most."""
    marks = []
    colors = len(OUTPUT_COLORS)
    # Step through the colors by a stride that is coprime with their count, so
    # consecutive outputs (often on the same board) land far apart on the wheel.
    stride = 5
    for index in range(count):
        name, hexa = OUTPUT_COLORS[(index * stride) % colors]
        pattern, grp, spc = OUTPUT_PATTERNS[min(index // colors, len(OUTPUT_PATTERNS) - 1)]
        marks.append({"color": hexa, "name": name, "pattern": pattern, "grp": grp, "spc": spc})
    return marks


def build_outputs_state(segments: list[dict]) -> dict:
    """Light each segment solid in its mark. `segments`: id, start, stop, mark."""
    painted = []
    for segment in segments:
        mark = segment["mark"]
        hexa = mark["color"]
        rgb = [int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16)]
        painted.append(
            {
                "id": int(segment["id"]),
                "start": int(segment["start"]),
                "stop": int(segment["stop"]),
                "on": True,
                "bri": 255,
                "fx": 0,
                "frz": False,
                "grp": mark["grp"],
                "spc": mark["spc"],
                "col": [rgb, [0, 0, 0], [0, 0, 0]],
            }
        )
    return {"tt": 0, "on": True, "bri": 255, "seg": painted}


# ----- segment finder ----------------------------------------------------------

# Distinct, easy-to-tell-apart colors for the pieces of a split output.
PIECE_COLORS = ["FF3B30", "34C759", "0A84FF", "FFD60A", "BF5AF2", "64D2FF", "FF9F0A", "FFFFFF"]
MARKER_COLOR = "FFFFFF"


@dataclass(frozen=True)
class Piece:
    start: int
    stop: int
    segment_id: int
    light_id: int | None


def clean_cuts(cuts: list[int], start: int, stop: int) -> list[int]:
    """Cuts are absolute LED indexes where a new piece begins. Each piece keeps at least one LED."""
    return sorted({int(cut) for cut in cuts if start < int(cut) < stop})


def plan_split(
    pieces: list[dict],
    start: int,
    stop: int,
    cuts: list[int],
    taken_ids: set[int],
    max_segments: int,
) -> tuple[list[Piece], list[dict]]:
    """Map cut points onto Lights and WLED segment ids.

    Existing Lights of the output are reused in order, so the first piece keeps
    the output's name, zone and segment. New pieces take the lowest segment ids
    not used anywhere on the board. Returns the pieces and the Lights left over.
    """
    edges = [start, *clean_cuts(cuts, start, stop), stop]
    ordered = sorted(pieces, key=lambda light: (int(light["start"]), int(light["id"])))
    free = (candidate for candidate in range(max(1, max_segments)) if candidate not in taken_ids)
    planned: list[Piece] = []
    for index, (low, high) in enumerate(zip(edges, edges[1:])):
        if index < len(ordered):
            reused = ordered[index]
            planned.append(Piece(low, high, int(reused["segment_id"]), int(reused["id"])))
            continue
        segment_id = next(free, None)
        if segment_id is None:
            raise ValueError(f"This board allows {max_segments} segments. Use fewer cuts.")
        planned.append(Piece(low, high, segment_id, None))
    return planned, ordered[len(planned):]


def build_split_state(pieces: list[Piece], removed_segment_ids: list[int], template: dict | None = None) -> dict:
    """Segments for the pieces, with deletions. New pieces copy the look of the output's first segment."""
    look = {key: template[key] for key in ("on", "bri", "col", "fx", "pal", "sx", "ix") if template and key in template}
    segments: list[dict] = []
    for piece in pieces:
        segment = {"id": piece.segment_id, "start": piece.start, "stop": max(piece.start + 1, piece.stop), "frz": False}
        if piece.light_id is None:
            segment.update(look)
            segment.setdefault("on", True)
        segments.append(segment)
    for segment_id in removed_segment_ids:
        # WLED deletes a segment whose stop is not after its start; 0 is the documented way.
        segments.append({"id": segment_id, "stop": 0})
    return {"tt": 0, "seg": segments}


def build_marks_state(
    segments: list[dict],
    marks: list[tuple[int, int, str]],
    *,
    base: str = "000000",
) -> dict:
    """Paint absolute LED ranges onto whatever segments currently cover them.

    Used by the ruler and the segment finder, which preview pieces that do not
    exist on the board yet. Each segment is frozen at its own bounds and painted
    with segment-relative "i" ranges, so the board's layout is never changed.
    """
    painted: list[dict] = []
    for segment in segments:
        low = int(segment["start"])
        high = int(segment["stop"])
        if high <= low:
            continue
        ranges: list[object] = [0, high - low, base]
        for start, stop, color in marks:
            overlap_low = max(low, int(start))
            overlap_high = min(high, int(stop))
            if overlap_high > overlap_low:
                ranges.extend([overlap_low - low, overlap_high - low, color])
        painted.append(
            {
                "id": int(segment["id"]),
                "start": low,
                "stop": high,
                "on": True,
                "bri": 255,
                "fx": 0,
                "frz": True,
                "col": [[0, 0, 0], [0, 0, 0], [0, 0, 0]],
                "i": ranges,
            }
        )
    return {"tt": 0, "on": True, "bri": 255, "seg": painted}


def finder_marks(start: int, stop: int, cuts: list[int], marker: int | None) -> list[tuple[int, int, str]]:
    """Each proposed piece dimly in its own color, cut points and the marker bright."""
    edges = [start, *clean_cuts(cuts, start, stop), stop]
    marks: list[tuple[int, int, str]] = []
    for index, (low, high) in enumerate(zip(edges, edges[1:])):
        marks.append((low, high, _dim(PIECE_COLORS[index % len(PIECE_COLORS)], 0.28)))
        marks.append((low, low + 1, PIECE_COLORS[index % len(PIECE_COLORS)]))
    if marker is not None and start <= marker < stop:
        marks.append((marker, marker + 1, MARKER_COLOR))
    return marks


def _dim(color: str, amount: float) -> str:
    channels = [int(color[i : i + 2], 16) for i in (0, 2, 4)]
    return "".join(f"{max(0, min(255, round(channel * amount))):02X}" for channel in channels)


def heal_state(lights: list[dict], state: dict) -> dict | None:
    """Segments the server holds for a split board that the board no longer has (after a reboot)."""
    by_output: dict[int, int] = {}
    for light in lights:
        by_output[int(light["output_index"])] = by_output.get(int(light["output_index"]), 0) + 1
    if not any(count > 1 for count in by_output.values()):
        return None
    present = {}
    for segment in state.get("seg") or []:
        if isinstance(segment, dict) and isinstance(segment.get("id"), int):
            present[segment["id"]] = (segment.get("start"), segment.get("stop"))
    wanted = []
    for light in lights:
        bounds = (int(light["start"]), int(light["stop"]))
        if present.get(int(light["segment_id"])) != bounds:
            wanted.append({"id": int(light["segment_id"]), "start": bounds[0], "stop": bounds[1], "on": True})
    if not wanted:
        return None
    return {"tt": 0, "seg": wanted}


def preview_interval(light_count: int) -> float:
    if light_count > MANY_LIGHTS:
        return PREVIEW_INTERVAL_MANY_S
    return PREVIEW_INTERVAL_S
