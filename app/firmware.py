from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

# ESP32 app image: magic 0xE9, default WP pin 0xEE at offset 8, chip id at offset 12.
# ESP8266 images share the magic byte but not that extended header, so a missing
# 0xEE pin means "not a confident ESP32 family" and the filename decides.
ESP32_CHIP_IDS = {
    0x0000: "esp32",
    0x0002: "esp32s2",
    0x0005: "esp32c3",
    0x0009: "esp32s3",
    0x000C: "esp32c2",
    0x0010: "esp32h2",
    0x0011: "esp32c6",
    0x0012: "esp32p4",
    0x0017: "esp32c5",
}

_FAMILY_TOKENS = (
    ("esp32-s3", "esp32s3"),
    ("esp32s3", "esp32s3"),
    ("esp32-s2", "esp32s2"),
    ("esp32s2", "esp32s2"),
    ("esp32-c3", "esp32c3"),
    ("esp32c3", "esp32c3"),
    ("esp32-c6", "esp32c6"),
    ("esp32c6", "esp32c6"),
    ("esp32-c5", "esp32c5"),
    ("esp32c5", "esp32c5"),
    ("esp32-c2", "esp32c2"),
    ("esp32c2", "esp32c2"),
    ("esp32-h2", "esp32h2"),
    ("esp32h2", "esp32h2"),
    ("esp32-p4", "esp32p4"),
    ("esp32p4", "esp32p4"),
    ("esp8266", "esp8266"),
    ("esp32", "esp32"),
)

_FAMILY_ALIASES = {token.replace("-", ""): family for token, family in _FAMILY_TOKENS}

_BOARD_TOKENS = ("dig-octa", "dig-quad", "dig-uno", "dig-next", "dig2go", "an-penta")

GITHUB_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


@dataclass
class ImageFamily:
    family: str | None
    source: str
    conflict: str | None = None


@dataclass
class CompatResult:
    ok: bool
    device_family: str | None
    bin_family: str | None
    source: str
    reason: str
    warnings: list[str] = field(default_factory=list)


