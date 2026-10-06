"""Offline, immutable authority/source state machine; does not command devices.

Request IDs and revalidation signals must come from an authenticated adapter.
This module cannot itself prove person, heartbeat, ownership or physical safety.
"""
from dataclasses import dataclass, replace
from typing import Literal

from .gate import OWNERSHIP, BY_MEMBERS


@dataclass(frozen=True)
class Pending:
    kind: Literal['device', 'source']
    target: int | str
    request_id: str


@dataclass(frozen=True)
class Authority:
    ownership: str = 'manual'
    source: str = 'enfluri'
    inhibited: bool = True
    pending: Pending | None = None
    maintenance: bool = False


def advance(state: Authority, event: str, *, device: int | None = None,
            request_id: str | None = None, healthy: bool = False,
            handover: bool = False, primary_failed: bool = False,
            primary_ready: bool = False, alternate_ready: bool = False) -> Authority:
    """Fail-closed transitions; no implicit grant from mode, helper OFF or restart."""
    if not isinstance(state, Authority) or state.ownership not in OWNERSHIP or state.source not in ('enfluri', 'tibber'):
        return Authority()
    if event == 'restart':
        return replace(state, inhibited=True, pending=None)
    if event == 'maintenance_on':
        return replace(state, ownership='manual', inhibited=True, pending=None, maintenance=True)
    if event == 'maintenance_off':
        return replace(state, ownership='manual', inhibited=True, pending=None, maintenance=False)
    if event in ('manual', 'manual_helper_on'):
        owned = OWNERSHIP[state.ownership]
        remaining = owned - {device} if device in (1, 2) else frozenset()
        return replace(state, ownership=BY_MEMBERS[frozenset(remaining)], pending=None)
    if event == 'manual_helper_off':
        return state
    if event == 'revalidate':
        ready = (not state.maintenance and healthy is True and ((state.source == 'enfluri' and primary_ready is True)
                                      or (state.source == 'tibber' and alternate_ready is True)))
        return replace(state, inhibited=not ready)
    if event == 'request_device':
        if device in (1, 2) and not state.inhibited and not state.maintenance and isinstance(request_id, str) and request_id:
            return replace(state, pending=Pending('device', device, request_id))
        return state
    if event == 'request_fallback':
        if (state.source == 'enfluri' and primary_failed is True and
                isinstance(request_id, str) and request_id):
            return replace(state, pending=Pending('source', 'tibber', request_id))
        return state
    if event == 'request_return':
        if (state.source == 'tibber' and primary_ready is True and
                isinstance(request_id, str) and request_id):
            return replace(state, pending=Pending('source', 'enfluri', request_id))
        return state
    if event == 'reject':
        return replace(state, pending=None) if state.pending and state.pending.request_id == request_id else state
    if event == 'approve':
        pending = state.pending
        if pending is None or not request_id or request_id != pending.request_id:
            return state
        cleared = replace(state, pending=None)
        if healthy is not True or state.maintenance:
            return cleared
        if pending.kind == 'device':
            if (state.inhibited or handover is not True or pending.target not in (1, 2)):
                return cleared
            owned = OWNERSHIP[state.ownership] | {pending.target}
            return replace(cleared, ownership=BY_MEMBERS[frozenset(owned)])
        if pending.target == 'tibber' and state.source == 'enfluri':
            if primary_failed is True and alternate_ready is True:
                return replace(cleared, source='tibber', inhibited=True)
        if pending.target == 'enfluri' and state.source == 'tibber':
            if primary_ready is True:
                return replace(cleared, source='enfluri', inhibited=True)
        return cleared
    return state
