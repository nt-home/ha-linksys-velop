"""Emulated Velop JNAP endpoint used by integration and end-to-end tests."""

from __future__ import annotations

import asyncio
import base64
import ssl
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web
from aiohttp.test_utils import TestServer

BASE = "http://linksys.com/jnap/"
PASSWORD = "example-password"  # noqa: S105
PRIMARY_SERIAL = "12K10M12345678"


def node_device(
    device_id: str,
    serial: str,
    node_type: str,
    name: str,
    *,
    connected: bool = True,
    parent: str = "",
    ip: str = "192.0.2.1",
    ipv6: str = "",
) -> dict[str, Any]:
    """Build a GetDevices node entry."""
    connection: dict[str, Any] = {"ipAddress": ip, "parentDeviceID": parent}
    if ipv6:
        connection["ipv6Address"] = ipv6
    return {
        "deviceID": device_id,
        "friendlyName": name,
        "nodeType": node_type,
        "isAuthority": node_type == "Master",
        "knownInterfaces": [{"macAddress": f"00:11:22:33:44:{serial[-2:]}"}],
        "connections": [connection] if connected else [],
        "properties": [{"name": "userDeviceName", "value": name}],
        "model": {"modelNumber": "MX2000", "hardwareVersion": "1"},
        "unit": {"serialNumber": serial, "firmwareVersion": "1.1.7.210469"},
    }


def client_device(
    index: int, parent: str, *, connected: bool = True, kind: str = "Wireless"
) -> dict[str, Any]:
    """Build a GetDevices client entry."""
    return {
        "deviceID": f"client-{index}",
        "friendlyName": f"client-{index}",
        "knownInterfaces": [
            {"macAddress": f"AA:BB:CC:00:00:{index:02X}", "interfaceType": kind}
        ],
        "connections": (
            [{"ipAddress": f"192.0.2.{index + 10}", "parentDeviceID": parent}]
            if connected
            else []
        ),
        "properties": [],
    }


PUBLIC_ACTIONS = frozenset(
    {"core/GetDeviceInfo", "nodes/smartmode/GetDeviceMode", "router/GetWANStatus"}
)


