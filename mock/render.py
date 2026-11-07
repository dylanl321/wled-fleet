"""Approximate WLED effects for the mock controller.

Effects are matched by name family so a Look that names "Candle" or "Aurora"
moves the way the real effect roughly does. Exact pixels do not matter; motion,
palette, speed, and brightness do.
"""

from __future__ import annotations

import colorsys
import math

EFFECTS = [
    "Solid", "Blink", "Breathe", "Wipe", "Wipe Random", "Random Colors", "Sweep", "Dynamic",
    "Colorloop", "Rainbow", "Scan", "Scan Dual", "Fade", "Theater", "Theater Rainbow", "Running",
    "Saw", "Twinkle", "Dissolve", "Dissolve Rnd", "Sparkle", "Sparkle Dark", "Sparkle+", "Strobe",
    "Strobe Rainbow", "Strobe Mega", "Blink Rainbow", "Android", "Chase", "Chase Random",
    "Chase Rainbow", "Chase Flash", "Chase Flash Rnd", "Rainbow Runner", "Colorful", "Traffic Light",
    "Sweep Random", "Chase 2", "Aurora", "Stream", "Scanner", "Lighthouse", "Fireworks", "Rain",
    "Tetrix", "Fire Flicker", "Gradient", "Loading", "Rolling Balls", "Fairy", "Two Dots",
    "Fairytwinkle", "Running Dual", "Image", "Tri Chase", "Tri Wipe", "Tri Fade", "Lightning", "ICU",
    "Multi Comet", "Scanner Dual", "Stream 2", "Oscillate", "Pride 2015", "Juggle", "Palette",
    "Fire 2012", "Colorwaves", "Bpm", "Fill Noise", "Noise 1", "Noise 2", "Noise 3", "Noise 4",
    "Colortwinkles", "Lake", "Meteor", "Meteor Smooth", "Railway", "Ripple", "Twinklefox",
    "Twinklecat", "Halloween Eyes", "Solid Pattern", "Solid Pattern Tri", "Spots", "Spots Fade",
    "Glitter", "Candle", "Fireworks Starburst", "Fireworks 1D", "Bouncing Balls", "Sinelon",
    "Sinelon Dual", "Sinelon Rainbow", "Popcorn", "Drip", "Plasma", "Percent", "Ripple Rainbow",
    "Heartbeat", "Pacifica", "Candle Multi", "Solid Glitter", "Sunrise", "Phased", "Twinkleup",
    "Noise Pal", "Sine", "Phased Noise", "Flow", "Chunchun", "Dancing Shadows", "Washing Machine",
]

PALETTES = [
    "Default", "* Random Cycle", "* Color 1", "* Colors 1&2", "* Color Gradient", "* Colors Only",
    "Party", "Cloud", "Lava", "Ocean", "Forest", "Rainbow", "Rainbow Bands", "Sunset", "Rivendell",
    "Breeze", "Red & Blue", "Yellowout", "Analogous", "Splash", "Pastel", "Sunset 2", "Beach",
    "Vintage", "Departure", "Landscape", "Beech", "Sherbet", "Hult", "Hult 64", "Drywet", "Jul",
    "Grintage", "Rewhi", "Tertiary", "Fire", "Icefire", "Cyane", "Light Pink", "Autumn", "Magenta",
    "Magred", "Yelmag", "Yelblu", "Orange & Teal", "Tiamat", "April Night", "Orangery", "C9",
    "Sakura", "Aurora", "Atlantica", "C9 2", "C9 New", "Temperature", "Aurora 2", "Retro Clown",
    "Candy", "Toxy Reaf", "Fairy Reaf", "Semi Blue", "Pink Candy", "Red Reaf", "Aqua Flash",
    "Yelblu Hot", "Lite Light", "Red Flash", "Blink Red", "Red Shift", "Red Tide", "Candy2",
]

