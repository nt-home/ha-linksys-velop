"""Allow-list mapping of hostile JNAP payloads into domain models."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..domain.models import (
    Backhaul,
    Client,
    FirmwareStatus,
    HubIdentity,
    NetworkMode,
    Node,
    NodeRole,
    SpeedTestResult,
    SpeedTestState,
    SystemStats,
    WanStatus,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_MAX_ITEMS = 1000


def _str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:256]
    return None


def _dicts(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:_MAX_ITEMS] if isinstance(item, dict)]


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def map_identity(output: Mapping[str, Any]) -> HubIdentity | None:
    """Map core/GetDeviceInfo; return None when the serial is missing."""
    serial = _str(output.get("serialNumber"))
    if serial is None:
        return None
    services = output.get("services")
    return HubIdentity(
        serial=serial,
        model=_str(output.get("modelNumber")) or "Velop",
        firmware=_str(output.get("firmwareVersion")) or "unknown",
        hardware=_str(output.get("hardwareVersion")),
        manufacturer=_str(output.get("manufacturer")) or "Linksys",
        services=frozenset(
            s for s in (services if isinstance(services, list) else []) if _str(s)
        ),
    )


def is_primary_mode(output: Mapping[str, Any]) -> bool:
    """Return False only when GetDeviceMode explicitly says not-master."""
    mode = _str(output.get("mode"))
    return mode is None or mode.lower() == "master"


def map_wan(output: Mapping[str, Any]) -> WanStatus:
    """Map router/GetWANStatus including bridge-mode detection."""
    detected = _str(output.get("detectedWANType"))
    connection = output.get("wanConnection")
    connection = connection if isinstance(connection, dict) else {}
    wan_type = detected or _str(connection.get("wanType"))
    status = _str(output.get("wanStatus"))
    mode = NetworkMode.UNKNOWN
    if wan_type is not None:
        mode = (
            NetworkMode.BRIDGE if "bridge" in wan_type.lower() else NetworkMode.ROUTER
        )
    connected = None if status is None else status.lower() == "connected"
    return WanStatus(
        connected=connected,
        status=status.lower() if status else None,
        wan_type=wan_type,
        ip_address=_str(connection.get("ipAddress")),
        mode=mode,
    )


def _properties(device: Mapping[str, Any]) -> dict[str, str]:
    props: dict[str, str] = {}
    for prop in _dicts(device.get("properties")):
        name, value = _str(prop.get("name")), _str(prop.get("value"))
        if name and value:
            props[name] = value
    return props


def _connections(device: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return _dicts(device.get("connections"))


def _first_mac(device: Mapping[str, Any]) -> str | None:
    for iface in _dicts(device.get("knownInterfaces")):
        if mac := _str(iface.get("macAddress")):
            return mac.lower()
    macs = device.get("knownMACAddresses")
    if isinstance(macs, list) and macs and (mac := _str(macs[0])):
        return mac.lower()
    return None


def map_backhaul(output: Mapping[str, Any]) -> dict[str, Backhaul]:
    """Map GetBackhaulInfo keyed by node device id."""
    result: dict[str, Backhaul] = {}
    for item in _dicts(output.get("backhaulDevices")):
        device_id = _str(item.get("deviceUUID"))
        if device_id is None:
            continue
        wireless = item.get("wirelessConnectionInfo")
        wireless = wireless if isinstance(wireless, dict) else {}
        rssi = _num(wireless.get("stationRSSI"))
        speed = _num(item.get("speedMbps"))
        result[device_id] = Backhaul(
            connection_type=(_str(item.get("connectionType")) or "").lower() or None,
            speed_mbps=speed if speed else None,
            signal_dbm=int(rssi) if rssi is not None else None,
            parent_ip=_str(item.get("parentIPAddress")),
        )
    return result


def _node_name(props: Mapping[str, str], device: Mapping[str, Any], serial: str) -> str:
    name = props.get("userDeviceName") or _str(device.get("friendlyName"))
    if name:
        return name
    model = device.get("model")
    model_no = _str(model.get("modelNumber")) if isinstance(model, dict) else None
    return f"{model_no or 'Velop'} {serial[-4:]}"


def map_devices(
    output: Mapping[str, Any],
    backhaul: Mapping[str, Backhaul],
    *,
    include_clients: bool,
    primary_serial: str | None = None,
) -> tuple[tuple[Node, ...], tuple[Client, ...]]:
    """Split GetDevices into mesh nodes and (optionally) clients.

    Nodes are devices carrying a ``nodeType`` (older firmware) or whose model
    device type is ``Infrastructure`` (MX2000 firmware 1.1.x). The primary is
    the ``Master`` node, else the node whose serial matches ``primary_serial``,
    else the one flagged ``isAuthority``.
    """
    devices = _dicts(output.get("devices"))
    nodes: list[Node] = []
    client_devices: list[Mapping[str, Any]] = []
    for device in devices:
        if _is_node(device):
            if node := _map_node(device, backhaul, primary_serial):
                nodes.append(node)
        else:
            client_devices.append(device)
    counts: dict[str, int] = {}
    for device in client_devices:
        # Same rule as _map_clients: a client belongs to its first connection.
        conns = _connections(device)
        if conns and (parent := _str(conns[0].get("parentDeviceID"))):
            counts[parent] = counts.get(parent, 0) + 1
    nodes = [_with_count(n, counts) for n in nodes]
    clients = _map_clients(client_devices) if include_clients else ()
    return tuple(nodes), clients


def map_wireless_counts(output: Mapping[str, Any]) -> dict[str, int]:
    """Map GetNodesWirelessNetworkConnections to wireless clients per node.

    Bridge mode gives clients no parent node, but this action lists the Wi-Fi
    clients each node serves. Wired clients are not part of it.
    """
    counts: dict[str, int] = {}
    for item in _dicts(output.get("nodeWirelessConnections")):
        device_id = _str(item.get("deviceID"))
        if device_id is not None:
            counts[device_id] = len(_dicts(item.get("connections")))
    return counts


def with_client_counts(
    nodes: tuple[Node, ...], counts: Mapping[str, int] | None
) -> tuple[Node, ...]:
    """Replace each node's client count; None leaves them unknown."""
    return tuple(
        replace(n, client_count=None if counts is None else counts.get(n.device_id, 0))
        for n in nodes
    )


