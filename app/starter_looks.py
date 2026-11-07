"""The curated Looks a fresh install starts with.

Effects and palettes are stored by name. Each board resolves them against its own
/json/eff and /json/pal at apply time, and uses `fallback` when its firmware does
not have the effect.
"""

from __future__ import annotations

STARTER_LOOKS: list[dict] = [
    {"name": "Warm white", "fx": "Solid", "col": [[255, 172, 92], [0, 0, 0], [0, 0, 0]]},
    {"name": "Candle", "fx": "Candle", "fallback": "Fire Flicker", "col": [[255, 138, 36], [0, 0, 0], [0, 0, 0]], "sx": 96, "ix": 200},
    {"name": "Ember", "fx": "Fire 2012", "fallback": "Fire Flicker", "pal": "Lava", "col": [[255, 70, 0], [0, 0, 0], [0, 0, 0]], "sx": 80, "ix": 160},
    {"name": "Aurora", "fx": "Aurora", "fallback": "Colorwaves", "pal": "Aurora", "col": [[0, 200, 120], [0, 0, 0], [0, 0, 0]], "sx": 60, "ix": 128},
    {"name": "Ocean", "fx": "Pacifica", "fallback": "Lake", "pal": "Ocean", "col": [[0, 90, 200], [0, 0, 0], [0, 0, 0]], "sx": 100, "ix": 128},
    {"name": "Rain", "fx": "Rain", "fallback": "Twinkle", "pal": "Ocean", "col": [[80, 150, 255], [0, 0, 20], [0, 0, 0]], "sx": 140, "ix": 120},
    {"name": "Pulse", "fx": "Breathe", "col": [[255, 60, 150], [0, 0, 0], [0, 0, 0]], "sx": 110},
    {"name": "Twinkle", "fx": "Twinklefox", "fallback": "Twinkle", "pal": "* Color 1", "col": [[255, 190, 110], [0, 0, 0], [0, 0, 0]], "sx": 90, "ix": 140},
    {"name": "Rainbow slow", "fx": "Rainbow", "col": [[255, 0, 0], [0, 0, 0], [0, 0, 0]], "sx": 40, "ix": 80},
    {"name": "Party", "fx": "Colorwaves", "fallback": "Palette", "pal": "Party", "col": [[255, 0, 120], [0, 0, 0], [0, 0, 0]], "sx": 120, "ix": 128},
    {"name": "Sunset", "fx": "Palette", "fallback": "Gradient", "pal": "Sunset", "col": [[255, 100, 0], [0, 0, 0], [0, 0, 0]], "sx": 30, "ix": 128},
    {"name": "Forest", "fx": "Lake", "fallback": "Palette", "pal": "Forest", "col": [[30, 160, 40], [0, 0, 0], [0, 0, 0]], "sx": 70, "ix": 128},
    {"name": "Ice", "fx": "Flow", "fallback": "Palette", "pal": "Icefire", "col": [[120, 180, 255], [0, 0, 0], [0, 0, 0]], "sx": 70, "ix": 128},
    {"name": "Lava lamp", "fx": "Plasma", "fallback": "Palette", "pal": "Lava", "col": [[255, 40, 0], [0, 0, 0], [0, 0, 0]], "sx": 40, "ix": 128},
    {"name": "Meteor", "fx": "Meteor Smooth", "fallback": "Meteor", "col": [[220, 230, 255], [0, 0, 0], [0, 0, 0]], "sx": 140, "ix": 120},
    {"name": "Starry night", "fx": "Twinkleup", "fallback": "Twinkle", "pal": "* Colors 1&2", "col": [[200, 215, 255], [0, 0, 18], [0, 0, 0]], "sx": 60, "ix": 90},
    {"name": "Fireflies", "fx": "Fairytwinkle", "fallback": "Twinkle", "pal": "* Color 1", "col": [[190, 255, 60], [0, 8, 0], [0, 0, 0]], "sx": 70, "ix": 60},
    {"name": "Night light", "fx": "Solid", "col": [[255, 70, 12], [0, 0, 0], [0, 0, 0]]},
    {"name": "Halloween", "fx": "Theater", "fallback": "Chase", "pal": "* Colors 1&2", "col": [[255, 80, 0], [110, 0, 200], [0, 0, 0]], "sx": 90, "ix": 128},
    {"name": "Christmas classic", "fx": "Twinklefox", "fallback": "Twinkle", "pal": "C9", "col": [[255, 0, 0], [0, 160, 0], [0, 0, 0]], "sx": 80, "ix": 170},
    {"name": "Christmas cool", "fx": "Twinklefox", "fallback": "Twinkle", "pal": "* Colors 1&2", "col": [[210, 225, 255], [0, 40, 140], [0, 0, 0]], "sx": 80, "ix": 170},
    {"name": "Patriotic", "fx": "Running", "fallback": "Chase", "pal": "* Colors Only", "col": [[255, 0, 0], [255, 255, 255], [0, 0, 255]], "sx": 80, "ix": 128},
    {"name": "Valentine", "fx": "Breathe", "fallback": "Solid", "col": [[255, 20, 80], [0, 0, 0], [0, 0, 0]], "sx": 60},
    {"name": "Cool white", "fx": "Solid", "col": [[200, 220, 255], [0, 0, 0], [0, 0, 0]]},
]
