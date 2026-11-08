"""Infer a show from WLED's realtime flag when xSchedule has not called the hook.

/json/info reports live and lip, the IP of the last realtime sender. The fleet's
own DDP stream uses this server's address, so that is not a show. A different
address is. When live ends, a show that this fallback started is handed back.
A show that xSchedule or the phone started is left for that signal to stop.
"""

from __future__ import annotations

import socket

from app.mapper.ownership import HELD_SOURCES, OwnerState


def source_address(ip: str, port: int) -> str:
    """The address a UDP packet to this board would leave from. That is what WLED stores in lip."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return ""
    try:
        probe.connect((ip, port or 9))
        return str(probe.getsockname()[0])
    except OSError:
        return ""
    finally:
        probe.close()


def local_addresses() -> set[str]:
    """Addresses WLED would report in lip if the packet came from this machine."""
    found = {"127.0.0.1", "::1", "0.0.0.0"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    return {item for item in found if item}


def foreign_live(info: dict, local_ips: set[str]) -> bool:
    if not isinstance(info, dict) or info.get("live") is not True:
        return False
    lip = info.get("lip")
    if not isinstance(lip, str):
        return False
    sender = lip.strip()
    if not sender or sender in local_ips:
        return False
    return True


def inferred_owner(info: dict, local_ips: set[str], state: OwnerState) -> str | None:
    """The owner the fallback wants, or None to leave the board alone."""
    live = foreign_live(info, local_ips)
    if state.owner == "xlights" and state.source in HELD_SOURCES:
        return None
    if live and state.owner != "xlights":
        return "xlights"
    if not live and state.owner == "xlights" and state.source == "inferred":
        return "fleet"
    return None


def confirm(streak: int, proposal: str | None, needed: int = 2) -> tuple[int, str | None]:
    """Require the same inference twice so one late packet does not flap the owner."""
    if proposal is None:
        return 0, None
    count = streak + 1
    if count >= needed:
        return 0, proposal
    return count, None
