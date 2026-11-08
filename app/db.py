from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from app.discovery import DeviceFacts
from app.util import utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  mac TEXT PRIMARY KEY,
  ip TEXT NOT NULL,
  port INTEGER NOT NULL DEFAULT 80,
  name TEXT NOT NULL,
  mdns TEXT,
  arch TEXT,
  core TEXT,
  version TEXT,
  release_name TEXT,
  brand TEXT,
  product TEXT,
  led_count INTEGER,
  rssi INTEGER,
  online INTEGER NOT NULL DEFAULT 0,
  last_seen TEXT,
  first_seen TEXT,
  last_backup_at TEXT,
  last_checked_at TEXT,
  forgotten INTEGER NOT NULL DEFAULT 0,
  starred INTEGER NOT NULL DEFAULT 0,
  profile_id INTEGER,
  bin_id INTEGER,
  board TEXT,
  pin TEXT,
  ota_passphrase TEXT,
  info_json TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS backups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mac TEXT NOT NULL REFERENCES devices(mac),
  created_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  cfg_sha TEXT NOT NULL,
  presets_sha TEXT NOT NULL,
  info_sha TEXT NOT NULL,
  reason TEXT NOT NULL,
  version TEXT,
  name TEXT,
  led_count INTEGER,
  byte_size INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backups_mac ON backups(mac, id DESC);

CREATE TABLE IF NOT EXISTS bins (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256 TEXT NOT NULL UNIQUE,
  filename TEXT NOT NULL,
  chip_family TEXT,
  family_source TEXT,
  byte_size INTEGER NOT NULL,
  source TEXT NOT NULL,
  source_url TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  chip_family TEXT NOT NULL,
  board TEXT,
  notes TEXT,
  bin_id INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  level TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  mac TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_id ON events(id DESC);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  sync INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
  group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  mac TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (group_id, mac)
);

CREATE TABLE IF NOT EXISTS scenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  look_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zones (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mac TEXT NOT NULL,
  segment_id INTEGER NOT NULL,
  output_index INTEGER NOT NULL,
  name TEXT NOT NULL,
  zone_id INTEGER,
  position INTEGER NOT NULL DEFAULT 0,
  named INTEGER NOT NULL DEFAULT 0,
  start INTEGER NOT NULL DEFAULT 0,
  stop INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  UNIQUE (mac, segment_id)
);

