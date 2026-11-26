# Linksys Velop Local for Home Assistant

Local control of a Linksys Velop MX-series mesh (built for the MX2000 / Atlas 6, compatible with other MX models) over the JNAP HTTP API. No cloud. Not affiliated with Linksys.

## Features

- Guided setup: host or IP, port (80, 443, 8443, …), admin password, **Use SSL** switch. The router certificate is **not verified**, so self-signed certificates work.
- Node picker: only the mesh nodes (primary and children) are listed, each with an area dropdown.
- Hub device (primary node) with child nodes linked underneath it.
- Works in router and bridge mode (client entities are created only in router mode).
- Add, remove, reconfigure (`Reconfigure`), re-authenticate and tune options (refresh intervals, included nodes, areas, presence tracking).
- Automatic refresh: fast tier (default 60 s: WAN, nodes, backhaul, clients) and slow tier (default 30 min: firmware, statistics), with back-off while the router is unreachable.

### Entities

| Platform | Entities |
|---|---|
| `sensor` | WAN status, WAN IP (disabled), operating mode, firmware, mesh node count, connected clients (router mode); per node: clients, IP, backhaul type/speed, signal (disabled), parent node; CPU and memory load (disabled) |
| `binary_sensor` | Internet (hub), connectivity (each child node) |
| `button` | Reboot (hub and each child node) |
| `update` | Firmware status per node (read-only) |
| `event` | Hub events (see below) |
| `device_tracker` | Only for clients you select in the options (or all, disabled by default) |

### Events for automations

The hub **Events** entity and the bus event `ha_linksys_velop_event` fire for: `wan_connected`, `wan_disconnected`, `node_online`, `node_offline`, `node_added`, `node_removed`, `firmware_update_available`, `mode_changed`, `authentication_failed`. A transition must be seen in two consecutive refreshes, and nothing fires for the state at startup.

Example: reboot the hub when the internet has been down for 5 minutes, at most every 30 minutes:

```yaml
automation:
  - alias: Reboot Velop after prolonged WAN loss
    triggers:
      - trigger: state
        entity_id: binary_sensor.living_room_internet
        to: "off"
        for: "00:05:00"
    conditions:
      - condition: template
        value_template: >
          {{ (now() - this.attributes.last_triggered | default(as_datetime('1970-01-01'), true)).total_seconds() > 1800 }}
    actions:
      - action: button.press
        target:
          entity_id: button.living_room_reboot
```

Actions: `ha_linksys_velop.reboot_node` (device target) and `ha_linksys_velop.refresh_now`.

## Install

Home Assistant 2026.11+: **Settings → Marketplace → Add from link** and enter this repository, then **Install**. Older versions: HACS custom repository, category *Integration*.

## Security notes

- The admin password lives in the config entry (`.storage`), which Home Assistant does not encrypt. Protect disk and backups. See [SECURITY.md](SECURITY.md).
- With **Use SSL** off the password travels unencrypted on your LAN.
- Client names, MACs and IPs are not exposed unless you enable presence tracking; diagnostics are redacted.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check . && mypy custom_components && pytest
```

Tests run against an emulated JNAP router (`tests/emulator/server.py`). Layout: `domain/` (pure models, ports), `infrastructure/` (JNAP transport, mapper, adapter), `application/` (events, onboarding, factory), presentation in the platform modules and `config_flow.py`.

## License

MIT
