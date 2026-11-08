"""Fixture documents as JSON files on the data volume."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from app.mapper.schema_check import SchemaError, require_valid
from app.util import utcnow

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class FixtureStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def list_ids(self) -> list[str]:
        with self._lock:
            names = []
            for path in sorted(self.root.glob("*.json")):
                names.append(path.stem)
            return names

    def list_documents(self) -> list[dict]:
        return [document for fixture_id in self.list_ids() if (document := self.get(fixture_id)) is not None]

    def get(self, fixture_id: str) -> dict | None:
        path = self._path(fixture_id)
        with self._lock:
            if not path.is_file():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

    def save(self, document: dict) -> dict:
        require_valid(document)
        fixture_id = str(document["id"])
        path = self._path(fixture_id)
        now = utcnow()
        with self._lock:
            existing = None
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
            stored = dict(document)
            stored["createdAt"] = existing.get("createdAt") if isinstance(existing, dict) and existing.get("createdAt") else now
            stored["updatedAt"] = now
            require_valid(stored)
            self._write(path, stored)
            return stored

    def delete(self, fixture_id: str) -> bool:
        path = self._path(fixture_id)
        with self._lock:
            if not path.is_file():
                return False
            path.unlink()
            return True

    def _path(self, fixture_id: str) -> Path:
        if not isinstance(fixture_id, str) or _ID.fullmatch(fixture_id) is None:
            raise SchemaError([f"Fixture id {fixture_id!r} is not a usable file name."])
        return self.root / f"{fixture_id}.json"

    def _write(self, path: Path, document: dict) -> None:
        payload = json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