# A few palettes as color stops. Others fall back to the segment's own colors.
STOPS: dict[str, list[list[int]]] = {
    "Party": [[85, 0, 171], [132, 0, 124], [181, 0, 75], [229, 0, 27], [232, 23, 0], [184, 71, 0], [171, 85, 0], [255, 0, 80]],
    "Cloud": [[0, 0, 255], [0, 0, 139], [135, 206, 235], [255, 255, 255], [173, 216, 230]],
    "Lava": [[0, 0, 0], [128, 0, 0], [255, 0, 0], [255, 165, 0], [255, 255, 255], [255, 165, 0], [255, 0, 0], [128, 0, 0]],
    "Ocean": [[25, 25, 112], [0, 0, 139], [0, 0, 128], [0, 0, 205], [46, 139, 87], [0, 128, 128], [95, 158, 160], [0, 0, 255], [0, 139, 139], [100, 149, 237], [127, 255, 212], [135, 206, 235]],
    "Forest": [[0, 100, 0], [0, 100, 0], [85, 107, 47], [0, 100, 0], [0, 128, 0], [34, 139, 34], [107, 142, 35], [0, 128, 0], [46, 139, 87], [102, 205, 170], [50, 205, 50], [154, 205, 50]],
    "Rainbow": [[255, 0, 0], [171, 85, 0], [171, 171, 0], [0, 255, 0], [0, 171, 85], [0, 0, 255], [85, 0, 171], [171, 0, 85]],
    "Sunset": [[120, 0, 0], [179, 22, 0], [255, 104, 0], [167, 22, 18], [100, 0, 103], [16, 0, 130], [0, 0, 160]],
    "Fire": [[0, 0, 0], [18, 0, 0], [113, 0, 0], [142, 3, 1], [175, 17, 1], [213, 44, 2], [255, 82, 4], [255, 156, 21], [255, 255, 255]],
    "Icefire": [[0, 0, 0], [0, 51, 255], [255, 255, 255], [255, 255, 255], [255, 51, 0], [0, 0, 0]],
    "Aurora": [[1, 5, 45], [0, 200, 23], [0, 255, 0], [0, 243, 45], [0, 135, 7], [1, 5, 45]],
    "Aurora 2": [[17, 177, 13], [121, 242, 5], [25, 173, 121], [250, 77, 127], [171, 101, 221]],
    "Pastel": [[61, 135, 184], [129, 188, 169], [255, 237, 167], [255, 198, 144], [255, 123, 98]],
    "Beach": [[255, 252, 214], [255, 252, 214], [255, 252, 214], [190, 191, 115], [137, 141, 52], [112, 255, 205], [51, 246, 214], [17, 235, 226], [2, 193, 199]],
    "Autumn": [[26, 1, 1], [67, 4, 1], [118, 14, 1], [137, 152, 52], [113, 65, 1], [133, 149, 59], [137, 152, 52], [113, 65, 1], [139, 154, 46], [113, 13, 1], [55, 3, 1], [17, 1, 1]],
    "Orange & Teal": [[0, 150, 92], [0, 150, 92], [255, 72, 0], [255, 72, 0]],
    "C9": [[184, 4, 0], [184, 4, 0], [144, 44, 2], [144, 44, 2], [4, 96, 2], [4, 96, 2], [7, 7, 88], [7, 7, 88]],
    "Sakura": [[196, 19, 10], [255, 69, 45], [223, 45, 72], [255, 82, 103], [223, 13, 17]],
    "Candy": [[229, 227, 1], [227, 101, 3], [40, 1, 80], [1, 1, 0], [255, 255, 255]],
    "Magenta": [[0, 0, 0], [42, 0, 255], [255, 0, 255], [255, 55, 255], [255, 255, 255]],
    "Temperature": [[1, 27, 105], [1, 40, 127], [1, 70, 168], [1, 92, 197], [1, 119, 221], [3, 130, 151], [23, 156, 149], [67, 182, 112], [121, 201, 52], [142, 203, 11], [224, 223, 1], [252, 187, 2], [247, 147, 1], [237, 87, 1], [229, 43, 1], [171, 2, 2]],
    "Red & Blue": [[255, 0, 0], [255, 0, 0], [0, 0, 255], [0, 0, 255]],
    "Retro Clown": [[227, 101, 3], [117, 1, 46], [255, 0, 0], [80, 2, 80]],
}


def clamp(value: float) -> int:
    return max(0, min(255, int(value)))


def mix(left: list[int], right: list[int], amount: float) -> list[int]:
    amount = max(0.0, min(1.0, amount))
    return [clamp(left[i] + (right[i] - left[i]) * amount) for i in range(3)]


def scale(color: list[int], amount: float) -> list[int]:
    return [clamp(channel * amount) for channel in color]


def hue(value: float, sat: float = 1.0) -> list[int]:
    red, green, blue = colorsys.hsv_to_rgb(value % 1, sat, 1)
    return [int(red * 255), int(green * 255), int(blue * 255)]


