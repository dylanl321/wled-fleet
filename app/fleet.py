from __future__ import annotations

import asyncio
import json
import logging
import socket
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import httpx

from app.backup import BackupStore, StoreResult
from app.config import Settings
from app.config_edit import apply_led_counts
from app.db import Database
from app.discovery import (
    bus_summaries,
    hosts_for_cidr,
    is_wled_info,
    network_labels,
    parse_extra_hosts,
    parse_info,
    parse_subnets,
    preset_entries,
    select_services,
    service_priority,
)
from app.errors import FleetError, WledError
from app.firmware import (
    DEFAULT_PROFILES,
    allowed_firmware_url,
    check_compat,
    clean_repo,
    extract_board,
    normalize_family,
    resolve_image_family,
    score_filename,
)
from app.mdns import MdnsWatcher
from app.util import clean_mdns, clean_name, safe_filename, sha256_bytes, utcnow
from app.wled import WledClient

log = logging.getLogger(__name__)

SETTING_LIMITS = {
    "backup_interval": (300, 86400 * 30),
    "backup_watch_interval": (15, 86400),
    "backup_retention": (1, 500),
    "discovery_interval": (5, 3600),
    "presence_interval": (5, 600),
}
MAX_SWEEP_HOSTS = 2048


@dataclass
class RuntimeSettings:
    scan_subnets: str
    extra_hosts: str
    backup_interval: int
    backup_watch_interval: int
    backup_retention: int
    discovery_interval: int
    presence_interval: int
    sources: dict[str, str]


@dataclass
class Job:
    id: str
    kind: str
    status: str
    total: int
    done: int = 0
    results: list[dict] = field(default_factory=list)
    error: str | None = None
    created_at: str = field(default_factory=utcnow)

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "results": self.results,
            "error": self.error,
            "created_at": self.created_at,
        }