CREATE TABLE IF NOT EXISTS looks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  effect TEXT NOT NULL,
  fallback TEXT NOT NULL,
  palette TEXT NOT NULL,
  colors_json TEXT NOT NULL,
  sx INTEGER NOT NULL,
  ix INTEGER NOT NULL,
  builtin INTEGER NOT NULL DEFAULT 0,
  use_count INTEGER NOT NULL DEFAULT 0,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._split_outputs()
            self._conn.commit()

    def _split_outputs(self) -> None:
        """Let one output hold several Lights: a Light is unique by segment, not by output.

        The first Lumen schema keyed lights on (mac, output_index). SQLite cannot drop
        a table constraint, so the table is rebuilt once with the same rows and ids.
        """
        row = self._conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='lights'").fetchone()
        if row is None or "UNIQUE (mac, output_index)" not in (row[0] or ""):
            return
        columns = "id, mac, segment_id, output_index, name, zone_id, position, named, start, stop, created_at"
        self._conn.execute("ALTER TABLE lights RENAME TO lights_by_output")
        self._conn.executescript(SCHEMA)
        self._conn.execute(f"INSERT INTO lights ({columns}) SELECT {columns} FROM lights_by_output")
        self._conn.execute("DROP TABLE lights_by_output")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup_to(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dest = sqlite3.connect(path)
            try:
                self._conn.backup(dest)
            finally:
                dest.close()

    def _all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _run(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    def upsert_device(self, facts: DeviceFacts) -> dict:
        existing = self.get_device(facts.mac)
        now = utcnow()
        info_json = json.dumps(facts.info or {}, sort_keys=True, separators=(",", ":"))
        if existing is None:
            self._run(
                """
                INSERT INTO devices (
                  mac, ip, port, name, arch, core, version, release_name, brand, product,
                  led_count, rssi, online, last_seen, first_seen, info_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    facts.mac,
                    facts.ip,
                    facts.port,
                    facts.name,
                    facts.arch,
                    facts.core,
                    facts.version,
                    facts.release_name,
                    facts.brand,
                    facts.product,
                    facts.led_count,
                    facts.rssi,
                    now,
                    now,
                    info_json,
                ),
            )
            return {"created": True, "ip_changed": False, "forgotten": False}
        ip_changed = existing["ip"] != facts.ip or int(existing["port"]) != int(facts.port)
        self._run(
            """
            UPDATE devices SET
              ip=?, port=?, name=?, arch=?, core=?, version=?, release_name=?, brand=?, product=?,
              led_count=?, rssi=?, online=1, last_seen=?, info_json=?, last_error=NULL
            WHERE mac=?
            """,
            (
                facts.ip,
                facts.port,
                facts.name,
                facts.arch,
                facts.core,
                facts.version,
                facts.release_name,
                facts.brand,
                facts.product,
                facts.led_count,
                facts.rssi,
                now,
                info_json,
                facts.mac,
            ),
        )
        return {"created": False, "ip_changed": ip_changed, "forgotten": bool(existing["forgotten"])}

    def get_device(self, mac: str) -> dict | None:
        return self._one("SELECT * FROM devices WHERE mac=?", (mac,))

    def list_devices(self) -> list[dict]:
        return self._all(
            """
            SELECT * FROM devices
            ORDER BY forgotten ASC, online DESC, starred DESC, lower(name) ASC
            """
        )

    def set_online(self, mac: str, online: bool, error: str | None = None) -> None:
        if online:
            self._run(
                "UPDATE devices SET online=1, last_seen=?, last_error=NULL WHERE mac=?",
                (utcnow(), mac),
            )
            return
        self._run(
            "UPDATE devices SET online=0, last_error=? WHERE mac=?",
            (error, mac),
        )

    def set_error(self, mac: str, message: str | None) -> None:
        self._run("UPDATE devices SET last_error=? WHERE mac=?", (message, mac))

    def set_identity(self, mac: str, name: str, mdns: str | None) -> None:
        self._run("UPDATE devices SET name=?, mdns=? WHERE mac=?", (name, mdns, mac))

    def mark_checked(self, mac: str, backed_up: bool) -> None:
        now = utcnow()
        if backed_up:
            self._run(
                "UPDATE devices SET last_backup_at=?, last_checked_at=?, last_error=NULL WHERE mac=?",
                (now, now, mac),
            )
            return
        self._run(
            "UPDATE devices SET last_checked_at=?, last_error=NULL WHERE mac=?",
            (now, mac),
        )

    def set_flags(self, mac: str, *, forgotten: bool | None = None, starred: bool | None = None) -> None:
        if forgotten is not None:
            self._run("UPDATE devices SET forgotten=? WHERE mac=?", (1 if forgotten else 0, mac))
        if starred is not None:
            self._run("UPDATE devices SET starred=? WHERE mac=?", (1 if starred else 0, mac))

    def set_assignment(self, mac: str, profile_id: int | None, board: str | None, bin_id: int | None) -> None:
        self._run(
            "UPDATE devices SET profile_id=?, board=?, bin_id=? WHERE mac=?",
            (profile_id, board or None, bin_id, mac),
        )

    def set_secrets(
        self,
        mac: str,
        *,
        pin: str | None = None,
        ota_passphrase: str | None = None,
        clear_pin: bool = False,
        clear_ota: bool = False,
    ) -> None:
        current = self.get_device(mac)
        if current is None:
            return
        next_pin = None if clear_pin else current["pin"] if pin is None else (pin or None)
        next_ota = None if clear_ota else current["ota_passphrase"] if ota_passphrase is None else (ota_passphrase or None)
        self._run(
            "UPDATE devices SET pin=?, ota_passphrase=? WHERE mac=?",
            (next_pin, next_ota, mac),
        )

    def clear_profile(self, profile_id: int) -> None:
        self._run("UPDATE devices SET profile_id=NULL WHERE profile_id=?", (profile_id,))

    def clear_bin(self, bin_id: int) -> None:
        self._run("UPDATE devices SET bin_id=NULL WHERE bin_id=?", (bin_id,))
        self._run("UPDATE profiles SET bin_id=NULL WHERE bin_id=?", (bin_id,))

    def insert_backup(self, row: dict) -> int:
        return self._run(
            """
            INSERT INTO backups (
              mac, created_at, content_hash, cfg_sha, presets_sha, info_sha,
              reason, version, name, led_count, byte_size
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["mac"],
                row["created_at"],
                row["content_hash"],
                row["cfg_sha"],
                row["presets_sha"],
                row["info_sha"],
                row["reason"],
                row.get("version"),
                row.get("name"),
                row.get("led_count"),
                row["byte_size"],
            ),
        )

    def latest_backup(self, mac: str) -> dict | None:
        return self._one("SELECT * FROM backups WHERE mac=? ORDER BY id DESC LIMIT 1", (mac,))

    def previous_backup(self, mac: str, backup_id: int) -> dict | None:
        return self._one(
            "SELECT * FROM backups WHERE mac=? AND id<? ORDER BY id DESC LIMIT 1",
            (mac, backup_id),
        )

    def list_backups(self, mac: str) -> list[dict]:
        return self._all("SELECT * FROM backups WHERE mac=? ORDER BY id DESC", (mac,))

    def get_backup(self, backup_id: int) -> dict | None:
        return self._one("SELECT * FROM backups WHERE id=?", (backup_id,))

    def delete_backup(self, backup_id: int) -> None:
        self._run("DELETE FROM backups WHERE id=?", (backup_id,))

    def count_sha(self, column: str, sha: str) -> int:
        if column not in {"cfg_sha", "presets_sha", "info_sha"}:
            raise ValueError(column)
        row = self._one(f"SELECT COUNT(*) AS n FROM backups WHERE {column}=?", (sha,))
        return int(row["n"] if row else 0)

    def insert_bin(self, row: dict) -> int:
        return self._run(
            """
            INSERT INTO bins (sha256, filename, chip_family, family_source, byte_size, source, source_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["sha256"],
                row["filename"],
                row.get("chip_family"),
                row.get("family_source"),
                row["byte_size"],
                row["source"],
                row.get("source_url"),
                row["created_at"],
            ),
        )

    def get_bin(self, bin_id: int) -> dict | None:
        return self._one("SELECT * FROM bins WHERE id=?", (bin_id,))

    def get_bin_by_sha(self, sha: str) -> dict | None:
        return self._one("SELECT * FROM bins WHERE sha256=?", (sha,))

    def list_bins(self) -> list[dict]:
        return self._all("SELECT * FROM bins ORDER BY id DESC")

    def delete_bin(self, bin_id: int) -> None:
        self.clear_bin(bin_id)
        self._run("DELETE FROM bins WHERE id=?", (bin_id,))

    def insert_profile(self, row: dict) -> int:
        return self._run(
            """
            INSERT INTO profiles (name, chip_family, board, notes, bin_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (row["name"], row["chip_family"], row.get("board") or "", row.get("notes") or "", row.get("bin_id"), row["created_at"]),
        )

    def get_profile(self, profile_id: int | None) -> dict | None:
        if not profile_id:
            return None
        return self._one("SELECT * FROM profiles WHERE id=?", (profile_id,))

    def get_profile_by_name(self, name: str) -> dict | None:
        return self._one("SELECT * FROM profiles WHERE name=?", (name,))

    def list_profiles(self) -> list[dict]:
        return self._all("SELECT * FROM profiles ORDER BY name COLLATE NOCASE")

    def update_profile(self, profile_id: int, row: dict) -> None:
        self._run(
            """
            UPDATE profiles SET name=?, chip_family=?, board=?, notes=?, bin_id=? WHERE id=?
            """,
            (row["name"], row["chip_family"], row.get("board") or "", row.get("notes") or "", row.get("bin_id"), profile_id),
        )

    def delete_profile(self, profile_id: int) -> None:
        self.clear_profile(profile_id)
        self._run("DELETE FROM profiles WHERE id=?", (profile_id,))

    def add_event(self, level: str, kind: str, message: str, mac: str | None = None) -> None:
        self._run(
            "INSERT INTO events (ts, level, kind, message, mac) VALUES (?, ?, ?, ?, ?)",
            (utcnow(), level, kind, message[:500], mac),
        )
        self._run(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 1000)"
        )

    def list_events(self, limit: int = 200) -> list[dict]:
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE key=?", (key,))
        if row is None:
            return default
        return row["value"]

    def set_setting(self, key: str, value: str) -> None:
        self._run(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def delete_setting(self, key: str) -> None:
        self._run("DELETE FROM settings WHERE key=?", (key,))

    def setting_map(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self._all("SELECT key, value FROM settings")}

    def list_groups(self) -> list[dict]:
        groups = self._all("SELECT * FROM groups ORDER BY position, id")
        members = self._all("SELECT * FROM group_members ORDER BY position, mac")
        by_id = {group["id"]: group for group in groups}
        for group in groups:
            group["macs"] = []
            group["sync"] = bool(group["sync"])
        for member in members:
            group = by_id.get(member["group_id"])
            if group is not None:
                group["macs"].append(member["mac"])
        return groups

    def get_group(self, group_id: int) -> dict | None:
        for group in self.list_groups():
            if group["id"] == group_id:
                return group
        return None

    def groups_for_mac(self, mac: str) -> list[int]:
        rows = self._all("SELECT group_id FROM group_members WHERE mac=? ORDER BY group_id", (mac,))
        return [row["group_id"] for row in rows]

    def create_group(self, name: str, macs: list[str] | None = None) -> dict:
        with self._lock, self._conn:
            count = self._conn.execute("SELECT COUNT(*) AS n FROM groups").fetchone()
            position = int(count["n"] if count else 0)
            cursor = self._conn.execute(
                "INSERT INTO groups (name, position, sync, created_at) VALUES (?, ?, 0, ?)",
                (name, position, utcnow()),
            )
            group_id = int(cursor.lastrowid)
            for member_position, mac in enumerate(macs or []):
                self._conn.execute(
                    "INSERT INTO group_members (group_id, mac, position) VALUES (?, ?, ?)",
                    (group_id, mac, member_position),
                )
        created = self.get_group(group_id)
        assert created is not None
        return created

    def update_group(self, group_id: int, name: str, macs: list[str]) -> dict:
        with self._lock, self._conn:
            self._conn.execute("UPDATE groups SET name=? WHERE id=?", (name, group_id))
            self._conn.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
            for position, mac in enumerate(macs):
                self._conn.execute(
                    "INSERT INTO group_members (group_id, mac, position) VALUES (?, ?, ?)",
                    (group_id, mac, position),
                )
        updated = self.get_group(group_id)
        assert updated is not None
        return updated

    def rename_group(self, group_id: int, name: str) -> None:
        self._run("UPDATE groups SET name=? WHERE id=?", (name, group_id))

    def set_group_sync(self, group_id: int, enabled: bool) -> None:
        self._run("UPDATE groups SET sync=? WHERE id=?", (1 if enabled else 0, group_id))

    def delete_group(self, group_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
            self._conn.execute("DELETE FROM groups WHERE id=?", (group_id,))
            self._conn.commit()

    def set_group_members(self, group_id: int, macs: list[str]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM group_members WHERE group_id=?", (group_id,))
            for position, mac in enumerate(macs):
                self._conn.execute(
                    "INSERT INTO group_members (group_id, mac, position) VALUES (?, ?, ?)",
                    (group_id, mac, position),
                )
            self._conn.commit()

    def add_group_member(self, group_id: int, mac: str) -> None:
        group = self.get_group(group_id)
        if group is None or mac in group["macs"]:
            return
        self._run(
            "INSERT INTO group_members (group_id, mac, position) VALUES (?, ?, ?)",
            (group_id, mac, len(group["macs"])),
        )

    def remove_group_member(self, group_id: int, mac: str) -> None:
        self._run("DELETE FROM group_members WHERE group_id=? AND mac=?", (group_id, mac))

    def list_scenes(self) -> list[dict]:
        rows = self._all("SELECT * FROM scenes ORDER BY id DESC")
        for row in rows:
            try:
                row["look"] = json.loads(row["look_json"])
            except json.JSONDecodeError:
                row["look"] = {}
        return rows

    def get_scene(self, scene_id: int) -> dict | None:
        for scene in self.list_scenes():
            if scene["id"] == scene_id:
                return scene
        return None

    def create_scene(self, name: str, look: dict) -> dict:
        scene_id = self._run(
            "INSERT INTO scenes (name, look_json, created_at) VALUES (?, ?, ?)",
            (name, json.dumps(look, separators=(",", ":")), utcnow()),
        )
        created = self.get_scene(scene_id)
        assert created is not None
        return created

    def delete_scene(self, scene_id: int) -> None:
        self._run("DELETE FROM scenes WHERE id=?", (scene_id,))

    def list_zones(self) -> list[dict]:
        return self._all("SELECT * FROM zones ORDER BY position, id")

    def get_zone(self, zone_id: int) -> dict | None:
        return self._one("SELECT * FROM zones WHERE id=?", (zone_id,))

    def create_zone(self, name: str, position: int | None = None) -> dict:
        if position is None:
            row = self._one("SELECT COALESCE(MAX(position), -1) AS n FROM zones")
            position = int(row["n"] if row else -1) + 1
        zone_id = self._run(
            "INSERT INTO zones (name, position, created_at) VALUES (?, ?, ?)",
            (name, position, utcnow()),
        )
        created = self.get_zone(zone_id)
        assert created is not None
        return created

    def rename_zone(self, zone_id: int, name: str) -> None:
        self._run("UPDATE zones SET name=? WHERE id=?", (name, zone_id))

    def set_zone_positions(self, pairs: list[tuple[int, int]]) -> None:
        with self._lock:
            for zone_id, position in pairs:
                self._conn.execute("UPDATE zones SET position=? WHERE id=?", (position, zone_id))
            self._conn.commit()

    def delete_zone(self, zone_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE lights SET zone_id=NULL WHERE zone_id=?", (zone_id,))
            self._conn.execute("DELETE FROM zones WHERE id=?", (zone_id,))
            self._conn.commit()

    def list_lights(self) -> list[dict]:
        rows = self._all("SELECT * FROM lights ORDER BY position, id")
        for row in rows:
            row["named"] = bool(row["named"])
        return rows

    def get_light(self, light_id: int) -> dict | None:
        row = self._one("SELECT * FROM lights WHERE id=?", (light_id,))
        if row is not None:
            row["named"] = bool(row["named"])
        return row

    def lights_for_mac(self, mac: str) -> list[dict]:
        return [light for light in self.list_lights() if light["mac"] == mac]

    def insert_light(self, row: dict) -> dict:
        light_id = self._run(
            """
            INSERT INTO lights (
              mac, segment_id, output_index, name, zone_id, position, named, start, stop, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["mac"],
                int(row["segment_id"]),
                int(row["output_index"]),
                row["name"],
                row.get("zone_id"),
                int(row.get("position") or 0),
                1 if row.get("named") else 0,
                int(row.get("start") or 0),
                int(row.get("stop") or 1),
                utcnow(),
            ),
        )
        created = self.get_light(light_id)
        assert created is not None
        return created

    def update_light(self, light_id: int, fields: dict) -> dict | None:
        allowed = {"name", "zone_id", "position", "named", "start", "stop", "segment_id", "output_index"}
        sets = []
        values: list[object] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "named":
                value = 1 if value else 0
            sets.append(f"{key}=?")
            values.append(value)
        if not sets:
            return self.get_light(light_id)
        values.append(light_id)
        self._run(f"UPDATE lights SET {', '.join(sets)} WHERE id=?", tuple(values))
        return self.get_light(light_id)

    def delete_light(self, light_id: int) -> None:
        self._run("DELETE FROM lights WHERE id=?", (light_id,))

    def set_light_order(self, rows: list[dict]) -> None:
        with self._lock:
            for row in rows:
                self._conn.execute(
                    "UPDATE lights SET zone_id=?, position=? WHERE id=?",
                    (row.get("zone_id"), int(row.get("position") or 0), int(row["id"])),
                )
            self._conn.commit()

    def list_looks(self) -> list[dict]:
        rows = self._all("SELECT * FROM looks ORDER BY position, id")
        for row in rows:
            row["builtin"] = bool(row["builtin"])
            try:
                row["colors"] = json.loads(row["colors_json"])
            except json.JSONDecodeError:
                row["colors"] = [[255, 214, 170], [0, 0, 0], [0, 0, 0]]
        return rows

    def get_look(self, look_id: int) -> dict | None:
        for look in self.list_looks():
            if look["id"] == look_id:
                return look
        return None

    def insert_look(self, row: dict) -> dict:
        look_id = self._run(
            """
            INSERT INTO looks (
              name, effect, fallback, palette, colors_json, sx, ix, builtin, use_count, position, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["name"],
                row["effect"],
                row["fallback"],
                row["palette"],
                json.dumps(row["colors"], separators=(",", ":")),
                int(row.get("sx") or 0),
                int(row.get("ix") or 0),
                1 if row.get("builtin") else 0,
                int(row.get("use_count") or 0),
                int(row.get("position") or 0),
                utcnow(),
            ),
        )
        created = self.get_look(look_id)
        assert created is not None
        return created

    def touch_look(self, look_id: int) -> None:
        self._run("UPDATE looks SET use_count=use_count+1 WHERE id=?", (look_id,))

    def delete_empty_zones(self) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM zones WHERE id NOT IN (SELECT DISTINCT zone_id FROM lights WHERE zone_id IS NOT NULL)"
            )
            self._conn.commit()