def noise(index: int, slot: int, seed: int = 0) -> float:
    """Repeatable 0..1 noise for a pixel and a time slot."""
    value = (index * 374761393 + slot * 668265263 + seed * 2246822519) & 0xFFFFFFFF
    value = ((value ^ (value >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((value ^ (value >> 16)) & 0xFFFF) / 65535


def smooth_noise(position: float, seed: int = 0) -> float:
    base = math.floor(position)
    frac = position - base
    frac = frac * frac * (3 - 2 * frac)
    return noise(base, 0, seed) * (1 - frac) + noise(base + 1, 0, seed) * frac


def palette_at(palette: str, colors: list[list[int]], position: float) -> list[int]:
    stops = STOPS.get(palette)
    if palette == "* Color 1":
        return list(colors[0])
    if palette in {"* Colors 1&2", "* Color Gradient", "* Colors Only"} or not stops:
        if palette == "Default" or palette.startswith("*") or not stops:
            choices = [color for color in colors if any(color)] or [colors[0]]
            stops = choices if len(choices) > 1 else [choices[0], scale(choices[0], 0.35)]
    position %= 1.0
    scaled = position * len(stops)
    index = int(scaled) % len(stops)
    return mix(stops[index], stops[(index + 1) % len(stops)], scaled - int(scaled))


def family(name: str) -> str:
    lowered = name.lower()
    table = [
        ("solid", ("solid pattern",), "stripes"),
        ("candle", (), "candle"),
        ("fire flicker", (), "candle"),
        ("fire", (), "fire"),
        ("aurora", (), "aurora"),
        ("pacifica", (), "ocean"),
        ("lake", (), "ocean"),
        ("flow", (), "flow"),
        ("colorwaves", (), "flow"),
        ("noise", (), "flow"),
        ("plasma", (), "flow"),
        ("palette", (), "flow"),
        ("pride", (), "flow"),
        ("rainbow", (), "rainbow"),
        ("colorloop", (), "rainbow"),
        ("twinkle", (), "twinkle"),
        ("sparkle", (), "twinkle"),
        ("glitter", (), "twinkle"),
        ("fairy", (), "twinkle"),
        ("dissolve", (), "twinkle"),
        ("halloween", (), "eyes"),
        ("breathe", (), "breathe"),
        ("heartbeat", (), "breathe"),
        ("fade", (), "breathe"),
        ("sunrise", (), "breathe"),
        ("blink", (), "blink"),
        ("strobe", (), "blink"),
        ("lightning", (), "blink"),
        ("rain", (), "rain"),
        ("drip", (), "rain"),
        ("ripple", (), "rain"),
        ("popcorn", (), "rain"),
        ("firework", (), "rain"),
        ("chase", (), "chase"),
        ("theater", (), "chase"),
        ("running", (), "chase"),
        ("railway", (), "chase"),
        ("scan", (), "comet"),
        ("meteor", (), "comet"),
        ("comet", (), "comet"),
        ("sinelon", (), "comet"),
        ("lighthouse", (), "comet"),
        ("wipe", (), "wipe"),
        ("sweep", (), "wipe"),
        ("random colors", (), "random"),
        ("dynamic", (), "random"),
    ]
    for needle, exclusions, result in table:
        if needle in lowered and not any(item in lowered for item in exclusions):
            return "solid" if needle == "solid" else result
        if needle in lowered and exclusions:
            return result
    return "flow"


def render(effect: str, palette: str, colors: list[list[int]], speed: int, intensity: int, count: int, now: float) -> list[list[int]]:
    """Full-brightness pixels for one segment."""
    colors = [list(color[:3]) + [0] * (3 - len(color[:3])) for color in (colors or [[255, 160, 60]])]
    while len(colors) < 3:
        colors.append([0, 0, 0])
    primary, secondary = colors[0], colors[1]
    kind = family(effect)
    rate = 0.25 + speed / 110
    clock = now * rate
    width = max(1, count)
    pixels: list[list[int]] = []
    for index in range(width):
        phase = index / width
        if kind == "solid":
            rgb = primary
        elif kind == "stripes":
            rgb = primary if (index // max(1, intensity // 40 + 1)) % 2 == 0 else secondary
        elif kind == "candle":
            flicker = 0.55 + 0.45 * smooth_noise(now * (3 + speed / 40) + index * 0.6, index % 7)
            rgb = scale(primary, flicker)
        elif kind == "fire":
            heat = smooth_noise(now * (2 + speed / 50) + index * 0.35, 3) * (1 - phase * 0.5)
            rgb = palette_at(palette if palette in STOPS else "Fire", colors, heat * 0.85)
        elif kind == "aurora":
            wave = smooth_noise(index * 0.08 + clock * 0.4, 11) * 0.6 + 0.2 * math.sin(phase * 6 + clock)
            rgb = scale(palette_at(palette if palette in STOPS else "Aurora", colors, wave + clock * 0.05), 0.35 + 0.65 * smooth_noise(index * 0.15 - clock * 0.6, 5))
        elif kind == "ocean":
            wave = 0.5 + 0.5 * math.sin(phase * 9 + clock * 1.3) * math.sin(phase * 3.1 - clock * 0.7)
            rgb = palette_at(palette if palette in STOPS else "Ocean", colors, wave * 0.8 + clock * 0.03)
        elif kind == "flow":
            rgb = palette_at(palette, colors, phase + clock * 0.15 + 0.15 * math.sin(phase * 5 + clock))
        elif kind == "rainbow":
            rgb = hue(phase * (0.5 + intensity / 255) + clock * 0.15)
        elif kind == "twinkle":
            slot = int(now * (1 + speed / 40))
            level = noise(index, slot, 9)
            density = 0.75 + (255 - intensity) / 1300
            base = palette_at(palette, colors, noise(index, 0, 2)) if palette not in {"Default", "* Color 1"} else primary
            rgb = scale(base, max(0.0, (level - density) / (1 - density))) if level > density else scale(secondary, 0.4)
        elif kind == "eyes":
            slot = int(now * 0.8)
            start = int(noise(slot, 1, 4) * max(1, width - 4))
            rgb = primary if index in (start, start + 3) and noise(slot, 2, 4) > 0.3 else [0, 0, 0]
        elif kind == "breathe":
            rgb = scale(primary, 0.15 + 0.85 * (0.5 + 0.5 * math.sin(clock * 2.2)))
        elif kind == "blink":
            rgb = primary if int(clock * 3) % 2 == 0 else secondary
        elif kind == "rain":
            slot = int(clock * 6)
            drop = noise(index, slot // 3, 6) > 0.92
            rgb = palette_at(palette, colors, noise(index, slot, 1)) if drop else scale(secondary, 0.3)
        elif kind == "chase":
            rgb = primary if (index + int(clock * 6)) % 3 == 0 else secondary
        elif kind == "comet":
            head = (clock * 0.3) % 1
            tail = max(0.0, 1 - ((head - phase) % 1) * (6 + intensity / 32))
            rgb = mix(secondary, primary, tail)
        elif kind == "wipe":
            edge = (clock * 0.25) % 2
            rgb = primary if (phase < edge if edge <= 1 else phase >= edge - 1) else secondary
        elif kind == "random":
            slot = int(clock * 1.5)
            rgb = hue(noise(index // 3, slot, 8))
        else:
            rgb = primary
        pixels.append([clamp(channel) for channel in rgb])
    return pixels


def render_state(state: dict, total: int, now: float) -> list[list[int]]:
    """The whole strip, every segment, with master and segment brightness applied."""
    pixels = [[0, 0, 0] for _ in range(max(1, total))]
    master = int(state.get("bri") or 0) / 255 if state.get("on") else 0.0
    for segment in state.get("seg") or []:
        if not isinstance(segment, dict):
            continue
        start = max(0, int(segment.get("start") or 0))
        stop = min(total, int(segment.get("stop") if isinstance(segment.get("stop"), int) else total))
        if stop <= start:
            continue
        level = master * (int(segment.get("bri", 255)) / 255) if segment.get("on", True) else 0.0
        fx = segment.get("fx") if isinstance(segment.get("fx"), int) else 0
        pal = segment.get("pal") if isinstance(segment.get("pal"), int) else 0
        effect = EFFECTS[fx] if 0 <= fx < len(EFFECTS) else "Solid"
        palette = PALETTES[pal] if 0 <= pal < len(PALETTES) else "Default"
        colors = segment.get("col") if isinstance(segment.get("col"), list) else []
        clean = []
        for color in colors[:3]:
            if isinstance(color, list) and len(color) >= 3:
                clean.append([int(color[0]), int(color[1]), int(color[2])])
            elif isinstance(color, str) and len(color) >= 6:
                clean.append([int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)])
        drawn = render(effect, palette, clean or [[255, 160, 60]], int(segment.get("sx", 128)), int(segment.get("ix", 128)), stop - start, now)
        for offset, rgb in enumerate(drawn):
            pixels[start + offset] = scale(rgb, level)
        frozen = segment.get("i")
        if isinstance(frozen, list) and frozen:
            _paint_individual(pixels, start, stop, frozen, level)
    return pixels


def _paint_individual(pixels: list[list[int]], start: int, stop: int, items: list, level: float) -> None:
    """WLED's "i" field: [index, color] pairs or [from, to, color] ranges. Black elsewhere."""
    for index in range(start, stop):
        pixels[index] = [0, 0, 0]
    cursor = 0
    while cursor < len(items):
        first = items[cursor]
        if cursor + 2 < len(items) and isinstance(items[cursor + 1], int) and isinstance(items[cursor + 2], (str, list)):
            low, high, color = first, items[cursor + 1], items[cursor + 2]
            cursor += 3
        elif cursor + 1 < len(items) and isinstance(items[cursor + 1], (str, list)):
            low, high, color = first, first + 1, items[cursor + 1]
            cursor += 2
        else:
            cursor += 1
            continue
        rgb = _parse(color)
        for index in range(start + int(low), min(stop, start + int(high))):
            pixels[index] = scale(rgb, max(level, 0.6))


def _parse(color: object) -> list[int]:
    if isinstance(color, list) and len(color) >= 3:
        return [clamp(color[0]), clamp(color[1]), clamp(color[2])]
    if isinstance(color, str) and len(color) >= 6:
        return [int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)]
    return [255, 255, 255]
