"""Tests for domain models and errors."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from custom_components.ha_linksys_velop.domain import errors
from custom_components.ha_linksys_velop.domain.models import (
    FastSnapshot,
    FirmwareStatus,
    HubIdentity,
    HubSnapshot,
    NetworkMode,
    Node,
    NodeRole,
    SlowSnapshot,
    SystemStats,
    WanStatus,
)


def _node(serial: str, role: NodeRole) -> Node:
    return Node(
        device_id=f"id-{serial}",
        serial=serial,
        name=serial,
        model="MX2000",
        role=role,
        online=True,
    )


def _snapshot(nodes: tuple[Node, ...], mode: NetworkMode = NetworkMode.ROUTER):
    return HubSnapshot(
        identity=HubIdentity("S1", "MX2000", "1.0"),
        wan=WanStatus(True, "connected", "DHCP", "203.0.113.9", mode),
        nodes=nodes,
        clients=(),
        firmware=MappingProxyType({}),
        stats=SystemStats(),
    )


def test_snapshot_mode_and_primary() -> None:
    """mode comes from WAN; primary picks the primary-role node."""
    child = _node("C1", NodeRole.CHILD)
    primary = _node("P1", NodeRole.PRIMARY)
    snap = _snapshot((child, primary), NetworkMode.BRIDGE)
    assert snap.mode is NetworkMode.BRIDGE
    assert snap.primary is primary


def test_snapshot_primary_absent() -> None:
    """primary is None without a primary node."""
    assert _snapshot((_node("C1", NodeRole.CHILD),)).primary is None
    assert _snapshot(()).primary is None


def test_node_by_serial() -> None:
    """node_by_serial indexes nodes."""
    a, b = _node("A", NodeRole.PRIMARY), _node("B", NodeRole.CHILD)
    assert _snapshot((a, b)).node_by_serial == {"A": a, "B": b}


def test_models_are_frozen_and_defaults() -> None:
    """Models are immutable with sensible defaults."""
    node = _node("A", NodeRole.PRIMARY)
    with pytest.raises(FrozenInstanceError):
        node.name = "x"  # type: ignore[misc]
    assert replace(node, online=False).online is False
    assert node.client_count is None
    assert HubIdentity("s", "m", "f").manufacturer == "Linksys"
    assert HubIdentity("s", "m", "f").services == frozenset()
    slow = SlowSnapshot()
    assert slow.firmware == {} and slow.stats == SystemStats()
    assert FastSnapshot(wan=_snapshot(()).wan, nodes=()).clients == ()
    assert FirmwareStatus(None).update_pending is False


def test_enums() -> None:
    """Enum string values are stable."""
    assert NetworkMode.ROUTER == "router"
    assert NodeRole.CHILD == "child"


def test_error_hierarchy() -> None:
    """Errors derive from VelopError with expected relationships."""
    assert issubclass(errors.ConnectionTimeoutError, errors.CannotConnectError)
    assert issubclass(errors.UnknownActionError, errors.JnapResultError)
    for cls in (
        errors.CannotConnectError,
        errors.UnsupportedDeviceError,
        errors.InvalidAuthError,
        errors.AccountLockedError,
        errors.JnapResultError,
        errors.NotPrimaryNodeError,
        errors.InvalidHostError,
    ):
        assert issubclass(cls, errors.VelopError)


def test_jnap_result_error_stores_result() -> None:
    """JnapResultError exposes the result code."""
    err = errors.UnknownActionError("_ErrorUnknownAction")
    assert err.result == "_ErrorUnknownAction"
    assert str(err) == "_ErrorUnknownAction"
