"""Tests for the allow-list JNAP mapper."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.ha_linksys_velop.domain.models import (
    NetworkMode,
    NodeRole,
)
from custom_components.ha_linksys_velop.infrastructure import mapper
from tests.emulator.server import client_device, node_device, three_node_mesh

HOSTILE: list[Any] = [None, 1, 1.5, True, "", "  ", [], {}, ["x"], {"a": 1}]


# identity -------------------------------------------------------------------


def test_identity_full() -> None:
    """A complete payload maps all fields."""
    ident = mapper.map_identity(
        {
            "serialNumber": " SER1 ",
            "modelNumber": "MX2000",
            "firmwareVersion": "1.2",
            "hardwareVersion": "2",
            "manufacturer": "Acme",
            "services": ["a", "b", "", 5, None, "a"],
        }
    )
    assert ident is not None
    assert ident.serial == "SER1"
    assert ident.model == "MX2000"
    assert ident.firmware == "1.2"
    assert ident.hardware == "2"
    assert ident.manufacturer == "Acme"
    assert ident.services == frozenset({"a", "b"})


def test_identity_defaults() -> None:
    """Missing optional fields fall back to defaults."""
    ident = mapper.map_identity({"serialNumber": "S"})
    assert ident is not None
    assert (ident.model, ident.firmware, ident.hardware) == ("Velop", "unknown", None)
    assert ident.manufacturer == "Linksys"
    assert ident.services == frozenset()


@pytest.mark.parametrize("bad", HOSTILE)
def test_identity_hostile_serial(bad: Any) -> None:
    """A non-string or blank serial yields None."""
    assert mapper.map_identity({"serialNumber": bad}) is None


@pytest.mark.parametrize("bad", HOSTILE)
def test_identity_hostile_others(bad: Any) -> None:
    """Hostile optional values never raise."""
    ident = mapper.map_identity(
        {
            "serialNumber": "S",
            "modelNumber": bad,
            "firmwareVersion": bad,
            "hardwareVersion": bad,
            "manufacturer": bad,
            "services": bad,
        }
    )
    assert ident is not None
    assert ident.model == "Velop"
    assert ident.firmware == "unknown"


def test_identity_truncates_long_strings() -> None:
    """Strings are capped at 256 characters."""
    ident = mapper.map_identity({"serialNumber": "x" * 1000})
    assert ident is not None
    assert len(ident.serial) == 256


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ({"mode": "Master"}, True),
        ({"mode": "master"}, True),
        ({}, True),
        ({"mode": ""}, True),
        ({"mode": 5}, True),
        ({"mode": "Slave"}, False),
        ({"mode": "unconfigured"}, False),
    ],
)
def test_is_primary_mode(output: dict[str, Any], expected: bool) -> None:
    """Only an explicit non-master mode is not primary."""
    assert mapper.is_primary_mode(output) is expected


# wan ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "mode", "wan_type"),
    [
        ({"detectedWANType": "DHCP"}, NetworkMode.ROUTER, "DHCP"),
        ({"detectedWANType": "Bridge"}, NetworkMode.BRIDGE, "Bridge"),
        ({"detectedWANType": "BRIDGED"}, NetworkMode.BRIDGE, "BRIDGED"),
        ({"wanConnection": {"wanType": "Static"}}, NetworkMode.ROUTER, "Static"),
        ({"wanConnection": {"wanType": "Bridge"}}, NetworkMode.BRIDGE, "Bridge"),
        (
            {"detectedWANType": "DHCP", "wanConnection": {"wanType": "Bridge"}},
            NetworkMode.ROUTER,
            "DHCP",
        ),
        ({}, NetworkMode.UNKNOWN, None),
        ({"detectedWANType": 5, "wanConnection": "x"}, NetworkMode.UNKNOWN, None),
    ],
)
def test_wan_mode_detection(
    output: dict[str, Any], mode: NetworkMode, wan_type: str | None
) -> None:
    """Bridge versus router is derived from detected type then connection type."""
    wan = mapper.map_wan(output)
    assert wan.mode is mode
    assert wan.wan_type == wan_type


def test_wan_status_and_ip() -> None:
    """Status is lower-cased and connected derived from it."""
    wan = mapper.map_wan(
        {
            "detectedWANType": "DHCP",
            "wanStatus": "Connected",
            "wanConnection": {"ipAddress": "203.0.113.9"},
        }
    )
    assert (wan.connected, wan.status, wan.ip_address) == (
        True,
        "connected",
        "203.0.113.9",
    )
    down = mapper.map_wan({"wanStatus": "Disconnected"})
    assert down.connected is False and down.status == "disconnected"
    none = mapper.map_wan({"wanStatus": 3})
    assert none.connected is None and none.status is None
    assert none.ip_address is None


# backhaul -------------------------------------------------------------------


def test_backhaul_mapping() -> None:
    """Backhaul entries map keyed by UUID with signal and speed."""
    result = mapper.map_backhaul(
        {
            "backhaulDevices": [
                {
                    "deviceUUID": "n2",
                    "connectionType": "Wireless",
                    "speedMbps": "866",
                    "parentIPAddress": "192.0.2.1",
                    "wirelessConnectionInfo": {"stationRSSI": -57.9},
                },
                {"deviceUUID": "n3", "connectionType": "Wired", "speedMbps": 0},
                {"deviceUUID": "n4"},
                {"connectionType": "Wired"},
                "junk",
            ]
        }
    )
    assert set(result) == {"n2", "n3", "n4"}
    assert result["n2"].connection_type == "wireless"
    assert result["n2"].speed_mbps == 866.0
    assert result["n2"].signal_dbm == -57
    assert result["n2"].parent_ip == "192.0.2.1"
    assert result["n3"].speed_mbps is None
    assert result["n3"].signal_dbm is None
    assert result["n4"].connection_type is None


@pytest.mark.parametrize("bad", HOSTILE)
def test_backhaul_hostile(bad: Any) -> None:
    """Hostile values never raise."""
    assert mapper.map_backhaul({"backhaulDevices": bad}) == {}
    result = mapper.map_backhaul(
        {
            "backhaulDevices": [
                {
                    "deviceUUID": "n",
                    "connectionType": bad,
                    "speedMbps": bad,
                    "parentIPAddress": bad,
                    "wirelessConnectionInfo": bad,
                }
            ]
        }
    )
    assert "n" in result
    assert result["n"].signal_dbm is None


# devices --------------------------------------------------------------------


def test_devices_nodes_vs_clients() -> None:
    """nodeType discriminates nodes from clients; clients are counted per node."""
    nodes, clients = mapper.map_devices(
        {"devices": three_node_mesh(with_clients=3)}, {}, include_clients=True
    )
    assert [n.serial for n in nodes] == [
        "12K10M12345678",
        "12K10M22222222",
        "12K10M33333333",
    ]
    assert nodes[0].role is NodeRole.PRIMARY
    assert nodes[1].role is NodeRole.CHILD
    # clients 1 and 3 -> node-2 ; client 2 -> node-1
    assert [n.client_count for n in nodes] == [1, 2, 0]
    assert len(clients) == 3
    assert clients[0].mac == "aa:bb:cc:00:00:01"
    assert clients[0].connected is True
    assert clients[0].node_device_id == "node-2"
    assert clients[0].connection_type == "Wireless"
    assert clients[0].ip_address == "192.0.2.11"


def test_devices_exclude_clients_still_counts() -> None:
    """include_clients False omits clients but keeps counts."""
    nodes, clients = mapper.map_devices(
        {"devices": three_node_mesh(with_clients=3)}, {}, include_clients=False
    )
    assert clients == ()
    assert sum(n.client_count or 0 for n in nodes) == 3


def test_disconnected_client() -> None:
    """A client without connections is offline and not counted."""
    nodes, clients = mapper.map_devices(
        {
            "devices": [
                node_device("n1", "SER1", "Master", "A"),
                client_device(1, "n1", connected=False),
            ]
        },
        {},
        include_clients=True,
    )
    assert nodes[0].client_count == 0
    assert clients[0].connected is False
    assert clients[0].node_device_id is None
    assert clients[0].ip_address is None


def test_node_fields_and_backhaul() -> None:
    """Node fields come from allow-listed keys and backhaul is attached."""
    backhaul = mapper.map_backhaul(
        {"backhaulDevices": [{"deviceUUID": "node-2", "connectionType": "Wired"}]}
    )
    nodes, _ = mapper.map_devices(
        {"devices": three_node_mesh(with_clients=0)}, backhaul, include_clients=True
    )
    child = nodes[1]
    assert child.name == "Bedroom"
    assert child.model == "MX2000"
    assert child.hardware == "1"
    assert child.firmware == "1.1.7.210469"
    assert child.mac == "00:11:22:33:44:22"
    assert child.ip_address == "192.0.2.2"
    assert child.parent_device_id == "node-1"
    assert child.online is True
    assert child.backhaul is not None
    assert child.backhaul.connection_type == "wired"
    assert nodes[0].backhaul is None


def test_node_offline_child_but_primary_always_online() -> None:
    """Children without connections are offline; the primary is always online."""
    nodes, _ = mapper.map_devices(
        {
            "devices": [
                node_device("n1", "SER1", "Master", "A", connected=False),
                node_device("n2", "SER2", "Slave", "B", connected=False),
            ]
        },
        {},
        include_clients=True,
    )
    assert nodes[0].online is True
    assert nodes[1].online is False
    assert nodes[1].ip_address is None
    assert nodes[1].parent_device_id is None


def test_node_type_case_and_unknown_type_is_child() -> None:
    """nodeType matching is case-insensitive; other types are children."""
    nodes, _ = mapper.map_devices(
        {
            "devices": [
                node_device("n1", "SER1", "MASTER", "A"),
                node_device("n2", "SER2", "Repeater", "B"),
            ]
        },
        {},
        include_clients=True,
    )
    assert [n.role for n in nodes] == [NodeRole.PRIMARY, NodeRole.CHILD]


def test_node_requires_serial_and_device_id() -> None:
    """Nodes missing a serial or deviceID are dropped."""
    no_serial = node_device("n1", "SER1", "Master", "A")
    no_serial["unit"] = {}
    no_id = node_device("n2", "SER2", "Slave", "B")
    del no_id["deviceID"]
    bad_unit = node_device("n3", "SER3", "Slave", "C")
    bad_unit["unit"] = "x"
    nodes, clients = mapper.map_devices(
        {"devices": [no_serial, no_id, bad_unit]}, {}, include_clients=True
    )
    assert nodes == () and clients == ()


def test_node_name_fallbacks() -> None:
    """Name falls back from userDeviceName to friendlyName to model+serial."""
    user = node_device("n1", "SER11111", "Master", "Friendly")
    user["properties"] = [{"name": "userDeviceName", "value": "Custom"}]
    friendly = node_device("n2", "SER22222", "Slave", "Friendly")
    friendly["properties"] = [{"name": "userDeviceName", "value": ""}]
    model = node_device("n3", "SER33333", "Slave", "")
    model["properties"] = []
    bare = node_device("n4", "SER44444", "Slave", "")
    bare["properties"] = "x"
    bare["model"] = "x"
    nodes, _ = mapper.map_devices(
        {"devices": [user, friendly, model, bare]}, {}, include_clients=True
    )
    assert [n.name for n in nodes] == [
        "Custom",
        "Friendly",
        "MX2000 3333",
        "Velop 4444",
    ]
    assert nodes[3].model == "Velop"


def test_mac_sources() -> None:
    """MAC comes from knownInterfaces, else knownMACAddresses, else none."""
    a = node_device("n1", "SER1", "Master", "A")
    a["knownInterfaces"] = [{"macAddress": ""}, {"macAddress": "AA:AA:AA:AA:AA:AA"}]
    b = node_device("n2", "SER2", "Slave", "B")
    del b["knownInterfaces"]
    b["knownMACAddresses"] = ["BB:BB:BB:BB:BB:BB"]
    c = node_device("n3", "SER3", "Slave", "C")
    del c["knownInterfaces"]
    c["knownMACAddresses"] = [5]
    d = node_device("n4", "SER4", "Slave", "D")
    del d["knownInterfaces"]
    d["knownMACAddresses"] = []
    nodes, _ = mapper.map_devices({"devices": [a, b, c, d]}, {}, include_clients=True)
    assert [n.mac for n in nodes] == [
        "aa:aa:aa:aa:aa:aa",
        "bb:bb:bb:bb:bb:bb",
        None,
        None,
    ]


def test_clients_without_mac_skipped_and_naming() -> None:
    """Clients need a MAC; names fall back to friendlyName then MAC."""
    no_mac = client_device(1, "n1")
    no_mac["knownInterfaces"] = []
    named = client_device(2, "n1")
    named["properties"] = [{"name": "userDeviceName", "value": "Phone"}]
    friendly = client_device(3, "n1")
    anon = client_device(4, "n1")
    anon["friendlyName"] = None
    _, clients = mapper.map_devices(
        {"devices": [no_mac, named, friendly, anon]}, {}, include_clients=True
    )
    assert [c.name for c in clients] == ["Phone", "client-3", "aa:bb:cc:00:00:04"]


def test_client_connection_type_first_non_empty() -> None:
    """connection_type is the first usable interfaceType."""
    dev = client_device(1, "n1")
    dev["knownInterfaces"] = [
        {"macAddress": "AA:AA:AA:AA:AA:01", "interfaceType": ""},
        {"macAddress": "AA:AA:AA:AA:AA:02", "interfaceType": "Wired"},
    ]
    _, clients = mapper.map_devices({"devices": [dev]}, {}, include_clients=True)
    assert clients[0].connection_type == "Wired"
    dev["knownInterfaces"] = [{"macAddress": "AA:AA:AA:AA:AA:01"}]
    _, clients = mapper.map_devices({"devices": [dev]}, {}, include_clients=True)
    assert clients[0].connection_type is None


@pytest.mark.parametrize("bad", HOSTILE)
def test_devices_hostile_container(bad: Any) -> None:
    """A non-list devices value yields nothing."""
    assert mapper.map_devices({"devices": bad}, {}, include_clients=True) == ((), ())


def test_devices_hostile_entries_and_fields() -> None:
    """Non-dict entries and wrongly typed fields are ignored."""
    weird_node = {
        "deviceID": "n1",
        "nodeType": "Master",
        "unit": {"serialNumber": "SER1"},
        "connections": ["x", None, {"ipAddress": 5, "parentDeviceID": []}],
        "properties": [None, {"name": 1, "value": 2}],
        "knownInterfaces": "nope",
    }
    weird_client = {
        "knownMACAddresses": ["AA:AA:AA:AA:AA:AA"],
        "connections": [{"parentDeviceID": "n1"}, {"parentDeviceID": "n1"}],
    }
    nodes, clients = mapper.map_devices(
        {"devices": [None, 5, "x", [], weird_node, weird_client]},
        {},
        include_clients=True,
    )
    assert len(nodes) == 1
    assert nodes[0].ip_address is None and nodes[0].mac is None
    assert nodes[0].client_count == 2
    assert len(clients) == 1 and clients[0].name == "aa:aa:aa:aa:aa:aa"


def test_nodetype_non_string_is_client() -> None:
    """A non-string nodeType does not make a device a node."""
    dev = client_device(1, "n1")
    dev["nodeType"] = 5
    nodes, clients = mapper.map_devices({"devices": [dev]}, {}, include_clients=True)
    assert nodes == () and len(clients) == 1


def test_item_cap() -> None:
    """Only the first 1000 list items are considered."""
    devices = [client_device(i % 200, "n1") for i in range(1500)]
    for i, dev in enumerate(devices):
        dev["knownInterfaces"] = [
            {"macAddress": f"00:00:00:00:{i // 256:02X}:{i % 256:02X}"}
        ]
    _, clients = mapper.map_devices({"devices": devices}, {}, include_clients=True)
    assert len(clients) == 1000
    fw = mapper.map_firmware(
        {"firmwareUpdateStatus": [{"deviceUUID": f"d{i}"} for i in range(1500)]}
    )
    assert len(fw) == 1000
    bh = mapper.map_backhaul(
        {"backhaulDevices": [{"deviceUUID": f"d{i}"} for i in range(1500)]}
    )
    assert len(bh) == 1000


# firmware / stats -----------------------------------------------------------


def test_firmware_mapping() -> None:
    """Firmware entries expose available version and pending flag."""
    result = mapper.map_firmware(
        {
            "firmwareUpdateStatus": [
                {
                    "deviceUUID": "n1",
                    "availableUpdate": {"firmwareVersion": "2.0"},
                    "pendingOperation": {"operation": "Downloading"},
                },
                {"deviceUUID": "n2", "availableUpdate": "x", "pendingOperation": "x"},
                {"deviceUUID": "n3"},
                {"availableUpdate": {"firmwareVersion": "3"}},
            ]
        }
    )
    assert set(result) == {"n1", "n2", "n3"}
    assert result["n1"].available_version == "2.0"
    assert result["n1"].update_pending is True
    assert result["n2"].available_version is None
    assert result["n2"].update_pending is False


@pytest.mark.parametrize("bad", HOSTILE)
def test_firmware_hostile(bad: Any) -> None:
    """Hostile container values yield nothing."""
    assert mapper.map_firmware({"firmwareUpdateStatus": bad}) == {}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12.5", 12.5),
        (7, 7.0),
        (0.5, 0.5),
        (True, None),
        ("abc", None),
        ("", None),
        (None, None),
        ([], None),
    ],
)
def test_stats_numbers(raw: Any, expected: float | None) -> None:
    """Numeric parsing accepts numbers and numeric strings only."""
    stats = mapper.map_stats({"CPULoad": raw, "MemoryLoad": raw})
    assert stats.cpu_load == expected
    assert stats.memory_load == expected


def test_stats_missing() -> None:
    """Missing stats are None."""
    stats = mapper.map_stats({})
    assert stats.cpu_load is None and stats.memory_load is None


# real MX2000 1.1.x firmware shape: no nodeType, nodes are Infrastructure ------


def _infra(device_id: str, serial: str, name: str, *, authority: bool = False) -> dict:
    return {
        "deviceID": device_id,
        "friendlyName": name,
        "isAuthority": authority,
        "knownMACAddresses": [f"AA:BB:CC:00:00:{serial[-2:]}"],
        "connections": [{"ipAddress": "192.0.2.5"}],
        "properties": [{"name": "userDeviceName", "value": name}],
        "model": {"deviceType": "Infrastructure", "modelNumber": "MX2000"},
        "unit": {"serialNumber": serial, "firmwareVersion": "1.1.7"},
    }


def test_infrastructure_nodes_without_node_type() -> None:
    """Four Infrastructure devices become four nodes; clients stay clients."""
    phone = {
        "deviceID": "p1",
        "knownMACAddresses": ["11:22:33:44:55:66"],
        "connections": [],
        "model": {"deviceType": "Phone"},
        "unit": {},
    }
    output = {
        "devices": [
            _infra("a", "S1", "Hall"),
            phone,
            _infra("b", "S2", "Dining"),
            _infra("c", "S3", "Study"),
            _infra("d", "S4", "Loft"),
        ]
    }
    nodes, clients = mapper.map_devices(
        output, {}, include_clients=True, primary_serial="S2"
    )
    assert [n.serial for n in nodes] == ["S1", "S2", "S3", "S4"]
    assert [n.role for n in nodes] == [
        NodeRole.CHILD,
        NodeRole.PRIMARY,
        NodeRole.CHILD,
        NodeRole.CHILD,
    ]
    assert [n.name for n in nodes] == ["Hall", "Dining", "Study", "Loft"]
    assert len(clients) == 1


def test_infrastructure_primary_from_authority_flag() -> None:
    """Without a known serial, isAuthority marks the primary."""
    output = {
        "devices": [_infra("a", "S1", "A"), _infra("b", "S2", "B", authority=True)]
    }
    nodes, _ = mapper.map_devices(output, {}, include_clients=False)
    assert [n.role for n in nodes] == [NodeRole.CHILD, NodeRole.PRIMARY]


def test_infrastructure_without_serial_is_dropped() -> None:
    """An Infrastructure device with no unit serial is not a mesh node."""
    dev = _infra("a", "S1", "A")
    dev["unit"] = {}
    nodes, clients = mapper.map_devices({"devices": [dev]}, {}, include_clients=True)
    assert (nodes, clients) == ((), ())
