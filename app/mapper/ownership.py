"""Who is allowed to stream to one controller.

The fleet and xLights both speak DDP. A board has one owner at a time:
fleet, xlights, or none. The renderer sends only while the owner is fleet.
Giving a board to xLights remembers the look that was on it. Giving it back
to the fleet restores that look.
"""

from __future__ import annotations

from dataclasses import dataclass

OWNERS = ("fleet", "xlights", "none")
HELD_SOURCES = ("hook", "phone", "manual")


@dataclass(frozen=True)
class OwnerState:
    owner: str
    source: str
    playlist: str | None
    previous_state: dict | None


@dataclass(frozen=True)
class OwnerChange:
    state: OwnerState
    snapshot: bool
    restore: bool


def decide(current: OwnerState, owner: str, source: str, playlist: str | None = None) -> OwnerChange:
    """Pure transition. snapshot means read the live look first. restore means write the saved one back."""
    if owner not in OWNERS:
        raise ValueError("Owner must be fleet, xlights, or none.")
    if current.owner == owner:
        kept = current.playlist if owner == "xlights" and not playlist else playlist
        if owner != "xlights":
            kept = None
        return OwnerChange(
            OwnerState(owner, source, kept if owner == "xlights" else None, current.previous_state),
            snapshot=False,
            restore=False,
        )
    previous = current.previous_state
    snapshot = False
    restore = False
    if owner == "xlights":
        snapshot = previous is None
    elif owner == "fleet":
        restore = previous is not None
        if restore:
            previous = None
    return OwnerChange(
        OwnerState(owner, source, playlist if owner == "xlights" else None, previous),
        snapshot=snapshot,
        restore=restore,
    )


def renderer_sends(owner: str) -> bool:
    return owner == "fleet"


def look_from_state(state: dict) -> dict:
    """The part of a WLED /json/state document that puts the same look back."""
    kept = {}
    for key in ("on", "bri", "transition", "ps", "pl", "mainseg", "seg", "udpn"):
        if key in state:
            kept[key] = state[key]
    return kept
