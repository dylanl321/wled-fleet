"""HTTP API for WLED Mapper. Paths sit under /api/mapper so the existing session gate covers them."""

from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.errors import FleetError
from app.mapper.service import MapperService


def mapper_of(request: Request) -> MapperService:
    service = getattr(request.app.state, "mapper", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Mapper is not ready")
    return service


def mount_mapper(app) -> None:
    router = APIRouter(prefix="/api/mapper")

    @router.get("/controllers")
    async def controllers(request: Request) -> dict:
        return {"controllers": mapper_of(request).controllers()}

    @router.post("/controllers/{controller_id}/owner")
    async def set_owner(controller_id: str, request: Request) -> dict:
        body = await _body(request)
        owner = body.get("owner")
        if not isinstance(owner, str):
            raise HTTPException(status_code=400, detail="owner is required")
        playlist = body.get("playlist") if isinstance(body.get("playlist"), str) else None
        return await mapper_of(request).set_owner(controller_id, owner, "manual", playlist)

    @router.api_route("/hooks/xschedule", methods=["GET", "POST"])
    async def xschedule_hook(request: Request) -> dict:
        payload: dict = {}
        if request.method == "POST":
            raw = await request.body()
            if raw:
                try:
                    loaded = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise HTTPException(status_code=400, detail="Body must be JSON") from exc
                if not isinstance(loaded, dict):
                    raise HTTPException(status_code=400, detail="Body must be a JSON object")
                payload = loaded
        event = payload.get("event") or request.query_params.get("event") or request.query_params.get("Event")
        playlist = payload.get("playlist") or request.query_params.get("playlist") or request.query_params.get("Parameters") or ""
        if not isinstance(event, str) or not event.strip():
            raise HTTPException(status_code=400, detail="event is required")
        if not isinstance(playlist, str):
            playlist = ""
        return await mapper_of(request).xschedule_hook(event, playlist)

    @router.post("/shows/{playlist}/start")
    async def start_show(playlist: str, request: Request) -> dict:
        return await mapper_of(request).start_show(playlist)

    @router.post("/shows/{playlist}/stop")
    async def stop_show(playlist: str, request: Request) -> dict:
        return await mapper_of(request).stop_show(playlist)

    @router.post("/xlights/sync-controllers")
    async def sync_controllers(request: Request) -> dict:
        body = await _optional_body(request)
        show = body.get("show") if isinstance(body.get("show"), str) else None
        return mapper_of(request).sync_controllers(body.get("apply") is True, show)

    @router.get("/settings")
    async def mapper_settings(request: Request) -> dict:
        return mapper_of(request).mapper_settings()

    @router.put("/settings")
    async def put_mapper_settings(request: Request) -> dict:
        return mapper_of(request).update_mapper_settings(await _body(request))

    @router.get("/fixtures")
    async def list_fixtures(request: Request) -> dict:
        return {"fixtures": mapper_of(request).list_fixtures()}

    @router.post("/import")
    async def import_fixture(request: Request) -> dict:
        body = await _body(request)
        text = body.get("text")
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="text is required")
        name = body.get("name") if isinstance(body.get("name"), str) else ""
        filename = body.get("filename") if isinstance(body.get("filename"), str) else ""
        controller = body.get("controllerId") if isinstance(body.get("controllerId"), str) else ""
        output = body.get("outputIndex", 0)
        if isinstance(output, bool) or not isinstance(output, int):
            raise HTTPException(status_code=400, detail="outputIndex must be an integer")
        return mapper_of(request).import_document(
            text,
            filename,
            name,
            controller,
            output,
            _scale(body.get("scaleMetresPerUnit")),
            body.get("apply") is True,
            body.get("replacePhoneCapture") is True,
        )

    @router.post("/fixtures/import")
    async def import_fixture_spec(request: Request) -> dict:
        body = await _body(request)
        source = body.get("source", body.get("text"))
        if isinstance(source, dict):
            text = source.get("text")
            filename = source.get("filename") if isinstance(source.get("filename"), str) else ""
            name = source.get("name") if isinstance(source.get("name"), str) else ""
        else:
            text = source
            filename = body.get("filename") if isinstance(body.get("filename"), str) else ""
            name = body.get("name") if isinstance(body.get("name"), str) else ""
        if not isinstance(text, str):
            raise HTTPException(status_code=400, detail="source text is required")
        controller = body.get("controllerId") if isinstance(body.get("controllerId"), str) else ""
        output = body.get("outputIndex", 0)
        if isinstance(output, bool) or not isinstance(output, int):
            raise HTTPException(status_code=400, detail="outputIndex must be an integer")
        return mapper_of(request).import_document(
            text,
            filename,
            name,
            controller,
            output,
            _scale(body.get("scaleMetresPerUnit")),
            body.get("apply") is True,
            body.get("replacePhoneCapture") is True,
        )

    @router.post("/fixtures")
    async def create_fixture(request: Request) -> dict:
        body = await _body(request)
        body.setdefault("id", str(uuid.uuid4()))
        body.setdefault("schemaVersion", 1)
        return mapper_of(request).save_fixture(body)

    @router.get("/fixtures/{fixture_id}")
    async def get_fixture(fixture_id: str, request: Request) -> dict:
        return mapper_of(request).get_fixture(fixture_id)

    @router.put("/fixtures/{fixture_id}")
    async def put_fixture(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        body["id"] = fixture_id
        body.setdefault("schemaVersion", 1)
        return mapper_of(request).save_fixture(body)

    @router.delete("/fixtures/{fixture_id}")
    async def delete_fixture(fixture_id: str, request: Request) -> dict:
        mapper_of(request).delete_fixture(fixture_id)
        return {"ok": True}

    @router.get("/fixtures/{fixture_id}/pixelmap")
    async def pixelmap(fixture_id: str, request: Request):
        if request.query_params.get("format") == "csv":
            text, media, filename = mapper_of(request).export(fixture_id, "csv", 1, 1, 1)
            return Response(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
        return mapper_of(request).pixelmap(fixture_id)

    @router.post("/fixtures/{fixture_id}/normalise")
    async def normalise(fixture_id: str, request: Request) -> dict:
        return mapper_of(request).normalise(fixture_id)

    @router.post("/fixtures/{fixture_id}/runs/{run_id}/scale-pitch")
    async def scale_pitch(fixture_id: str, run_id: str, request: Request) -> dict:
        return mapper_of(request).scale_pitch(fixture_id, run_id)

    @router.post("/fixtures/{fixture_id}/detect")
    async def begin_detect(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        return await mapper_of(request).begin_detect(fixture_id, body.get("runId"), body.get("mode") or "binary")

    @router.post("/fixtures/{fixture_id}/detect/{session_id}/finish")
    async def finish_detect(fixture_id: str, session_id: str, request: Request) -> dict:
        return await mapper_of(request).finish_detect(fixture_id, session_id)

    @router.post("/fixtures/{fixture_id}/detect/{session_id}")
    async def step_detect(fixture_id: str, session_id: str, request: Request) -> dict:
        body = await _body(request)
        return await mapper_of(request).step_detect(fixture_id, session_id, body.get("step"))

    @router.post("/fixtures/{fixture_id}/highlight")
    async def highlight(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        controller = body.get("controller")
        if not isinstance(controller, str) or not controller:
            raise HTTPException(status_code=400, detail="controller is required")
        output = body.get("output", 0)
        index = body.get("index")
        if isinstance(output, bool) or not isinstance(output, int):
            raise HTTPException(status_code=400, detail="output must be an integer")
        if isinstance(index, bool) or not isinstance(index, int):
            raise HTTPException(status_code=400, detail="index must be an integer")
        return await mapper_of(request).highlight(fixture_id, controller, output, index)

    @router.post("/fixtures/{fixture_id}/effect")
    async def effect(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        name = body.get("name")
        if not isinstance(name, str):
            raise HTTPException(status_code=400, detail="name is required")
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        return await mapper_of(request).start_effect(fixture_id, name, params)

    @router.post("/fixtures/{fixture_id}/stop")
    async def stop(fixture_id: str, request: Request) -> dict:
        return await mapper_of(request).stop_effect(fixture_id)

    @router.post("/fixtures/{fixture_id}/ledmap")
    async def ledmap(fixture_id: str, request: Request) -> dict:
        return await mapper_of(request).push_ledmap(fixture_id, await _body(request))

    @router.get("/fixtures/{fixture_id}/export")
    async def export(fixture_id: str, request: Request) -> Response:
        fmt = request.query_params.get("format") or "json"
        width = _positive(request.query_params.get("width"), 16)
        height = _positive(request.query_params.get("height"), 16)
        depth = _positive(request.query_params.get("depth"), 1)
        text, media, filename = mapper_of(request).export(fixture_id, fmt, width, height, depth)
        return Response(text, media_type=media, headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @router.get("/status")
    async def status(request: Request) -> dict:
        service = mapper_of(request)
        return {**service.status, **service._show_fields()}

    @router.get("/live")
    async def live(request: Request) -> StreamingResponse:
        service = mapper_of(request)

        async def events():
            yield _event(service.status)
            queue = service.subscribe()
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield _event(item)
            finally:
                service.unsubscribe(queue)

        return StreamingResponse(events(), media_type="text/event-stream")

    app.include_router(router)


async def _optional_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return payload


async def _body(request: Request) -> dict:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return payload


def _event(payload: dict) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _point(body: dict, key: str) -> list:
    value = body.get(key)
    if not isinstance(value, list) or len(value) != 3 or not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
        raise ValueError(key)
    return value


async def _optional_body(request: Request) -> dict:
    raw = await request.body()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return payload


def _scale(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise HTTPException(status_code=400, detail="scaleMetresPerUnit must be a number")
    return float(raw)


def _positive(raw: str | None, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise FleetError("width, height, and depth must be integers.", 400) from exc
    if value < 1:
        raise FleetError("width, height, and depth must be at least 1.", 400)
    return value