def normalize_family(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", value.lower())
    if not compact:
        return None
    if compact in _FAMILY_ALIASES:
        return _FAMILY_ALIASES[compact]
    for token in sorted(_FAMILY_ALIASES, key=len, reverse=True):
        if token in compact:
            return _FAMILY_ALIASES[token]
    return None


def family_from_filename(name: str) -> str | None:
    lowered = name.lower().replace("_", "-")
    for token, family in _FAMILY_TOKENS:
        if token in lowered:
            return family
    return None


def extract_board(value: str | None) -> str | None:
    if not value:
        return None
    lowered = value.lower().replace("_", "-").replace(" ", "-")
    for token in _BOARD_TOKENS:
        if token in lowered:
            return token
    return None


def detect_image_family(data: bytes) -> str | None:
    if len(data) < 16 or data[0] != 0xE9:
        return None
    segments = data[1]
    flash_mode = data[2]
    if not 1 <= segments <= 16 or flash_mode > 3:
        return None
    if data[8] != 0xEE:
        return None
    chip_id = int.from_bytes(data[12:14], "little")
    return ESP32_CHIP_IDS.get(chip_id)


def resolve_image_family(data: bytes, filename: str, declared: str | None) -> ImageFamily:
    header = detect_image_family(data) if data else None
    named = family_from_filename(filename)
    declared_family = normalize_family(declared)
    if header and named and header != named:
        return ImageFamily(header, "header", f"File header says {header} but the name looks like {named}.")
    if header and declared_family and header != declared_family:
        return ImageFamily(header, "header", f"File header says {header}, not {declared_family}.")
    if named and declared_family and named != declared_family:
        return ImageFamily(named, "filename", f"File name looks like {named}, not {declared_family}.")
    if header:
        return ImageFamily(header, "header")
    if named:
        return ImageFamily(named, "filename")
    if declared_family:
        return ImageFamily(declared_family, "declared")
    return ImageFamily(None, "unknown")


def _feature_warnings(filename: str, release_name: str | None) -> list[str]:
    warnings: list[str] = []
    lowered = filename.lower()
    release = (release_name or "").lower()
    for label, token in (("Ethernet", "ethernet"), ("AE+", "aeplus"), ("temperature", "temp")):
        in_file = token in lowered.replace("-", "").replace("_", "")
        in_release = token in release.replace("-", "").replace("_", "")
        if in_file and not in_release:
            warnings.append(f"This file looks like a {label} build.")
        if in_release and not in_file and token != "temp":
            warnings.append(f"Controller release name mentions {label}. This file may be a different build.")
    return warnings


def check_compat(
    device_arch: str | None,
    data: bytes,
    filename: str,
    declared: str | None = None,
    release_name: str | None = None,
    board: str | None = None,
) -> CompatResult:
    device_family = normalize_family(device_arch)
    image = resolve_image_family(data, filename, declared)
    if image.conflict:
        return CompatResult(False, device_family, image.family, image.source, image.conflict)
    if not device_family:
        return CompatResult(False, None, image.family, image.source, "Controller chip is unknown.")
    if not image.family:
        return CompatResult(
            False,
            device_family,
            None,
            image.source,
            "Could not tell which chip this file is built for.",
        )
    if image.family != device_family:
        return CompatResult(
            False,
            device_family,
            image.family,
            image.source,
            f"File is {image.family}; this controller is {device_family}.",
        )
    warnings = _feature_warnings(filename, release_name)
    bin_board = extract_board(filename)
    device_board = extract_board(board) or extract_board(release_name)
    if bin_board and device_board and bin_board != device_board:
        return CompatResult(
            False,
            device_family,
            image.family,
            image.source,
            f"File is built for {bin_board}; this controller is {device_board}.",
            warnings,
        )
    if device_board and not bin_board:
        warnings.append(
            f"File has no board name. This controller is marked {device_board}; QuinLED boards usually need their own build."
        )
    if bin_board and not device_board:
        warnings.append(f"File is a {bin_board} build. Assign a firmware profile if this is a different board.")
    return CompatResult(True, device_family, image.family, image.source, "Chip matches.", warnings)


def score_filename(
    filename: str,
    arch: str | None,
    release_name: str | None = None,
    board: str | None = None,
) -> int:
    family = family_from_filename(filename)
    device = normalize_family(arch)
    if family and device and family != device:
        return -1
    if family is None and device == "esp8266":
        return 0
    score = 1
    if family and device and family == device:
        score += 10
    wanted = extract_board(board) or extract_board(release_name)
    file_board = extract_board(filename)
    if wanted and file_board and wanted != file_board:
        return -1
    if wanted and file_board and wanted == file_board:
        score += 20
    elif wanted and file_board is None and device and device.startswith("esp32"):
        score += 0
    if family is None and device and device.startswith("esp32") and file_board:
        score += 5
    if release_name and release_name.lower() in filename.lower():
        score += 5
    lowered = filename.lower()
    if any(token in lowered for token in ("audioreactive", "ethernet", "aeplus", "ae-plus")):
        score -= 2
    release = (release_name or "").lower().replace("-", "")
    compact = lowered.replace("-", "").replace("_", "")
    if "ethernet" in release and "ethernet" in compact:
        score += 8
    if "aeplus" in release and "aeplus" in compact:
        score += 8
    return score


def clean_repo(repo: str) -> str:
    text = repo.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        raise ValueError("Use a GitHub repo like wled/WLED.")
    return text


def allowed_firmware_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host in GITHUB_HOSTS:
        return True
    return host.endswith(".githubusercontent.com")


DEFAULT_PROFILES = (
    {
        "name": "Dig-Uno ESP32",
        "chip_family": "esp32",
        "board": "dig-uno",
        "notes": "QuinLED Dig-Uno v3/v4. Use the Dig-Uno .bin from QuinLED-Firmware, not a generic ESP32 image. AE+ and Ethernet hats are separate files.",
    },
    {
        "name": "Dig-Uno ESP8266",
        "chip_family": "esp8266",
        "board": "dig-uno",
        "notes": "Older Dig-Uno boards. Use an official WLED ESP8266 build.",
    },
    {
        "name": "Dig-Quad ESP32",
        "chip_family": "esp32",
        "board": "dig-quad",
        "notes": "QuinLED Dig-Quad. Use the matching Dig-Quad .bin. AE+ and Ethernet are separate files.",
    },
    {
        "name": "Generic ESP32",
        "chip_family": "esp32",
        "board": "",
        "notes": "Stock WLED ESP32 build from the official WLED releases.",
    },
    {
        "name": "Generic ESP8266",
        "chip_family": "esp8266",
        "board": "",
        "notes": "Stock WLED ESP8266 build from the official WLED releases.",
    },
)