def _is_node(device: Mapping[str, Any]) -> bool:
    if _str(device.get("nodeType")) is not None:
        return True
    model = device.get("model")
    kind = _str(model.get("deviceType")) if isinstance(model, dict) else None
    return kind is not None and kind.lower() == "infrastructure"


def _role(
    device: Mapping[str, Any], serial: str, primary_serial: str | None
) -> NodeRole:
    node_type = (_str(device.get("nodeType")) or "").lower()
    if node_type == "master":
        return NodeRole.PRIMARY
    if node_type:
        return NodeRole.CHILD
    if primary_serial is not None:
        return NodeRole.PRIMARY if serial == primary_serial else NodeRole.CHILD
    return NodeRole.PRIMARY if device.get("isAuthority") is True else NodeRole.CHILD


def _with_count(node: Node, counts: Mapping[str, int]) -> Node:
    return replace(node, client_count=counts.get(node.device_id, 0))


def _map_node(
    device: Mapping[str, Any],
    backhaul: Mapping[str, Backhaul],
    primary_serial: str | None,
) -> Node | None:
    unit = device.get("unit")
    unit = unit if isinstance(unit, dict) else {}
    model = device.get("model")
    model = model if isinstance(model, dict) else {}
    serial = _str(unit.get("serialNumber"))
    device_id = _str(device.get("deviceID"))
    if serial is None or device_id is None:
        return None
    props = _properties(device)
    conns = _connections(device)
    role = _role(device, serial, primary_serial)
    link = backhaul.get(device_id)
    return Node(
        device_id=device_id,
        serial=serial,
        name=_node_name(props, device, serial),
        model=_str(model.get("modelNumber")) or "Velop",
        role=role,
        online=role is NodeRole.PRIMARY or bool(conns),
        firmware=_str(unit.get("firmwareVersion")),
        hardware=_str(model.get("hardwareVersion")),
        mac=_first_mac(device),
        ip_address=_str(conns[0].get("ipAddress")) if conns else None,
        parent_device_id=_str(conns[0].get("parentDeviceID")) if conns else None,
        backhaul=link,
    )


