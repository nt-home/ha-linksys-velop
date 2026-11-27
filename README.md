# Linksys Velop for Home Assistant
![Language](https://img.shields.io/badge/language-Python-blue) ![Version](https://img.shields.io/badge/version-0.5.1-informational) ![License](https://img.shields.io/badge/license-MIT-green) ![Build](https://img.shields.io/github/actions/workflow/status/nt-home/ha-linksys-velop/ci.yml)

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
- **Use SSL** is on by default. The port never changes it: the values you enter are used as given. An empty port means 443 with SSL, or 80 without.
- **Verify SSL certificate** is off by default (the router uses a self-signed certificate); turn it on when a proxy with a valid certificate sits in front of the node. It only applies with **Use SSL**.
- The admin password is stored encrypted and decrypted only when a connection is built.
- All mesh nodes (primary and children) are added; the hub device has the child nodes linked underneath it.
- Works in router and bridge mode (the hub-wide client total, client events and device trackers exist only in router mode).
- Reconfigure, re-authenticate and tune options: refresh intervals, node names, areas, included nodes, client join and leave events, presence tracking.
- Per-node **Network clients** count, refreshed on the **Status refresh** interval from the **Velop options**. In router mode it counts every client once, on the node it is connected to. In bridge mode the router does not say which node a wired client uses, so it counts only the Wi-Fi clients of each node (read from the router's per-node wireless connections).
- Optional **client joined / client left** events for each mesh node, for use in automations (off by default).
- **Find channels** button: starts the router's own Channel Finder (automatic channel selection) over JNAP, the same action the Linksys app uses. The scan can briefly interrupt Wi-Fi and took about 5 to 6 minutes on an MX2000, so start it when that is acceptable.
- **Check speed** button: runs the router's built-in internet speed check (the app's **Speed Check**) and shows the finished result as diagnostic sensors.
- Channel scan and speed check start and finish events for automations, including runs started from the Linksys app. While one runs, the status refresh speeds up to 5 s so its end is seen quickly. If the router stops answering while it works, Home Assistant keeps the last data for up to 5 minutes instead of marking entities unavailable.
- Each node shows **IPv4** and **IPv6** address sensors (the IPv6 one only when the router reports an address) and a **Mesh parent node** sensor. The router leaves the parent empty for mesh nodes, so the parent is worked out from the backhaul uplink address, falling back to the primary node.
- Status values (for example **Connected**, **Wired**, **Wireless**) are shown in sentence case in every entity.
- The firmware entity uses the standard icon, like the other entities.
- Entity names set their order on the device page, which sorts by name: **Firmware** and **Firmware version** first, then operating information (**Internet**, **Mesh ...**), then addresses and results (**Network ...**, **Speed check ...**), and controls last (**Check speed**, **Find channels**, **Reboot**).
- Two refresh tiers: fast (default 60 s: WAN, nodes, backhaul, clients) and slow (default 30 min: firmware, statistics), with back-off while the router is unreachable.

| Platform         | Entities                                                                                                                                                                                                              |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sensor`         | Internet status, WAN IP address (disabled), mesh operating mode, firmware version, mesh nodes, network clients (router mode); per node: network clients (Wi-Fi only in bridge mode), IPv4 and IPv6 address, mesh backhaul type/speed, signal (disabled), mesh parent node; mesh CPU and memory load (disabled); hub diagnostics: speed check download, upload, ping, last run and result (disabled) |
| `binary_sensor`  | Internet (hub), connectivity (each child node); hub diagnostics: mesh channel scan running, speed check running                                                                                                            |
| `button`         | Reboot (hub and each child node); Find channels and Check speed (hub)                                                                                                                                           |
| `update`         | Firmware status per node (read-only)                                                                                                                                                                                  |
| `event`          | Hub events (including channel scan and speed check); per node client activity when client events are enabled (see API Reference)                                                                                                                               |
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

- The channel scan and speed check entities appear only when the router firmware answers the matching JNAP actions (`nodes/setup/GetSelectedChannels`, `healthcheck/GetHealthCheckStatus`). Otherwise they are not created.
- Speed check values are shown in Mbps (the router reports kbit/s), as diagnostic entities of the hub device. The **Speed check result** sensor (the router's exit code) is disabled by default. Diagnostics downloads include the last result without identifying data.
- **Use SSL** off sends the admin password unencrypted over your network.
- The password is encrypted in the config entry; the key is a separate private file, `.storage/ha_linksys_velop.key`. If that key is lost, Home Assistant asks for the password again. Anyone who can read your whole `.storage` folder or a full backup can read both files, so protect disk and backups. See [SECURITY.md](SECURITY.md).
- Client names, MACs and IPs are not exposed unless you enable presence tracking or client join and leave events; diagnostics are redacted.
- In **Velop options**, open the **Clients** card and switch on **Client join and leave events**. The integration reloads and each node gets a **Network client activity** event entity. In bridge mode the router reports no clients, so none is created.

## API Reference

Home Assistant actions: `ha_linksys_velop.reboot_node` (device target) and `ha_linksys_velop.refresh_now`.

The hub **Network events** entity and the bus event `ha_linksys_velop_event` fire for `wan_connected`, `wan_disconnected`, `node_online`, `node_offline`, `node_added`, `node_removed`, `firmware_update_available`, `mode_changed` and `authentication_failed`. A transition must be seen in two consecutive refreshes, and nothing fires for the state at startup.

The same **Network events** entity and bus event also fire the router activity events below. Nothing fires for a run already in progress at startup.

| Event type               | When                                             | Extra fields                                    |
| ------------------------ | ------------------------------------------------ | ----------------------------------------------- |
| `channel_scan_started`   | Channel Finder starts (from Home Assistant or the app) | none                                      |
| `channel_scan_completed` | Channel Finder finishes                          | none                                            |
| `speed_test_started`     | A speed check starts                             | none                                            |
| `speed_test_completed`   | A speed check finishes with a result             | `download_mbps`, `upload_mbps`, `latency_ms`    |
| `speed_test_failed`      | A speed check ends without a result              | `detail` (the router's exit code)               |

Pressing a button while the same activity runs, or on firmware without the feature, raises an error in Home Assistant instead of starting it.

Example: notify with the result of every speed check:

```yaml
automation:
  - alias: Report Velop speed check
    triggers:
      - trigger: state
        entity_id: event.living_room_events
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.event_type == 'speed_test_completed' }}"
    actions:
      - action: notify.notify
        data:
          message: >
            Speed check: {{ trigger.to_state.attributes.download_mbps }} Mbps down,
            {{ trigger.to_state.attributes.upload_mbps }} Mbps up,
            {{ trigger.to_state.attributes.latency_ms }} ms ping
```

Example: run a speed check every night at 03:00:

```yaml
automation:
  - alias: Nightly Velop speed check
    triggers:
      - trigger: time
        at: "03:00:00"
    actions:
      - action: button.press
        target:
          entity_id: button.living_room_start_speed_check
```

With **Client join and leave events** enabled, each node's **Network client activity** entity and the same bus event also fire `client_joined` and `client_left` with `node_name`, `node_serial`, `client_name` and `client_mac`. The bus event's `device_id` is the node's device. A client that roams fires `client_left` on the old node and `client_joined` on the new one. Clients already connected at startup raise nothing. Events are checked on the **Status refresh** interval.

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