class Fleet:
    def __init__(self, db: Database, settings: Settings, http: httpx.AsyncClient):
        self.db = db
        self.settings = settings
        self.http = http
        self.wled = WledClient(http, ota_timeout=settings.ota_timeout)
        self.backups = BackupStore(db, settings.data_dir)
        self.mdns = MdnsWatcher()
        self.activity: dict[str, str] = {}
        self.jobs: dict[str, Job] = {}
        self.last_scan: dict = {"at": None, "probed": 0, "online": 0, "added": 0, "skipped": False}
        self._locks: dict[str, asyncio.Lock] = {}
        self._scan_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._jobs: asyncio.Queue[tuple[Job, Callable[[Job], Awaitable[None]]]] = asyncio.Queue()
        self._negative_until: dict[str, float] = {}
        self._release_cache: dict[str, tuple[float, list]] = {}

    def lock(self, mac: str) -> asyncio.Lock:
        if mac not in self._locks:
            self._locks[mac] = asyncio.Lock()
        return self._locks[mac]

    def stop(self) -> None:
        self._stop.set()

    async def run_jobs(self) -> None:
        while not self._stop.is_set():
            job, work = await self._jobs.get()
            job.status = "running"
            try:
                await work(job)
                if job.status == "running":
                    job.status = "done"
            except Exception as exc:
                job.status = "error"
                job.error = str(exc)
                log.exception("job %s failed", job.id)

    async def run_maintenance(self) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.mdns.start, loop)
        discovery = asyncio.create_task(self._discovery_loop(), name="discovery")
        backup = asyncio.create_task(self._backup_loop(), name="backup")
        try:
            await self._stop.wait()
        finally:
            discovery.cancel()
            backup.cancel()
            await asyncio.gather(discovery, backup, return_exceptions=True)
            await asyncio.to_thread(self.mdns.close)

    def runtime(self) -> RuntimeSettings:
        saved = self.db.setting_map()

        def text(key: str, env_value: str) -> tuple[str, str]:
            if key in saved:
                return saved[key], "saved"
            return env_value, "environment"

        def number(key: str, env_value: int) -> tuple[int, str]:
            low, high = SETTING_LIMITS[key]
            if key in saved and saved[key] != "":
                try:
                    value = int(saved[key])
                except ValueError:
                    value = env_value
                    source = "environment"
                else:
                    source = "saved"
            else:
                value = env_value
                source = "environment"
            return max(low, min(high, value)), source

        subnets, subnet_source = text("scan_subnets", self.settings.scan_subnets)
        hosts, host_source = text("extra_hosts", self.settings.extra_hosts)
        backup_interval, backup_source = number("backup_interval", self.settings.backup_interval)
        watch, watch_source = number("backup_watch_interval", self.settings.backup_watch_interval)
        retention, retention_source = number("backup_retention", self.settings.backup_retention)
        discovery, discovery_source = number("discovery_interval", self.settings.discovery_interval)
        presence, presence_source = number("presence_interval", self.settings.presence_interval)
        return RuntimeSettings(
            scan_subnets=subnets,
            extra_hosts=hosts,
            backup_interval=backup_interval,
            backup_watch_interval=watch,
            backup_retention=retention,
            discovery_interval=discovery,
            presence_interval=presence,
            sources={
                "scan_subnets": subnet_source,
                "extra_hosts": host_source,
                "backup_interval": backup_source,
                "backup_watch_interval": watch_source,
                "backup_retention": retention_source,
                "discovery_interval": discovery_source,
                "presence_interval": presence_source,
            },
        )

    def update_settings(self, payload: dict) -> RuntimeSettings:
        if payload.get("reset"):
            for key in list(SETTING_LIMITS) + ["scan_subnets", "extra_hosts"]:
                self.db.delete_setting(key)
            return self.runtime()
        for key, value in payload.items():
            if key == "reset":
                continue
            if value is None:
                self.db.delete_setting(key)
                continue
            if key in {"scan_subnets", "extra_hosts"}:
                text = str(value).strip()
                if key == "scan_subnets":
                    _, errors = parse_subnets(text)
                else:
                    _, errors = parse_extra_hosts(text)
                if errors:
                    raise FleetError(" ".join(errors))
                self.db.set_setting(key, text)
                continue
            if key in SETTING_LIMITS:
                low, high = SETTING_LIMITS[key]
                try:
                    number = int(value)
                except (TypeError, ValueError) as exc:
                    raise FleetError(f"{key} must be a number.") from exc
                if not low <= number <= high:
                    raise FleetError(f"{key} must be between {low} and {high}.")
                self.db.set_setting(key, str(number))
        return self.runtime()

    def event(self, level: str, kind: str, message: str, mac: str | None = None) -> None:
        self.db.add_event(level, kind, message, mac)
        log.info("%s %s", kind, message)

    def _require(self, mac: str) -> dict:
        device = self.db.get_device(mac)
        if device is None:
            raise FleetError("Unknown controller.", 404)
        return device

    def public_device(self, row: dict) -> dict:
        profile = self.db.get_profile(row.get("profile_id"))
        return {
            "mac": row["mac"],
            "ip": row["ip"],
            "port": row["port"],
            "name": row["name"],
            "mdns": row.get("mdns"),
            "arch": row.get("arch"),
            "core": row.get("core"),
            "version": row.get("version"),
            "release_name": row.get("release_name"),
            "brand": row.get("brand"),
            "product": row.get("product"),
            "led_count": row.get("led_count"),
            "rssi": row.get("rssi"),
            "online": bool(row["online"]),
            "last_seen": row.get("last_seen"),
            "first_seen": row.get("first_seen"),
            "last_backup_at": row.get("last_backup_at"),
            "last_checked_at": row.get("last_checked_at"),
            "forgotten": bool(row["forgotten"]),
            "starred": bool(row["starred"]),
            "profile_id": row.get("profile_id"),
            "profile_name": profile["name"] if profile else None,
            "bin_id": row.get("bin_id"),
            "board": row.get("board"),
            "last_error": row.get("last_error"),
            "has_pin": bool(row.get("pin")),
            "has_ota_passphrase": bool(row.get("ota_passphrase")),
            "activity": self.activity.get(row["mac"]),
        }

    def list_public(self) -> list[dict]:
        return [self.public_device(row) for row in self.db.list_devices()]

    async def _discovery_loop(self) -> None:
        next_full = 0.0
        while not self._stop.is_set():
            runtime = self.runtime()
            full = time.monotonic() >= next_full
            try:
                await self.scan_once(full=full)
            except Exception:
                log.exception("discovery failed")
            if full:
                next_full = time.monotonic() + runtime.discovery_interval
            await self._sleep(runtime.presence_interval)

    async def _backup_loop(self) -> None:
        await self._sleep(3)
        while not self._stop.is_set():
            try:
                await self.backup_pass()
            except Exception:
                log.exception("backup pass failed")
            await self._sleep(min(30, self.runtime().backup_watch_interval))

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(0.1, seconds))
        except asyncio.TimeoutError:
            return

    def request_scan(self) -> bool:
        if self._scan_lock.locked():
            return False
        task = asyncio.create_task(self.scan_once(full=True))
        task.add_done_callback(self._log_task)
        return True

    def _log_task(self, task: asyncio.Task) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("background scan failed")

    async def scan_once(self, full: bool) -> dict:
        if self._scan_lock.locked():
            return {"skipped": True}
        async with self._scan_lock:
            summary = await self._scan_once(full=full)
        for mac in summary.pop("new_macs", []):
            try:
                await self.backup_device(mac, "discover")
            except FleetError as exc:
                self.db.set_error(mac, str(exc))
                self.event("error", "backup.failed", str(exc), mac)
        self.last_scan = {"at": utcnow(), "mdns": self.mdns.ready, **summary}
        return summary

    async def _scan_once(self, full: bool) -> dict:
        runtime = self.runtime()
        targets: dict[tuple[str, int], str] = {}
        for host, port in self._extra_targets(runtime):
            targets[(host, port)] = "extra"
        for host, port, source in await self._mdns_targets():
            targets.setdefault((host, port), source)
        for host, port in await self._node_targets():
            targets.setdefault((host, port), "nodes")
        if full:
            for host in self._subnet_hosts(runtime):
                targets.setdefault((host, 80), "sweep")
        for device in self.db.list_devices():
            targets.setdefault((device["ip"], int(device["port"])), "known")

        probed = await self._probe_all(targets)
        seen: dict[str, tuple[str, int]] = {}
        added = 0
        ip_changes = 0
        new_macs: list[str] = []
        for (host, port), info in probed:
            try:
                facts = parse_info(info, host, port)
            except ValueError as exc:
                log.info("skip %s:%s: %s", host, port, exc)
                continue
            if facts.mac in seen and seen[facts.mac] != (host, port):
                log.warning("MAC %s answered on %s and %s:%s", facts.mac, seen[facts.mac], host, port)
                continue
            seen[facts.mac] = (host, port)
            result = self.db.upsert_device(facts)
            if result["created"]:
                added += 1
                new_macs.append(facts.mac)
                self.event("info", "discovery.new", f"Found {facts.name} at {host}", facts.mac)
            elif result["ip_changed"]:
                ip_changes += 1
                self.event("info", "discovery.ip", f"{facts.name} moved to {host}", facts.mac)

        follow: dict[tuple[str, int], str] = {}
        for mac in list(seen):
            device = self.db.get_device(mac)
            if device is None or device["forgotten"]:
                continue
            for host, port in await self.wled.node_targets(device):
                follow.setdefault((host, port), "nodes")
        if follow:
            for (host, port), info in await self._probe_all(follow):
                try:
                    facts = parse_info(info, host, port)
                except ValueError:
                    continue
                if facts.mac in seen:
                    continue
                seen[facts.mac] = (host, port)
                result = self.db.upsert_device(facts)
                if result["created"]:
                    added += 1
                    new_macs.append(facts.mac)
                    self.event("info", "discovery.new", f"Found {facts.name} at {host} via a neighbor", facts.mac)

        offline = 0
        for device in self.db.list_devices():
            if device["mac"] in seen:
                continue
            if device["online"]:
                offline += 1
                self.event("warn", "discovery.offline", f"{device['name']} is not answering", device["mac"])
            self.db.set_online(device["mac"], False, "Not answering")

        summary = {
            "probed": len(targets),
            "online": len(seen),
            "added": added,
            "offline": offline,
            "ip_changes": ip_changes,
            "full": full,
            "new_macs": new_macs,
            "skipped": False,
        }
        log.info(
            "scan probed=%s online=%s added=%s offline=%s full=%s",
            summary["probed"],
            summary["online"],
            added,
            offline,
            full,
        )
        return summary

    def _extra_targets(self, runtime: RuntimeSettings) -> list[tuple[str, int]]:
        hosts, errors = parse_extra_hosts(runtime.extra_hosts)
        for error in errors:
            log.warning("extra host ignored: %s", error)
        return hosts

    def _subnet_hosts(self, runtime: RuntimeSettings) -> list[str]:
        subnets, errors = parse_subnets(runtime.scan_subnets)
        for error in errors:
            log.warning("subnet ignored: %s", error)
        hosts: list[str] = []
        for subnet in subnets:
            try:
                hosts.extend(hosts_for_cidr(subnet))
            except ValueError as exc:
                log.warning("subnet skipped: %s", exc)
        if len(hosts) > MAX_SWEEP_HOSTS:
            log.warning("sweep truncated from %s to %s hosts", len(hosts), MAX_SWEEP_HOSTS)
            return hosts[:MAX_SWEEP_HOSTS]
        return hosts

    async def _mdns_targets(self) -> list[tuple[str, int, str]]:
        records = []
        for type_, name in self.mdns.drain():
            resolved = await self.mdns.resolve(type_, name)
            if resolved:
                records.append(resolved)
        selected = select_services(records)
        targets: list[tuple[str, int, str]] = []
        now = time.monotonic()
        for record in selected:
            source = "mdns" if service_priority(record["type"], record["name"]) == 0 else "http-mdns"
            for address in record["addresses"]:
                if source == "http-mdns" and self._negative_until.get(address, 0) > now:
                    continue
                targets.append((address, int(record["port"] or 80), source))
        return targets

    async def _node_targets(self) -> list[tuple[str, int]]:
        targets: list[tuple[str, int]] = []
        for device in self.db.list_devices():
            if not device["online"] or device["forgotten"]:
                continue
            for host, port in await self.wled.node_targets(device):
                targets.append((host, port))
        return targets

    async def _probe_all(self, targets: dict[tuple[str, int], str]) -> list[tuple[tuple[str, int], dict]]:
        semaphore = asyncio.Semaphore(max(1, self.settings.scan_concurrency))
        timeout = httpx.Timeout(self.settings.scan_timeout, connect=min(0.5, self.settings.scan_timeout))

        async def probe(host: str, port: int, source: str) -> tuple[tuple[str, int], dict] | None:
            async with semaphore:
                contact = await asyncio.to_thread(resolve_ipv4, host)
                if ":" in contact and not contact.startswith("["):
                    url = f"http://[{contact}]:{port}/json/info"
                elif port == 80:
                    url = f"http://{contact}/json/info"
                else:
                    url = f"http://{contact}:{port}/json/info"
                try:
                    response = await self.http.get(url, timeout=timeout)
                    data = response.json()
                except (httpx.HTTPError, ValueError, UnicodeError):
                    if source == "http-mdns":
                        self._negative_until[contact] = time.monotonic() + 600
                    return None
                if response.status_code != 200 or not isinstance(data, dict):
                    if source == "http-mdns":
                        self._negative_until[contact] = time.monotonic() + 600
                    return None
                if not is_wled_info(data):
                    if source == "http-mdns":
                        self._negative_until[contact] = time.monotonic() + 600
                    return None
                self._negative_until.pop(contact, None)
                return (contact, port), data

        results = await asyncio.gather(*(probe(host, port, source) for (host, port), source in targets.items()))
        return [item for item in results if item is not None]

    async def backup_pass(self) -> None:
        runtime = self.runtime()
        now = time.time()
        last_schedule = self.db.get_setting("last_scheduled_backup")
        if last_schedule is None:
            self.db.set_setting("last_scheduled_backup", str(now))
            schedule_due = False
        else:
            schedule_due = now - float(last_schedule or 0) >= runtime.backup_interval
        last_watch = float(self.db.get_setting("last_watch_backup", "0") or 0)
        watch_due = now - last_watch >= runtime.backup_watch_interval
        if not schedule_due and not watch_due:
            return
        created = 0
        unchanged = 0
        for device in self.db.list_devices():
            if device["forgotten"] or not device["online"]:
                continue
            reason = "schedule" if schedule_due else "change"
            try:
                result = await self.backup_device(device["mac"], reason)
            except FleetError as exc:
                self.db.set_error(device["mac"], str(exc))
                self.event("error", "backup.failed", str(exc), device["mac"])
                continue
            if result.created:
                created += 1
            else:
                unchanged += 1
        if watch_due:
            self.db.set_setting("last_watch_backup", str(now))
        if schedule_due:
            self.db.set_setting("last_scheduled_backup", str(now))
            self.event(
                "info",
                "backup.schedule",
                f"Daily backup finished. {created} updated, {unchanged} unchanged.",
            )

    async def backup_device(self, mac: str, reason: str) -> StoreResult:
        async with self.lock(mac):
            device = self._require(mac)
            return await self._backup_unlocked(device, reason)

    async def _backup_unlocked(self, device: dict, reason: str) -> StoreResult:
        self.activity[device["mac"]] = "backing-up"
        try:
            cfg, presets, info = await self.wled.download_backup(device)
            result = self.backups.save(
                device["mac"],
                cfg,
                presets,
                info,
                reason,
                self.runtime().backup_retention,
            )
        finally:
            self.activity.pop(device["mac"], None)
        if result.created:
            self.event("info", "backup.created", f"Saved a {reason} backup for {device['name']}", device["mac"])
        labels = {}
        try:
            labels = network_labels(json.loads(cfg))
        except (json.JSONDecodeError, UnicodeError):
            labels = {}
        if labels.get("mdns") or labels.get("name"):
            self.db.set_identity(device["mac"], labels.get("name") or device["name"], labels.get("mdns"))
        return result

    async def live(self, mac: str) -> dict:
        device = self._require(mac)
        try:
            state, cfg_bytes, presets_bytes = await asyncio.gather(
                self.wled.get_json(device, "/json/state"),
                self.wled.get_cfg_bytes(device),
                self.wled.get_presets_bytes(device),
            )
        except WledError as exc:
            self.db.set_online(mac, False, str(exc))
            return {"online": False, "error": str(exc), "state": None, "presets": [], "buses": []}
        try:
            cfg = json.loads(cfg_bytes)
        except (json.JSONDecodeError, UnicodeError):
            cfg = {}
        try:
            presets = json.loads(presets_bytes)
        except (json.JSONDecodeError, UnicodeError):
            presets = {}
        labels = network_labels(cfg)
        if labels.get("mdns"):
            self.db.set_identity(mac, labels.get("name") or device["name"], labels.get("mdns"))
        self.db.set_online(mac, True)
        return {
            "online": True,
            "error": None,
            "state": state,
            "presets": preset_entries(presets),
            "buses": bus_summaries(cfg),
            "ssid": labels.get("ssid"),
            "ap_ssid": labels.get("ap_ssid"),
            "mdns": labels.get("mdns"),
            "name": labels.get("name") or device["name"],
        }

    async def set_state(self, mac: str, payload: dict) -> dict:
        device = self._require(mac)
        body = {key: value for key, value in payload.items() if value is not None}
        if not body:
            raise FleetError("Nothing to change.")
        state = await self.wled.post_state(device, body)
        self.db.set_online(mac, True)
        return state

    async def set_color(self, mac: str, segment: int, color: list[int], turn_on: bool) -> dict:
        body: dict = {"seg": [{"id": segment, "col": [color]}]}
        if turn_on:
            body["on"] = True
        return await self.set_state(mac, body)

    async def set_segments(self, mac: str, segments: list[dict]) -> dict:
        body = {
            "seg": [
                {
                    "id": item["id"],
                    "start": item["start"],
                    "stop": item["stop"],
                    "on": item["on"],
                    "rev": item["rev"],
                }
                for item in segments
            ]
        }
        return await self.set_state(mac, body)

    async def rename(self, mac: str, name: str, mdns: str | None) -> dict:
        async with self.lock(mac):
            device = self._require(mac)
            clean = clean_name(name)
            if not clean:
                raise FleetError("Enter a name.")
            mdns_clean = None
            if mdns is not None:
                mdns_clean = clean_mdns(mdns)
                if not mdns_clean:
                    raise FleetError("mDNS name needs letters or numbers.")
            current_mdns = device.get("mdns") or ""
            mdns_changed = mdns_clean is not None and mdns_clean != current_mdns
            payload: dict = {"id": {"name": clean}}
            if mdns_clean is not None:
                payload["id"]["mdns"] = mdns_clean
            if mdns_changed:
                payload["rb"] = True
            await self.wled.post_cfg(device, payload)
            if mdns_changed:
                info = await self.wled.wait_ready(device)
                self.db.upsert_device(parse_info(info, device["ip"], int(device["port"])))
            self.db.set_identity(mac, clean, mdns_clean if mdns_clean is not None else device.get("mdns"))
            self.event("info", "device.rename", f"Renamed to {clean}", mac)
            return self.public_device(self._require(mac))

    async def set_leds(self, mac: str, buses: list[dict]) -> dict:
        async with self.lock(mac):
            device = self._require(mac)
            self.activity[mac] = "rebooting"
            try:
                await self._backup_unlocked(device, "pre-change")
                raw = await self.wled.get_cfg_bytes(device)
                try:
                    cfg = json.loads(raw)
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise FleetError("Controller config is not valid JSON.") from exc
                if not isinstance(cfg, dict):
                    raise FleetError("Controller config is not valid JSON.")
                try:
                    apply_led_counts(cfg, buses)
                except ValueError as exc:
                    raise FleetError(str(exc)) from exc
                encoded = json.dumps(cfg).encode()
                await self.wled.upload(device, "cfg.json", encoded, "application/json")
                info = await self.wled.wait_ready(device)
                self.db.upsert_device(parse_info(info, device["ip"], int(device["port"])))
                fresh = self._require(mac)
                await self._backup_unlocked(fresh, "change")
            finally:
                self.activity.pop(mac, None)
            self.event("info", "device.leds", f"Updated LED outputs on {device['name']}", mac)
            return self.public_device(self._require(mac))

    async def identify(self, mac: str) -> None:
        device = self._require(mac)
        await self.wled.identify(device)
        self.event("info", "device.identify", f"Flashed {device['name']}", mac)

    async def reboot_device(self, mac: str) -> dict:
        async with self.lock(mac):
            device = self._require(mac)
            self.activity[mac] = "rebooting"
            try:
                info = await self.wled.reboot(device)
                self.db.upsert_device(parse_info(info, device["ip"], int(device["port"])))
            finally:
                self.activity.pop(mac, None)
        self.event("info", "device.reboot", f"Rebooted {device['name']}", mac)
        return self.public_device(self._require(mac))

    async def restore(self, mac: str, backup_id: int) -> dict:
        async with self.lock(mac):
            device = self._require(mac)
            row = self.db.get_backup(backup_id)
            if row is None or row["mac"] != mac:
                raise FleetError("Backup not found.", 404)
            self.activity[mac] = "restoring"
            try:
                await self._backup_unlocked(device, "pre-restore")
                cfg, presets, _info = self.backups.files(row)
                await self.wled.upload(device, "presets.json", presets, "application/json")
                await self.wled.upload(device, "cfg.json", cfg, "application/json")
                info = await self.wled.wait_ready(device, timeout=60)
                self.db.upsert_device(parse_info(info, device["ip"], int(device["port"])))
            finally:
                self.activity.pop(mac, None)
        self.event("info", "restore.ok", f"Restored a backup onto {device['name']}", mac)
        return self.public_device(self._require(mac))

    def bin_path(self, sha: str) -> Path:
        return self.settings.data_dir / "firmware" / f"{sha}.bin"

    def public_bin(self, row: dict) -> dict:
        return {
            "id": row["id"],
            "sha256": row["sha256"],
            "filename": row["filename"],
            "chip_family": row.get("chip_family"),
            "family_source": row.get("family_source"),
            "byte_size": row["byte_size"],
            "source": row["source"],
            "source_url": row.get("source_url"),
            "created_at": row["created_at"],
        }

    def save_firmware(
        self,
        data: bytes,
        filename: str,
        source: str,
        source_url: str | None = None,
        declared: str | None = None,
    ) -> dict:
        filename = safe_filename(filename)
        if len(data) > 8 * 1024 * 1024:
            raise FleetError("Firmware file is larger than 8 MB.", 413)
        if len(data) < 16 or data[0] != 0xE9:
            raise FleetError("This file does not look like ESP firmware. It should start with byte E9.", 400)
        image = resolve_image_family(data, filename, declared)
        if image.conflict:
            raise FleetError(image.conflict, 400)
        if not image.family:
            raise FleetError("Could not tell which chip this file is for. Choose a chip family and upload again.", 400)
        digest = sha256_bytes(data)
        path = self.bin_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        existing = self.db.get_bin_by_sha(digest)
        if existing:
            return self.public_bin(existing)
        bin_id = self.db.insert_bin(
            {
                "sha256": digest,
                "filename": filename,
                "chip_family": image.family,
                "family_source": image.source,
                "byte_size": len(data),
                "source": source,
                "source_url": source_url,
                "created_at": utcnow(),
            }
        )
        self.event("info", "firmware.stored", f"Stored firmware {filename} for {image.family}")
        stored = self.db.get_bin(bin_id)
        assert stored is not None
        return self.public_bin(stored)

    def read_bin(self, bin_id: int) -> tuple[dict, bytes]:
        row = self.db.get_bin(bin_id)
        if row is None:
            raise FleetError("Firmware file not found.", 404)
        path = self.bin_path(row["sha256"])
        if not path.exists():
            raise FleetError("Firmware file is missing from disk.", 404)
        return row, path.read_bytes()

    def check_devices(self, macs: list[str], bin_id: int | None) -> list[dict]:
        bins = [self.db.get_bin(bin_id)] if bin_id else self.db.list_bins()
        reports = []
        for mac in macs:
            device = self._require(mac)
            profile = self.db.get_profile(device.get("profile_id"))
            board = device.get("board") or (profile or {}).get("board")
            bin_reports = []
            for row in bins:
                if row is None:
                    continue
                try:
                    _row, data = self.read_bin(int(row["id"]))
                except FleetError as exc:
                    bin_reports.append(
                        {"id": row["id"], "filename": row["filename"], "ok": False, "reason": str(exc), "warnings": []}
                    )
                    continue
                compat = check_compat(
                    device.get("arch"),
                    data,
                    row["filename"],
                    row.get("chip_family"),
                    device.get("release_name"),
                    board,
                )
                effective = device.get("bin_id") or (profile or {}).get("bin_id")
                bin_reports.append(
                    {
                        "id": row["id"],
                        "filename": row["filename"],
                        "chip_family": row.get("chip_family"),
                        "ok": compat.ok,
                        "reason": compat.reason,
                        "warnings": compat.warnings,
                        "profile_bin": effective == row["id"],
                        "score": score_filename(row["filename"], device.get("arch"), device.get("release_name"), board),
                    }
                )
            bin_reports.sort(key=lambda item: (not item["ok"], not item.get("profile_bin"), -item.get("score", 0), item["filename"]))
            reports.append(
                {
                    "mac": device["mac"],
                    "name": device["name"],
                    "arch": device.get("arch"),
                    "release_name": device.get("release_name"),
                    "board": board,
                    "bins": bin_reports,
                }
            )
        return reports

    async def update_firmware(self, mac: str, bin_id: int, force: bool) -> dict:
        async with self.lock(mac):
            device = self._require(mac)
            row, data = self.read_bin(bin_id)
            profile = self.db.get_profile(device.get("profile_id"))
            board = device.get("board") or (profile or {}).get("board")
            compat = check_compat(
                device.get("arch"),
                data,
                row["filename"],
                row.get("chip_family"),
                device.get("release_name"),
                board,
            )
            if not compat.ok and not force:
                self.event("warn", "firmware.rejected", f"{device['name']}: {compat.reason}", mac)
                raise FleetError(compat.reason, 409)
            if not compat.ok and force:
                self.event("warn", "firmware.forced", f"Forced update on {device['name']}: {compat.reason}", mac)
            self.activity[mac] = "updating"
            old_version = device.get("version")
            try:
                await self._backup_unlocked(device, "pre-update")
                await self.wled.ota(device, row["filename"], data)
                info = await self.wled.wait_ready(device, timeout=self.settings.ota_timeout)
                self.db.upsert_device(parse_info(info, device["ip"], int(device["port"])))
            finally:
                self.activity.pop(mac, None)
        version = info.get("ver")
        self.event(
            "info",
            "firmware.updated",
            f"Updated {device['name']} from {old_version or 'unknown'} to {version}",
            mac,
        )
        return {"mac": mac, "version": version, "warnings": compat.warnings, "previous": old_version}

    def ensure_default_profiles(self) -> list[dict]:
        for item in DEFAULT_PROFILES:
            if self.db.get_profile_by_name(item["name"]):
                continue
            self.db.insert_profile({**item, "bin_id": None, "created_at": utcnow()})
        return self.db.list_profiles()

    def save_profile(self, payload: dict, profile_id: int | None = None) -> dict:
        family = normalize_family(payload["chip_family"])
        if not family:
            raise FleetError("Choose a chip family such as esp32 or esp8266.")
        board = extract_board(payload.get("board")) or (payload.get("board") or "").strip().lower()[:40]
        row = {
            "name": clean_name(payload["name"]) or payload["name"].strip()[:60],
            "chip_family": family,
            "board": board,
            "notes": (payload.get("notes") or "")[:400],
            "bin_id": payload.get("bin_id"),
        }
        if not row["name"]:
            raise FleetError("Enter a profile name.")
        if row["bin_id"]:
            blob = self.db.get_bin(int(row["bin_id"]))
            if blob is None:
                raise FleetError("Firmware file not found.", 404)
            if blob.get("chip_family") and blob["chip_family"] != family:
                raise FleetError(f"That file is {blob['chip_family']}, not {family}.", 409)
        if profile_id is None:
            try:
                new_id = self.db.insert_profile({**row, "created_at": utcnow()})
            except sqlite3.IntegrityError as exc:
                raise FleetError("A profile with that name already exists.") from exc
        else:
            if self.db.get_profile(profile_id) is None:
                raise FleetError("Profile not found.", 404)
            self.db.update_profile(profile_id, row)
            new_id = profile_id
        saved = self.db.get_profile(new_id)
        assert saved is not None
        return saved

    def assign_profile(self, mac: str, profile_id: int | None) -> dict:
        device = self._require(mac)
        if profile_id is None:
            previous = self.db.get_profile(device.get("profile_id"))
            board = device.get("board")
            if previous and board == previous.get("board"):
                board = ""
            self.db.set_assignment(mac, None, board, device.get("bin_id"))
        else:
            profile = self.db.get_profile(profile_id)
            if profile is None:
                raise FleetError("Profile not found.", 404)
            self.db.set_assignment(mac, profile_id, profile.get("board") or "", device.get("bin_id"))
        return self.public_device(self._require(mac))

    def assign_bin(self, mac: str, bin_id: int | None) -> dict:
        device = self._require(mac)
        if bin_id is not None and self.db.get_bin(bin_id) is None:
            raise FleetError("Firmware file not found.", 404)
        self.db.set_assignment(mac, device.get("profile_id"), device.get("board"), bin_id)
        return self.public_device(self._require(mac))

    async def github_releases(self, repo: str) -> list[dict]:
        cleaned = clean_repo(repo)
        cached = self._release_cache.get(cleaned)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        response = await self.http.get(
            f"https://api.github.com/repos/{cleaned}/releases",
            params={"per_page": 8},
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "wled-fleet",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=25,
        )
        if response.status_code == 404:
            raise FleetError(f"GitHub repository {cleaned} was not found.", 404)
        if response.status_code == 403:
            raise FleetError("GitHub refused the release list. Try again in a minute.", 502)
        if response.status_code >= 400:
            raise FleetError(f"GitHub returned {response.status_code} while listing releases.", 502)
        releases = []
        for release in response.json():
            assets = []
            for asset in release.get("assets") or []:
                name = asset.get("name") or ""
                url = asset.get("browser_download_url")
                if not name.lower().endswith(".bin") or not url:
                    continue
                assets.append(
                    {
                        "name": name,
                        "url": url,
                        "size": asset.get("size") or 0,
                        "chip_family": resolve_image_family(b"", name, None).family,
                        "board": extract_board(name),
                    }
                )
            if assets:
                releases.append(
                    {
                        "tag": release.get("tag_name"),
                        "name": release.get("name") or release.get("tag_name"),
                        "published": release.get("published_at"),
                        "assets": assets,
                    }
                )
        self._release_cache[cleaned] = (time.monotonic(), releases)
        return releases

    async def fetch_firmware(self, url: str, filename: str | None, declared: str | None) -> dict:
        if not allowed_firmware_url(url):
            raise FleetError("Firmware downloads are limited to GitHub release files.", 400)
        chunks: list[bytes] = []
        total = 0
        async with self.http.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=90,
            headers={"User-Agent": "wled-fleet"},
        ) as response:
            if response.status_code >= 400:
                raise FleetError(f"Download failed with status {response.status_code}.", 502)
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > 8 * 1024 * 1024:
                    raise FleetError("Firmware file is larger than 8 MB.", 413)
                chunks.append(chunk)
        data = b"".join(chunks)
        name = safe_filename(filename or url.rsplit("/", 1)[-1])
        return self.save_firmware(data, name, "github", url, declared)

    def start_job(self, kind: str, total: int, work: Callable[[Job], Awaitable[None]]) -> dict:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, status="queued", total=total)
        self.jobs[job.id] = job
        self._trim_jobs()
        self._jobs.put_nowait((job, work))
        return job.public()

    def _trim_jobs(self) -> None:
        if len(self.jobs) <= 40:
            return
        finished = [job for job in self.jobs.values() if job.status in {"done", "error"}]
        finished.sort(key=lambda job: job.created_at)
        for job in finished[: len(self.jobs) - 40]:
            self.jobs.pop(job.id, None)

    def get_job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise FleetError("That task is no longer on this server.", 404)
        return job.public()

    def queue_backup(self, macs: list[str]) -> dict:
        async def work(job: Job) -> None:
            for mac in macs:
                name = mac
                try:
                    device = self._require(mac)
                    name = device["name"]
                    result = await self.backup_device(mac, "manual")
                    job.results.append({"mac": mac, "name": name, "ok": True, "created": result.created})
                except FleetError as exc:
                    job.results.append({"mac": mac, "name": name, "ok": False, "error": str(exc)})
                job.done += 1

        return self.start_job("backup", len(macs), work)

    def queue_reboot(self, macs: list[str]) -> dict:
        async def work(job: Job) -> None:
            for mac in macs:
                name = mac
                try:
                    device = await self.reboot_device(mac)
                    name = device["name"]
                    job.results.append({"mac": mac, "name": name, "ok": True})
                except FleetError as exc:
                    job.results.append({"mac": mac, "name": name, "ok": False, "error": str(exc)})
                job.done += 1

        return self.start_job("reboot", len(macs), work)

    def queue_firmware(self, macs: list[str], bin_id: int, force: bool) -> dict:
        if force and len(macs) != 1:
            raise FleetError("Override is only allowed for one controller at a time.")

        async def work(job: Job) -> None:
            for mac in macs:
                name = mac
                try:
                    device = self._require(mac)
                    name = device["name"]
                    result = await self.update_firmware(mac, bin_id, force=force)
                    job.results.append({"mac": mac, "name": name, "ok": True, "version": result.get("version")})
                except FleetError as exc:
                    job.results.append({"mac": mac, "name": name, "ok": False, "error": str(exc)})
                job.done += 1

        return self.start_job("firmware", len(macs), work)

    def queue_restore(self, mac: str, backup_id: int) -> dict:
        async def work(job: Job) -> None:
            try:
                device = await self.restore(mac, backup_id)
                job.results.append({"mac": mac, "name": device["name"], "ok": True})
            except FleetError as exc:
                job.results.append({"mac": mac, "ok": False, "error": str(exc)})
            job.done = 1

        return self.start_job("restore", 1, work)


def resolve_ipv4(host: str) -> str:
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    except socket.gaierror:
        return host
    if not infos:
        return host
    return infos[0][4][0]
