# Linksys Velop Local for Home Assistant
![Language](https://img.shields.io/badge/language-Python-blue) ![Version](https://img.shields.io/badge/version-0.3.0-informational) ![License](https://img.shields.io/badge/license-MIT-green) ![Build](https://img.shields.io/github/actions/workflow/status/nt-home/ha-linksys-velop/ci.yml)

Local control of a Linksys Velop MX-series mesh (built for the MX2000 / Atlas 6, compatible with other MX models) over the JNAP HTTP API. No cloud. Not affiliated with Linksys.

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Contributing](#contributing)
- [Tests](#tests)
- [License](#license)

## Features

- Guided setup: host or IP, port, admin password, **Use SSL** and **Verify SSL certificate**.
- **Use SSL** is on by default and follows the port: 80 turns it off, 443 and 8443 turn it on, any other port keeps your choice.
- **Verify SSL certificate** is off by default (the router uses a self-signed certificate); turn it on when a proxy with a valid certificate sits in front of the node. It only applies with **Use SSL**.
- The admin password is stored encrypted and decrypted only when a connection is built.
- All mesh nodes (primary and children) are added; the hub device has the child nodes linked underneath it.
- Works in router and bridge mode (the hub-wide client total, client events and device trackers exist only in router mode).
- Reconfigure, re-authenticate and tune options: refresh intervals, node names, areas, included nodes, client join and leave events, presence tracking.
- Per-node **Connected clients** count, refreshed on the **Status refresh** interval from the **Velop options**. In router mode it counts every client once, on the node it is connected to. In bridge mode the router does not say which node a wired client uses, so it counts only the Wi-Fi clients of each node (read from the router's per-node wireless connections).
- Optional **client joined / client left** events for each mesh node, for use in automations (off by default).
- Two refresh tiers: fast (default 60 s: WAN, nodes, backhaul, clients) and slow (default 30 min: firmware, statistics), with back-off while the router is unreachable.

| Platform         | Entities                                                                                                                                                                                                              |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor`         | WAN status, WAN IP (disabled), operating mode, firmware, mesh node count, connected clients (router mode); per node: connected clients (Wi-Fi only in bridge mode), IP, backhaul type/speed, signal (disabled), parent node; CPU and memory load (disabled) |
| `binary_sensor`  | Internet (hub), connectivity (each child node)                                                                                                                                                                        |
| `button`         | Reboot (hub and each child node)                                                                                                                                                                                      |
| `update`         | Firmware status per node (read-only)                                                                                                                                                                                  |
| `event`          | Hub events; per node client activity when client events are enabled (see API Reference)                                                                                                                               |
| `device_tracker` | Only for clients you select in the options (or all, disabled by default)                                                                                                                                              |

## Prerequisites

- Python >=3.14
- Home Assistant 2026.11 or later
- pip (test and development tools only)

## Installation

Home Assistant 2026.11+: open **Settings → Marketplace → Add from link**, enter this repository, then select **Install**. Older versions: add it as a HACS custom repository with category *Integration*.

For development, install the tooling:

```bash
pip install -r requirements_test.txt
```

## Configuration

Add the integration from **Settings → Devices & services**. Configuration files: `custom_components/ha_linksys_velop/manifest.json`, `custom_components/ha_linksys_velop/translations/en.json`.

- **Use SSL** off sends the admin password unencrypted over your network.
- The password is encrypted in the config entry; the key is a separate private file, `.storage/ha_linksys_velop.key`. If that key is lost, Home Assistant asks for the password again. Anyone who can read your whole `.storage` folder or a full backup can read both files, so protect disk and backups. See [SECURITY.md](SECURITY.md).
- Client names, MACs and IPs are not exposed unless you enable presence tracking or client join and leave events; diagnostics are redacted.
- In **Velop options**, open the **Clients** card and switch on **Client join and leave events**. The integration reloads and each node gets a **Client activity** event entity. In bridge mode the router reports no clients, so none is created.

## API Reference

Home Assistant actions: `ha_linksys_velop.reboot_node` (device target) and `ha_linksys_velop.refresh_now`.

The hub **Events** entity and the bus event `ha_linksys_velop_event` fire for `wan_connected`, `wan_disconnected`, `node_online`, `node_offline`, `node_added`, `node_removed`, `firmware_update_available`, `mode_changed` and `authentication_failed`. A transition must be seen in two consecutive refreshes, and nothing fires for the state at startup.

With **Client join and leave events** enabled, each node's **Client activity** entity and the same bus event also fire `client_joined` and `client_left` with `node_name`, `node_serial`, `client_name` and `client_mac`. The bus event's `device_id` is the node's device. A client that roams fires `client_left` on the old node and `client_joined` on the new one. Clients already connected at startup raise nothing. Events are checked on the **Status refresh** interval.

Example: notify when a device joins the Office node:

```yaml
automation:
  - alias: Device joined Office node
    triggers:
      - trigger: state
        entity_id: event.office_client_activity
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'client_joined' }}"
    actions:
      - action: notify.notify
        data:
          message: "{{ trigger.to_state.attributes.client_name }} joined the Office node"
```

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

## Contributing

Contributions are welcome. Fork the [repository](https://github.com/nt-home/ha-linksys-velop), create a branch, make your changes, and open a pull request.

## Tests

Run the linters, type check and test suite against an emulated JNAP router (`tests/emulator/server.py`):

```bash
ruff check . && ruff format --check . && mypy custom_components && pytest
```

The suite covers the domain, infrastructure and application layers, the config flow, password encryption, certificate verification and the platforms.

## License

This project is licensed under the [MIT](LICENSE) License.
