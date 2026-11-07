from __future__ import annotations

from pydantic import BaseModel, Field


class LoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class StateIn(BaseModel):
    on: bool | None = None
    bri: int | None = Field(default=None, ge=0, le=255)
    ps: int | None = None


class ColorIn(BaseModel):
    r: int = Field(ge=0, le=255)
    g: int = Field(ge=0, le=255)
    b: int = Field(ge=0, le=255)
    segment: int = Field(default=0, ge=0, le=32)
    on: bool = True


class SegmentIn(BaseModel):
    id: int = Field(ge=0, le=32)
    start: int = Field(ge=0, le=8192)
    stop: int = Field(ge=0, le=8192)
    on: bool = True
    rev: bool = False


class SegmentsIn(BaseModel):
    segments: list[SegmentIn] = Field(min_length=1, max_length=32)


class BusIn(BaseModel):
    start: int = Field(ge=0, le=8192)
    len: int = Field(ge=1, le=8192)


class LedsIn(BaseModel):
    buses: list[BusIn] = Field(min_length=1, max_length=16)


class RenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    mdns: str | None = Field(default=None, max_length=32)


class FlagIn(BaseModel):
    on: bool


class ProfileAssignIn(BaseModel):
    profile_id: int | None = None


class BinAssignIn(BaseModel):
    bin_id: int | None = None


class SecretsIn(BaseModel):
    pin: str | None = Field(default=None, max_length=16)
    ota_passphrase: str | None = Field(default=None, max_length=64)
    clear_pin: bool = False
    clear_ota: bool = False


class MacsIn(BaseModel):
    macs: list[str] = Field(min_length=1, max_length=100)


class FirmwareJobIn(BaseModel):
    macs: list[str] = Field(min_length=1, max_length=100)
    bin_id: int
    force: bool = False


class CheckIn(BaseModel):
    macs: list[str] = Field(min_length=1, max_length=100)
    bin_id: int | None = None


class RestoreIn(BaseModel):
    backup_id: int


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    chip_family: str = Field(min_length=2, max_length=20)
    board: str = ""
    notes: str = ""
    bin_id: int | None = None


class FetchIn(BaseModel):
    url: str
    filename: str | None = None
    chip_family: str | None = None


class SettingsIn(BaseModel):
    scan_subnets: str | None = None
    extra_hosts: str | None = None
    backup_interval: int | None = None
    backup_watch_interval: int | None = None
    backup_retention: int | None = None
    discovery_interval: int | None = None
    presence_interval: int | None = None
    reset: bool = False


class LookIn(BaseModel):
    on: bool | None = None
    bri: int | None = Field(default=None, ge=0, le=255)
    col: list[list[int]] | None = None
    fx: int | None = Field(default=None, ge=0, le=255)
    pal: int | None = Field(default=None, ge=0, le=255)
    sx: int | None = Field(default=None, ge=0, le=255)
    ix: int | None = Field(default=None, ge=0, le=255)
    ps: int | None = None
    tt: int | None = Field(default=None, ge=0, le=255)


class ApplyIn(BaseModel):
    macs: list[str] = Field(min_length=1, max_length=100)
    look: LookIn
    sync: bool = False


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)


class MembersIn(BaseModel):
    macs: list[str] = Field(max_length=100)


class MemberIn(BaseModel):
    mac: str


class SceneIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    look: LookIn


class SceneApplyIn(BaseModel):
    macs: list[str] = Field(default_factory=list, max_length=100)
    group_id: int | None = None
    sync: bool = False


class SyncIn(BaseModel):
    enabled: bool