@dataclass
class Emulator:
    """A scriptable router. Mutate attributes between refreshes."""

    password: str = PASSWORD
    wan_type: str = "DHCP"
    wan_status: str = "Connected"
    devices: list[dict[str, Any]] = field(default_factory=list)
    backhaul: list[dict[str, Any]] = field(default_factory=list)
    firmware: list[dict[str, Any]] = field(default_factory=list)
    wireless: list[dict[str, Any]] = field(default_factory=list)
    locked: bool = False
    mode: str = "master"
    unknown_actions: set[str] = field(default_factory=set)
    require_auth_for_info: bool = False
    raw_override: bytes | None = None
    status_override: int | None = None
    delay: float = 0.0
    drop_on_reboot: bool = False
    calls: list[tuple[str, bool]] = field(default_factory=list)
    reboots: list[dict[str, Any]] = field(default_factory=list)
    scan_running: bool = False
    scan_starts: int = 0
    scan_start_result: str = "OK"
    speed_results: list[dict[str, Any]] = field(default_factory=list)
    speed_running: bool = False
    speed_starts: int = 0
    uptime_seconds: int | None = 90061
    wan_ip: str = "203.0.113.9"
    reject_wrapped_transaction: bool = True
    _server: TestServer | None = None

    def __post_init__(self) -> None:
        """Default to a three-node router-mode mesh."""
        if not self.devices:
            self.devices = three_node_mesh()
        if not self.backhaul:
            self.backhaul = [
                {
                    "deviceUUID": "node-2",
                    "connectionType": "Wireless",
                    "speedMbps": 866,
                    "parentIPAddress": "192.0.2.1",
                    "wirelessConnectionInfo": {"stationRSSI": -57},
                },
                {"deviceUUID": "node-3", "connectionType": "Wired", "speedMbps": 1000},
            ]

    @property
    def port(self) -> int:
        """Return the listening port."""
        assert self._server is not None
        return self._server.port

    async def start(self, *, use_ssl: bool = False) -> None:
        """Start listening on localhost."""
        app = web.Application()
        app.router.add_post("/JNAP/", self._handle)
        ctx = None
        if use_ssl:
            ctx = _self_signed_context()
        self._server = TestServer(app, host="127.0.0.1")
        await self._server.start_server(ssl=ctx)

    async def stop(self) -> None:
        """Stop the server."""
        if self._server:
            await self._server.close()

    def _authorised(self, request: web.Request) -> bool:
        header = request.headers.get("X-JNAP-Authorization", "")
        expected = (
            "Basic " + base64.b64encode(f"admin:{self.password}".encode()).decode()
        )
        return header == expected

    def _action(self, action: str, authed: bool) -> dict[str, Any]:
        self.calls.append((action.removeprefix(BASE), authed))
        if action in self.unknown_actions:
            return {"result": "_ErrorUnknownAction"}
        name = action.removeprefix(BASE)
        needs_auth = name not in PUBLIC_ACTIONS or self.require_auth_for_info
        if needs_auth and not authed:
            if self.locked:
                return {"result": "ErrorAdminAccountLocked"}
            return {"result": "_ErrorUnauthorized"}
        handler = getattr(self, "_a_" + name.replace("/", "_"), None)
        if handler is None:
            return {"result": "_ErrorUnknownAction"}
        return handler()

    def _a_core_GetDeviceInfo(self) -> dict[str, Any]:
        return {
            "result": "OK",
            "output": {
                "manufacturer": "Linksys",
                "modelNumber": "MX2000",
                "serialNumber": PRIMARY_SERIAL,
                "firmwareVersion": "1.1.7.210469",
                "hardwareVersion": "1",
                "services": [f"{BASE}core/Core"],
            },
        }

    def _a_nodes_smartmode_GetDeviceMode(self) -> dict[str, Any]:
        return {"result": "OK", "output": {"mode": self.mode}}

    def _a_core_CheckAdminPassword(self) -> dict[str, Any]:
        return {"result": "OK", "output": {}}

    def _a_router_GetWANStatus(self) -> dict[str, Any]:
        return {
            "result": "OK",
            "output": {
                "detectedWANType": self.wan_type,
                "wanStatus": self.wan_status,
                "wanConnection": {"wanType": self.wan_type, "ipAddress": self.wan_ip},
            },
        }

    def _a_devicelist_GetDevices(self) -> dict[str, Any]:
        return {"result": "OK", "output": {"devices": self.devices}}

    def _a_nodes_diagnostics_GetBackhaulInfo(self) -> dict[str, Any]:
        return {"result": "OK", "output": {"backhaulDevices": self.backhaul}}

    def _a_nodes_networkconnections_GetNodesWirelessNetworkConnections(
        self,
    ) -> dict[str, Any]:
        return {"result": "OK", "output": {"nodeWirelessConnections": self.wireless}}

    def _a_nodes_firmwareupdate_GetFirmwareUpdateStatus(self) -> dict[str, Any]:
        return {"result": "OK", "output": {"firmwareUpdateStatus": self.firmware}}

    def _a_diagnostics_GetSystemStats(self) -> dict[str, Any]:
        output: dict[str, Any] = {"CPULoad": "12.5", "MemoryLoad": "40"}
        if self.uptime_seconds is not None:
            output["uptimeSeconds"] = self.uptime_seconds
        return {"result": "OK", "output": output}

    def _a_core_Reboot(self) -> dict[str, Any]:
        self.reboots.append({"action": "core/Reboot"})
        return {"result": "OK", "output": {}}

    def _a_core_Reboot2(self) -> dict[str, Any]:
        self.reboots.append({"action": "core/Reboot2"})
        return {"result": "OK", "output": {}}

    def _a_nodes_setup_GetSelectedChannels(self) -> dict[str, Any]:
        return {"result": "OK", "output": {"isRunning": self.scan_running}}

    def _a_nodes_setup_StartAutoChannelSelection(self) -> dict[str, Any]:
        if self.scan_start_result != "OK":
            return {"result": self.scan_start_result}
        self.scan_starts += 1
        self.scan_running = True
        return {"result": "OK", "output": {}}

    def finish_scan(self) -> None:
        """End the running channel scan."""
        self.scan_running = False

    def _pending_speed(self) -> dict[str, Any]:
        return {
            "timestamp": "2026-11-27T08:46:00Z",
            "healthCheckModule": "SpeedTest",
            "speedTestResult": {
                "resultID": self.speed_starts,
                "exitCode": "NotAvailable",
                "latency": 0,
                "downloadBandwidth": 0,
                "uploadBandwidth": 0,
                "serverID": "42",
            },
        }

    def _a_healthcheck_GetHealthCheckStatus(self) -> dict[str, Any]:
        output = self._pending_speed() if self.speed_running else {}
        return {"result": "OK", "output": output}

    def _a_healthcheck_GetHealthCheckResults(self) -> dict[str, Any]:
        items = list(self.speed_results)
        if self.speed_running:
            items.append(self._pending_speed())
        return {"result": "OK", "output": {"healthCheckResults": items}}

    def _a_healthcheck_RunHealthCheck(self) -> dict[str, Any]:
        self.speed_starts += 1
        self.speed_running = True
        return {"result": "OK", "output": {"resultID": self.speed_starts}}

    def finish_speed(
        self,
        download_kbps: int = 92000,
        upload_kbps: int = 95000,
        latency: int = 3,
        exit_code: str = "Success",
    ) -> None:
        """End the running speed check with the given numbers."""
        self.speed_running = False
        self.speed_results.append(
            {
                "timestamp": "2026-11-27T08:47:00Z",
                "healthCheckModule": "SpeedTest",
                "speedTestResult": {
                    "resultID": self.speed_starts,
                    "exitCode": exit_code,
                    "latency": latency,
                    "downloadBandwidth": download_kbps,
                    "uploadBandwidth": upload_kbps,
                    "serverID": "42",
                },
            }
        )

    async def _handle(self, request: web.Request) -> web.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status_override is not None:
            return web.Response(status=self.status_override)
        if self.raw_override is not None:
            return web.Response(body=self.raw_override, content_type="application/json")
        authed = self._authorised(request)
        action = request.headers.get("X-JNAP-Action", "")
        body = await request.json()
        if action == f"{BASE}core/Transaction":
            if not isinstance(body, list) and self.reject_wrapped_transaction:
                return web.json_response({"result": "_ErrorInvalidInput"})
            # Like the real hub, a transaction is authenticated by its first
            # action: a public first action leaves the rest unauthenticated.
            first = str(body[0]["action"]).removeprefix(BASE) if body else ""
            effective = authed and first not in PUBLIC_ACTIONS
            responses = [self._action(item["action"], effective) for item in body]
            top = "OK" if all(r["result"] == "OK" for r in responses) else "Error"
            return web.json_response({"result": top, "responses": responses})
        if self.drop_on_reboot and action.endswith("Reboot"):
            self.reboots.append({"action": "dropped"})
            assert request.transport is not None
            request.transport.close()
            return web.Response()
        return web.json_response(self._action(action, authed))


def three_node_mesh(*, with_clients: int = 3) -> list[dict[str, Any]]:
    """Return a primary plus two child nodes and some clients."""
    devices = [
        node_device("node-1", PRIMARY_SERIAL, "Master", "Living Room", ip="192.0.2.1"),
        node_device(
            "node-2",
            "12K10M22222222",
            "Slave",
            "Bedroom",
            parent="node-1",
            ip="192.0.2.2",
        ),
        node_device(
            "node-3",
            "12K10M33333333",
            "Slave",
            "Office",
            parent="node-1",
            ip="192.0.2.3",
        ),
    ]
    devices += [
        client_device(i, "node-2" if i % 2 else "node-1")
        for i in range(1, with_clients + 1)
    ]
    return devices


def _self_signed_context() -> ssl.SSLContext:
    import datetime  # noqa: PLC0415
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "velop.invalid")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    with tempfile.TemporaryDirectory() as tmp:
        cert_path = Path(tmp) / "c.pem"
        key_path = Path(tmp) / "k.pem"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)
    return ctx
