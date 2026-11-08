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

    @router.get("/fixtures")
    async def list_fixtures(request: Request) -> dict:
        return {"fixtures": mapper_of(request).list_fixtures()}

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

    @router.post("/fixtures/{fixture_id}/detect/plan")
    async def detect_plan(fixture_id: str, request: Request) -> dict:
        body = await _optional_body(request)
        mode = body.get("mode") or "binary"
        if not isinstance(mode, str):
            raise HTTPException(status_code=400, detail="mode must be binary or single")
        luma = body.get("luma")
        if luma is not None and not isinstance(luma, (int, float)):
            raise HTTPException(status_code=400, detail="luma must be a number")
        return mapper_of(request).detection_plan(fixture_id, mode, None if luma is None else float(luma))

    @router.post("/fixtures/{fixture_id}/detect/frame")
    async def detect_frame(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        pixels = body.get("pixels")
        if not isinstance(pixels, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in pixels):
            raise HTTPException(status_code=400, detail="pixels must be a list of indexes")
        return await mapper_of(request).show_detection(fixture_id, pixels)

    @router.post("/fixtures/{fixture_id}/detect/accept")
    async def detect_accept(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        corrections = body.get("corrections")
        if not isinstance(corrections, list):
            raise HTTPException(status_code=400, detail="corrections must be a list")
        seen = body.get("seen")
        if seen is not None and (
            not isinstance(seen, list) or not all(isinstance(item, int) and not isinstance(item, bool) for item in seen)
        ):
            raise HTTPException(status_code=400, detail="seen must be a list of indexes")
        return mapper_of(request).accept_detection(fixture_id, corrections, seen)

    @router.post("/import")
    async def import_map(request: Request) -> dict:
        body = await _body(request)
        fmt = body.get("format")
        text = body.get("text")
        name = body.get("name") or "Imported"
        if not isinstance(fmt, str) or not isinstance(text, str) or not isinstance(name, str):
            raise HTTPException(status_code=400, detail="format, name, and text are required")
        return mapper_of(request).import_map(fmt, name, text)

    @router.post("/fixtures/{fixture_id}/align")
    async def align(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        try:
            return mapper_of(request).align_session(
                fixture_id, _point(body, "sourceA"), _point(body, "sourceB"), _point(body, "destA"), _point(body, "destB")
            )
        except FleetError:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Each reference needs [x, y, z].") from exc

    @router.post("/fixtures/{fixture_id}/drift")
    async def drift(fixture_id: str, request: Request) -> dict:
        body = await _body(request)
        try:
            return mapper_of(request).spread_drift(fixture_id, _point(body, "error"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="error must be [x, y, z].") from exc

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
        return mapper_of(request).status

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
