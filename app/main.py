from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.auth import issue_session, password_ok, session_valid
from app.config import Settings
from app.db import Database
from app.diffutil import json_diff, mask_secrets
from app.discovery import normalize_mac
from app.errors import FleetError
from app.fleet import Fleet
from app.schemas import (
    BinAssignIn,
    CheckIn,
    ColorIn,
    FetchIn,
    FirmwareJobIn,
    FlagIn,
    LedsIn,
    LoginIn,
    MacsIn,
    ProfileAssignIn,
    ProfileIn,
    RenameIn,
    SecretsIn,
    SegmentsIn,
    SettingsIn,
    StateIn,
)

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
OPEN_PATHS = {"/health", "/api/login", "/api/session", "/manifest.webmanifest", "/sw.js", "/favicon.svg"}
COOKIE = "fleet_session"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)


def load_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes()
        if len(data) >= 16:
            return data
    data = secrets.token_bytes(32)
    path.write_bytes(data)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return data


def fleet_of(request: Request) -> Fleet:
    return request.app.state.fleet


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def signed_in(request: Request) -> bool:
    settings = settings_of(request)
    if not settings.admin_password:
        return True
    return session_valid(
        request.cookies.get(COOKIE),
        request.app.state.session_secret,
        settings.admin_password,
    )


def require_mac(mac: str, request: Request) -> str:
    normalized = normalize_mac(mac)
    if normalized is None or fleet_of(request).db.get_device(normalized) is None:
        raise HTTPException(status_code=404, detail="Unknown controller")
    return normalized


def public_backup(row: dict) -> dict:
    return {
        "id": row["id"],
        "mac": row["mac"],
        "created_at": row["created_at"],
        "content_hash": row["content_hash"],
        "reason": row["reason"],
        "version": row.get("version"),
        "name": row.get("name"),
        "led_count": row.get("led_count"),
        "byte_size": row["byte_size"],
    }