def _map_clients(devices: Iterable[Mapping[str, Any]]) -> tuple[Client, ...]:
    clients: list[Client] = []
    for device in devices:
        mac = _first_mac(device)
        if mac is None:
            continue
        conns = _connections(device)
        props = _properties(device)
        name = props.get("userDeviceName") or _str(device.get("friendlyName")) or mac
        interface_types = [
            _str(i.get("interfaceType")) for i in _dicts(device.get("knownInterfaces"))
        ]
        clients.append(
            Client(
                mac=mac,
                name=name,
                connected=bool(conns),
                connection_type=next((t for t in interface_types if t), None),
                node_device_id=_str(conns[0].get("parentDeviceID")) if conns else None,
                ip_address=_str(conns[0].get("ipAddress")) if conns else None,
            )
        )
    return tuple(clients)


def map_firmware(output: Mapping[str, Any]) -> dict[str, FirmwareStatus]:
    """Map nodes/firmwareupdate/GetFirmwareUpdateStatus keyed by device id."""
    result: dict[str, FirmwareStatus] = {}
    for item in _dicts(output.get("firmwareUpdateStatus")):
        device_id = _str(item.get("deviceUUID"))
        if device_id is None:
            continue
        update = item.get("availableUpdate")
        version = (
            _str(update.get("firmwareVersion")) if isinstance(update, dict) else None
        )
        result[device_id] = FirmwareStatus(
            available_version=version,
            update_pending=isinstance(item.get("pendingOperation"), dict),
        )
    return result


def map_stats(output: Mapping[str, Any]) -> SystemStats:
    """Map diagnostics/GetSystemStats."""
    return SystemStats(
        cpu_load=_num(output.get("CPULoad")),
        memory_load=_num(output.get("MemoryLoad")),
    )


_KBPS_PER_MBPS = 1000
_SPEED_PENDING_CODES = frozenset({"notavailable", "unavailable"})


def map_channel_scan(output: Mapping[str, Any]) -> bool | None:
    """Map nodes/setup/GetSelectedChannels to "is a channel scan running"."""
    running = output.get("isRunning")
    return running if isinstance(running, bool) else None


def _timestamp(value: object) -> datetime | None:
    text = _str(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _mbps(value: object) -> float | None:
    kbps = _num(value)
    return None if kbps is None else round(kbps / _KBPS_PER_MBPS, 2)


def _speed_item(item: Mapping[str, Any]) -> tuple[SpeedTestResult, bool] | None:
    """Map one health-check item; the flag is True while the test is running."""
    body = item.get("speedTestResult")
    if not isinstance(body, dict):
        return None
    code = _str(body.get("exitCode")) or "Unknown"
    result_id = _num(body.get("resultID"))
    latency = _num(body.get("latency", body.get("latencyMs")))
    result = SpeedTestResult(
        result_id=None if result_id is None else int(result_id),
        timestamp=_timestamp(item.get("timestamp")),
        exit_code=code,
        download_mbps=_mbps(body.get("downloadBandwidth")),
        upload_mbps=_mbps(body.get("uploadBandwidth")),
        latency_ms=latency,
        server_id=_str(body.get("serverID")),
    )
    return result, code.lower() in _SPEED_PENDING_CODES


def map_speed_test(
    status: Mapping[str, Any] | None, results: Mapping[str, Any] | None
) -> SpeedTestState | None:
    """Combine GetHealthCheckStatus and GetHealthCheckResults.

    The hub reports a running test with an exit code of ``NotAvailable`` and
    partly filled numbers. The newest item that is not pending is the latest
    finished test. Returns None when neither answer was usable.
    """
    if status is None and results is None:
        return None
    running = False
    if status is not None and (item := _speed_item(status)) is not None:
        running = item[1]
    latest: SpeedTestResult | None = None
    for raw in _dicts((results or {}).get("healthCheckResults")):
        mapped = _speed_item(raw)
        if mapped is None or mapped[1]:
            continue
        candidate = mapped[0]
        if latest is None or (candidate.result_id or 0) >= (latest.result_id or 0):
            latest = candidate
    return SpeedTestState(running=running, latest=latest)
