from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def env_bool(raw: str | None, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def base_url(host: str, port: int) -> str:
    bracketed = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port == 80:
        return f"http://{bracketed}"
    return f"http://{bracketed}:{port}"


def clean_name(value: str) -> str:
    text = re.sub(r"[\x00-\x1f]", "", value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:32]


def clean_mdns(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9-]", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:32]


def safe_filename(value: str) -> str:
    name = Path(value or "").name.strip()
    name = re.sub(r"[^A-Za-z0-9._+-]", "_", name)
    if not name or name in {".", ".."}:
        return "firmware.bin"
    return name[:180]