def parse_document(data: bytes) -> tuple[object | None, str | None]:
    try:
        return json.loads(data.decode("utf-8-sig")), None
    except (UnicodeError, json.JSONDecodeError):
        return None, data.decode("utf-8", errors="replace")[:20000]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.data_dir / "fleet.db")
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=2.0),
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=20),
            follow_redirects=False,
        )
        fleet = Fleet(db, settings, http)
        app.state.db = db
        app.state.http = http
        app.state.fleet = fleet
        app.state.session_secret = load_secret(settings.data_dir / "session.key")
        app.state.login_failures = {}
        tasks = [asyncio.create_task(fleet.run_jobs())]
        if settings.scheduler_enabled:
            tasks.append(asyncio.create_task(fleet.run_maintenance()))
            log.info("WLED Fleet %s listening for controllers", settings.app_version)
        try:
            yield
        finally:
            fleet.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await http.aclose()
            db.close()

    app = FastAPI(title="WLED Fleet", version=settings.app_version, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(FleetError)
    async def fleet_error(_request: Request, exc: FleetError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.middleware("http")
    async def protect(request: Request, call_next):
        path = request.url.path
        response = None
        if path.startswith("/static") or path in OPEN_PATHS or not settings.admin_password:
            response = await call_next(request)
        elif path == "/" or signed_in(request):
            response = await call_next(request)
        else:
            response = JSONResponse({"detail": "Sign in required"}, status_code=401)
        if path == "/" or path.startswith("/static") or path in {"/sw.js", "/manifest.webmanifest"}:
            response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/health")
    async def health(request: Request) -> dict:
        if getattr(request.app.state, "fleet", None) is None:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    async def service_worker() -> FileResponse:
        return FileResponse(STATIC / "sw.js", media_type="application/javascript")

    @app.get("/manifest.webmanifest")
    async def manifest() -> FileResponse:
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/session")
    async def session(request: Request) -> dict:
        required = bool(settings.admin_password)
        return {"required": required, "ok": (not required) or signed_in(request)}

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request) -> JSONResponse:
        if not settings.admin_password:
            return JSONResponse({"ok": True, "required": False})
        if not password_ok(body.password, settings.admin_password):
            request.app.state.login_failures[request.client.host if request.client else "?"] = (
                request.app.state.login_failures.get(request.client.host if request.client else "?", 0) + 1
            )
            raise HTTPException(status_code=401, detail="Wrong password")
        token = issue_session(request.app.state.session_secret, settings.admin_password)
        response = JSONResponse({"ok": True, "required": True})
        response.set_cookie(
            COOKIE,
            token,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
            max_age=60 * 60 * 24 * 30,
            path="/",
        )
        return response

    @app.post("/api/logout")
    async def logout() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/status")
    async def status(request: Request) -> dict:
        fleet = fleet_of(request)
        return {
            "version": settings.app_version,
            "auth": bool(settings.admin_password),
            "tls": settings.tls_enabled(),
            "mdns": fleet.mdns.ready,
            "mdns_error": fleet.mdns.error,
            "scan": fleet.last_scan,
        }

    @app.get("/api/devices")
    async def devices(request: Request) -> list[dict]:
        return fleet_of(request).list_public()

    @app.get("/api/devices/{mac}")
    async def device(request: Request, mac: str = Depends(require_mac)) -> dict:
        return fleet_of(request).public_device(fleet_of(request).db.get_device(mac))

    @app.get("/api/devices/{mac}/live")
    async def live(request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).live(mac)

    @app.post("/api/devices/{mac}/state")
    async def state(body: StateIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).set_state(mac, body.model_dump())

    @app.post("/api/devices/{mac}/color")
    async def color(body: ColorIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).set_color(mac, body.segment, [body.r, body.g, body.b], body.on)

    @app.post("/api/devices/{mac}/segments")
    async def segments(body: SegmentsIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        for item in body.segments:
            if item.stop <= item.start:
                raise HTTPException(status_code=400, detail="Segment end must be after the start.")
        return await fleet_of(request).set_segments(mac, [item.model_dump() for item in body.segments])

    @app.post("/api/devices/{mac}/leds")
    async def leds(body: LedsIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).set_leds(mac, [item.model_dump() for item in body.buses])

    @app.post("/api/devices/{mac}/rename")
    async def rename(body: RenameIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).rename(mac, body.name, body.mdns)

    @app.post("/api/devices/{mac}/identify")
    async def identify(request: Request, mac: str = Depends(require_mac)) -> dict:
        await fleet_of(request).identify(mac)
        return {"ok": True}

    @app.post("/api/devices/{mac}/reboot")
    async def reboot(request: Request, mac: str = Depends(require_mac)) -> dict:
        return await fleet_of(request).reboot_device(mac)

    @app.post("/api/devices/{mac}/backup")
    async def backup_one(request: Request, mac: str = Depends(require_mac)) -> dict:
        result = await fleet_of(request).backup_device(mac, "manual")
        return {"ok": True, "created": result.created, "id": result.backup_id}

    @app.post("/api/devices/{mac}/forget")
    async def forget(body: FlagIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        fleet = fleet_of(request)
        fleet.db.set_flags(mac, forgotten=body.on)
        device = fleet.db.get_device(mac)
        fleet.event("info", "device.forget", f"{'Forgot' if body.on else 'Restored'} {device['name']}", mac)
        return fleet.public_device(device)

    @app.post("/api/devices/{mac}/star")
    async def star(body: FlagIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        fleet = fleet_of(request)
        fleet.db.set_flags(mac, starred=body.on)
        return fleet.public_device(fleet.db.get_device(mac))

    @app.post("/api/devices/{mac}/profile")
    async def assign_profile(body: ProfileAssignIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return fleet_of(request).assign_profile(mac, body.profile_id)

    @app.post("/api/devices/{mac}/bin")
    async def assign_bin(body: BinAssignIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        return fleet_of(request).assign_bin(mac, body.bin_id)

    @app.post("/api/devices/{mac}/secrets")
    async def secrets_route(body: SecretsIn, request: Request, mac: str = Depends(require_mac)) -> dict:
        data = body.model_dump(exclude_unset=True)
        kwargs: dict = {
            "clear_pin": bool(data.get("clear_pin")),
            "clear_ota": bool(data.get("clear_ota")),
        }
        if "pin" in data:
            if not data["pin"]:
                kwargs["clear_pin"] = True
            else:
                kwargs["pin"] = data["pin"]
        if "ota_passphrase" in data:
            if not data["ota_passphrase"]:
                kwargs["clear_ota"] = True
            else:
                kwargs["ota_passphrase"] = data["ota_passphrase"]
        fleet_of(request).db.set_secrets(mac, **kwargs)
        return fleet_of(request).public_device(fleet_of(request).db.get_device(mac))

    @app.get("/api/devices/{mac}/backups")
    async def backups(request: Request, mac: str = Depends(require_mac)) -> list[dict]:
        return [public_backup(row) for row in fleet_of(request).db.list_backups(mac)]

    @app.get("/api/backups/{backup_id}")
    async def backup_detail(backup_id: int, request: Request) -> dict:
        fleet = fleet_of(request)
        row = fleet.db.get_backup(backup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        cfg, presets, info = fleet.backups.files(row)
        cfg_json, cfg_text = parse_document(cfg)
        presets_json, presets_text = parse_document(presets)
        info_json, _info_text = parse_document(info)
        return {
            **public_backup(row),
            "cfg": mask_secrets(cfg_json) if cfg_json is not None else None,
            "cfg_text": cfg_text,
            "presets": mask_secrets(presets_json) if presets_json is not None else None,
            "presets_text": presets_text,
            "info": info_json,
        }

    @app.get("/api/backups/{backup_id}/diff")
    async def backup_diff(backup_id: int, request: Request, against: int | None = None) -> dict:
        fleet = fleet_of(request)
        row = fleet.db.get_backup(backup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        other = fleet.db.get_backup(against) if against else fleet.db.previous_backup(row["mac"], backup_id)
        if other is None:
            return {"against": None, "changes": [], "note": "No earlier backup to compare."}
        left = _masked_pair(fleet, other)
        right = _masked_pair(fleet, row)
        changes = json_diff({"cfg": left[0], "presets": left[1]}, {"cfg": right[0], "presets": right[1]})
        truncated = len(changes) > 400
        return {
            "against": public_backup(other),
            "backup": public_backup(row),
            "changes": changes[:400],
            "truncated": truncated,
        }

    @app.get("/api/backups/{backup_id}/download")
    async def backup_download(backup_id: int, request: Request) -> Response:
        fleet = fleet_of(request)
        row = fleet.db.get_backup(backup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        filename = f"wled-{row['mac'].replace(':', '')}-{backup_id}.zip"
        return Response(
            content=fleet.backups.zip_bytes(row),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/backups/{backup_id}/restore")
    async def restore(backup_id: int, request: Request) -> dict:
        fleet = fleet_of(request)
        row = fleet.db.get_backup(backup_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Backup not found")
        return fleet.queue_restore(row["mac"], backup_id)

    @app.post("/api/bulk/backup")
    async def bulk_backup(body: MacsIn, request: Request) -> dict:
        return fleet_of(request).queue_backup(_macs(request, body.macs))

    @app.post("/api/bulk/reboot")
    async def bulk_reboot(body: MacsIn, request: Request) -> dict:
        return fleet_of(request).queue_reboot(_macs(request, body.macs))

    @app.post("/api/bulk/firmware")
    async def bulk_firmware(body: FirmwareJobIn, request: Request) -> dict:
        return fleet_of(request).queue_firmware(_macs(request, body.macs), body.bin_id, body.force)

    @app.post("/api/scan")
    async def scan(request: Request) -> dict:
        started = fleet_of(request).request_scan()
        return {"started": started}

    @app.get("/api/jobs/{job_id}")
    async def job(job_id: str, request: Request) -> dict:
        return fleet_of(request).get_job(job_id)

    @app.get("/api/firmware/bins")
    async def bins(request: Request) -> list[dict]:
        return [fleet_of(request).public_bin(row) for row in fleet_of(request).db.list_bins()]

    @app.post("/api/firmware/bins")
    async def upload_bin(
        request: Request,
        file: UploadFile = File(...),
        chip_family: str | None = Form(default=None),
    ) -> dict:
        data = await file.read()
        return fleet_of(request).save_firmware(data, file.filename or "firmware.bin", "upload", None, chip_family)

    @app.delete("/api/firmware/bins/{bin_id}")
    async def delete_bin(bin_id: int, request: Request) -> dict:
        fleet = fleet_of(request)
        row = fleet.db.get_bin(bin_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Firmware file not found")
        path = fleet.bin_path(row["sha256"])
        fleet.db.delete_bin(bin_id)
        if path.exists():
            path.unlink()
        return {"ok": True}

    @app.get("/api/firmware/profiles")
    async def profiles(request: Request) -> list[dict]:
        return fleet_of(request).db.list_profiles()

    @app.post("/api/firmware/profiles")
    async def create_profile(body: ProfileIn, request: Request) -> dict:
        return fleet_of(request).save_profile(body.model_dump())

    @app.post("/api/firmware/profiles/defaults")
    async def default_profiles(request: Request) -> list[dict]:
        return fleet_of(request).ensure_default_profiles()

    @app.patch("/api/firmware/profiles/{profile_id}")
    async def update_profile(profile_id: int, body: ProfileIn, request: Request) -> dict:
        return fleet_of(request).save_profile(body.model_dump(), profile_id)

    @app.delete("/api/firmware/profiles/{profile_id}")
    async def delete_profile(profile_id: int, request: Request) -> dict:
        fleet = fleet_of(request)
        if fleet.db.get_profile(profile_id) is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        fleet.db.delete_profile(profile_id)
        return {"ok": True}

    @app.post("/api/firmware/check")
    async def check(body: CheckIn, request: Request) -> dict:
        return {"devices": fleet_of(request).check_devices(_macs(request, body.macs), body.bin_id)}

    @app.get("/api/firmware/releases")
    async def releases(request: Request, repo: str) -> dict:
        return {"repo": repo, "releases": await fleet_of(request).github_releases(repo)}

    @app.post("/api/firmware/fetch")
    async def fetch_firmware(body: FetchIn, request: Request) -> dict:
        return await fleet_of(request).fetch_firmware(body.url, body.filename, body.chip_family)

    @app.get("/api/events")
    async def events(request: Request) -> list[dict]:
        return fleet_of(request).db.list_events()

    @app.get("/api/settings")
    async def get_settings(request: Request) -> dict:
        runtime = fleet_of(request).runtime()
        return {
            "scan_subnets": runtime.scan_subnets,
            "extra_hosts": runtime.extra_hosts,
            "backup_interval": runtime.backup_interval,
            "backup_watch_interval": runtime.backup_watch_interval,
            "backup_retention": runtime.backup_retention,
            "discovery_interval": runtime.discovery_interval,
            "presence_interval": runtime.presence_interval,
            "sources": runtime.sources,
            "auth_enabled": bool(settings.admin_password),
            "port": settings.port,
            "wled_github_repo": settings.wled_github_repo,
            "quinled_github_repo": settings.quinled_github_repo,
            "tls": settings.tls_enabled(),
        }

    @app.put("/api/settings")
    async def put_settings(body: SettingsIn, request: Request) -> dict:
        runtime = fleet_of(request).update_settings(body.model_dump(exclude_unset=True))
        return {
            "scan_subnets": runtime.scan_subnets,
            "extra_hosts": runtime.extra_hosts,
            "backup_interval": runtime.backup_interval,
            "backup_watch_interval": runtime.backup_watch_interval,
            "backup_retention": runtime.backup_retention,
            "discovery_interval": runtime.discovery_interval,
            "presence_interval": runtime.presence_interval,
            "sources": runtime.sources,
        }

    if (STATIC).exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _macs(request: Request, values: list[str]) -> list[str]:
    found = []
    for value in values:
        normalized = normalize_mac(value)
        if normalized is None or fleet_of(request).db.get_device(normalized) is None:
            raise HTTPException(status_code=404, detail=f"Unknown controller {value}")
        found.append(normalized)
    return found


def _masked_pair(fleet: Fleet, row: dict) -> tuple[object, object]:
    cfg, presets, _info = fleet.backups.files(row)
    cfg_json, _cfg_text = parse_document(cfg)
    presets_json, _presets_text = parse_document(presets)
    return mask_secrets(cfg_json), mask_secrets(presets_json)


app = create_app()


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    kwargs: dict = {
        "host": "0.0.0.0",
        "port": settings.port,
        "log_level": settings.log_level.lower(),
        "proxy_headers": True,
        "forwarded_allow_ips": "*",
    }
    if settings.tls_enabled():
        kwargs["ssl_certfile"] = str(settings.tls_cert)
        kwargs["ssl_keyfile"] = str(settings.tls_key)
        log.info("HTTPS enabled with %s", settings.tls_cert)
    uvicorn.run("app.main:app", **kwargs)
