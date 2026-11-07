from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.db import Database
from app.util import sha256_bytes, utcnow


def canonical_bytes(data: bytes) -> bytes:
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return b"raw\0" + data
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def content_hash(cfg: bytes, presets: bytes) -> str:
    """Identity of a backup.

    Key order and whitespace do not create a new version. A later identical
    config is stored again so the history shows that it came back, but two
    saves in a row of the same config do not.
    """
    digest = hashlib.sha256()
    digest.update(b"wled-fleet-v1\0")
    digest.update(canonical_bytes(cfg))
    digest.update(b"\0")
    digest.update(canonical_bytes(presets))
    return digest.hexdigest()


@dataclass
class StoreResult:
    created: bool
    backup_id: int
    content_hash: str
    pruned: int = 0


class BackupStore:
    def __init__(self, db: Database, root: Path):
        self.db = db
        self.root = root
        self.blob_root = root / "blobs"

    def blob_path(self, kind: str, sha: str) -> Path:
        return self.blob_root / kind / sha

    def _write_blob(self, kind: str, sha: str, data: bytes) -> None:
        path = self.blob_path(kind, sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)

    def read_blob(self, kind: str, sha: str) -> bytes:
        return self.blob_path(kind, sha).read_bytes()

    def save(
        self,
        mac: str,
        cfg: bytes,
        presets: bytes,
        info: dict,
        reason: str,
        retention: int,
    ) -> StoreResult:
        digest = content_hash(cfg, presets)
        latest = self.db.latest_backup(mac)
        if latest and latest["content_hash"] == digest:
            self.db.mark_checked(mac, backed_up=False)
            return StoreResult(False, int(latest["id"]), digest, 0)
        cfg_sha = sha256_bytes(cfg)
        presets_sha = sha256_bytes(presets)
        info_bytes = json.dumps(info, sort_keys=True, separators=(",", ":")).encode()
        info_sha = sha256_bytes(info_bytes)
        self._write_blob("cfg", cfg_sha, cfg)
        self._write_blob("presets", presets_sha, presets)
        self._write_blob("info", info_sha, info_bytes)
        leds = info.get("leds") if isinstance(info.get("leds"), dict) else {}
        led_count = leds.get("count") if isinstance(leds, dict) else None
        backup_id = self.db.insert_backup(
            {
                "mac": mac,
                "created_at": utcnow(),
                "content_hash": digest,
                "cfg_sha": cfg_sha,
                "presets_sha": presets_sha,
                "info_sha": info_sha,
                "reason": reason,
                "version": info.get("ver") if isinstance(info.get("ver"), str) else None,
                "name": info.get("name") if isinstance(info.get("name"), str) else None,
                "led_count": led_count if isinstance(led_count, int) else None,
                "byte_size": len(cfg) + len(presets),
            }
        )
        pruned = self.prune(mac, retention)
        self.db.mark_checked(mac, backed_up=True)
        return StoreResult(True, backup_id, digest, pruned)

    def prune(self, mac: str, retention: int) -> int:
        rows = self.db.list_backups(mac)
        removed = 0
        for row in rows[retention:]:
            self.db.delete_backup(int(row["id"]))
            self._drop_if_unused("cfg", row["cfg_sha"])
            self._drop_if_unused("presets", row["presets_sha"])
            self._drop_if_unused("info", row["info_sha"])
            removed += 1
        return removed

    def _drop_if_unused(self, kind: str, sha: str) -> None:
        column = {"cfg": "cfg_sha", "presets": "presets_sha", "info": "info_sha"}[kind]
        if self.db.count_sha(column, sha) == 0:
            path = self.blob_path(kind, sha)
            if path.exists():
                path.unlink()

    def files(self, row: dict) -> tuple[bytes, bytes, bytes]:
        return (
            self.read_blob("cfg", row["cfg_sha"]),
            self.read_blob("presets", row["presets_sha"]),
            self.read_blob("info", row["info_sha"]),
        )

    def zip_bytes(self, row: dict) -> bytes:
        cfg, presets, info = self.files(row)
        manifest = {
            "mac": row["mac"],
            "created_at": row["created_at"],
            "reason": row["reason"],
            "content_hash": row["content_hash"],
            "version": row.get("version"),
            "name": row.get("name"),
            "led_count": row.get("led_count"),
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("cfg.json", cfg)
            archive.writestr("presets.json", presets)
            archive.writestr("info.json", info)
            archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode())
        return buffer.getvalue()
