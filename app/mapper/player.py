"""Show players. xSchedule is the one this fleet calls. FPP is the same shape, not built.

xSchedule's REST API is two URLs on its web port:

- ``/xScheduleQuery?Query=GetPlayLists`` and ``GetPlayingStatus``
- ``/xScheduleCommand?Command=Play specified playlist&Parameters=<name>``
- ``/xScheduleCommand?Command=Stop specified playlist&Parameters=<name>``
"""

from __future__ import annotations

from typing import NoReturn, Protocol

import httpx

from app.errors import FleetError


class ShowPlayer(Protocol):
    kind: str

    def configured(self) -> bool: ...

    async def playlists(self) -> list[str]: ...

    async def start(self, playlist: str) -> dict: ...

    async def stop(self, playlist: str) -> dict: ...

    async def playing(self) -> str | None: ...


class XScheduleClient:
    kind = "xschedule"

    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self.base_url = base_url.rstrip("/")
        self.http = http

    def configured(self) -> bool:
        return bool(self.base_url)

    async def playlists(self) -> list[str]:
        payload = await self._get("/xScheduleQuery", {"Query": "GetPlayLists"})
        return playlist_names(payload)

    async def start(self, playlist: str) -> dict:
        return await self._command("Play specified playlist", playlist)

    async def stop(self, playlist: str) -> dict:
        if playlist:
            return await self._command("Stop specified playlist", playlist)
        return await self._command("Stop", "")

    async def playing(self) -> str | None:
        payload = await self._get("/xScheduleQuery", {"Query": "GetPlayingStatus"})
        return playing_name(payload)

    async def _command(self, command: str, parameters: str) -> dict:
        payload = await self._get("/xScheduleCommand", {"Command": command, "Parameters": parameters})
        if not isinstance(payload, dict):
            return {"result": "ok"}
        return payload

    async def _get(self, path: str, params: dict) -> object:
        if not self.configured():
            raise FleetError("No xSchedule URL is configured.", 409)
        try:
            response = await self.http.get(self.base_url + path, params=params, timeout=httpx.Timeout(8.0, connect=3.0))
        except httpx.HTTPError as exc:
            raise FleetError(f"Could not reach xSchedule. {exc.__class__.__name__}", 502) from exc
        if response.status_code >= 400:
            raise FleetError(f"xSchedule returned {response.status_code}.", 502)
        try:
            payload = response.json()
        except ValueError as exc:
            raise FleetError("xSchedule did not return JSON.", 502) from exc
        if isinstance(payload, dict):
            result = str(payload.get("result") or payload.get("Result") or "ok").lower()
            if result in {"failed", "fail", "error"}:
                message = payload.get("message") or payload.get("Message") or "xSchedule refused the command."
                raise FleetError(str(message), 502)
        return payload


class FppPlayer:
    """Falcon Player can run the same show from a Pi.

    TODO: call FPP's REST API (``GET /api/playlists``, ``POST /api/playlist/{name}/start``,
    ``GET /api/fppd/status``) when a show is played from FPP instead of xSchedule.
    The methods below are the same interface; they are not implemented.
    """

    kind = "fpp"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def configured(self) -> bool:
        return bool(self.base_url)

    async def playlists(self) -> list[str]:
        self._refuse()

    async def start(self, playlist: str) -> dict:
        self._refuse()

    async def stop(self, playlist: str) -> dict:
        self._refuse()

    async def playing(self) -> str | None:
        self._refuse()

    def _refuse(self) -> NoReturn:
        raise FleetError("FPP playback is not built yet. Point the player at xSchedule.", 501)


def player_for(kind: str, url: str, http: httpx.AsyncClient) -> ShowPlayer:
    if (kind or "").strip().lower() == "fpp":
        return FppPlayer(url)
    return XScheduleClient(url, http)


def playlist_names(payload: object) -> list[str]:
    raw: object = payload
    if isinstance(payload, dict):
        raw = payload.get("playlists") or payload.get("PlayLists") or payload.get("data") or []
        if isinstance(raw, dict):
            raw = raw.get("playlist") or raw.get("playlists") or []
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            names.append(item.strip())
        elif isinstance(item, dict):
            value = item.get("name") or item.get("Name")
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
    return names


def playing_name(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or payload.get("Status") or "")
    if status.lower() not in {"playing", "paused"}:
        return None
    for key in ("playlist", "PlayList", "Playlist", "playlistname"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
