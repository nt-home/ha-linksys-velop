# Changelog

All notable changes follow [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-26

### Added

- **Verify SSL certificate** option under **Use SSL** in the setup and reconfigure forms, off by default so the router's self-signed certificate keeps working; turn it on when a proxy with a valid certificate is in front of the node
- Port-aware **Use SSL**: port 80 turns it off, ports 443 and 8443 turn it on, any other port keeps the chosen value
- Encrypted storage of the admin password with Fernet; the key lives in a separate private file (`.storage/ha_linksys_velop.key`) and the password is decrypted only when a client is built
- Config entry migration 1.1 to 1.2 that encrypts an existing plaintext password and adds `verify_ssl`
- Reauthentication prompt when the stored password cannot be decrypted, for example after the key file is lost
- Tests for encryption, migration, certificate verification, port-based SSL and `strings.json` parity with `translations/en.json`

### Changed

- **Use SSL** is on by default in the setup form
- Bump the integration version to 0.2.0
- Rewrite the README to the standard section layout and document the new SSL, certificate and password behaviour

### Security

- Reject IPv4-mapped IPv6 loopback and metadata addresses (for example `::ffff:127.0.0.1`) as hub hosts

## [0.1.0] - 2026-11-26
### Added

- First implementation: guided setup (host, port, SSL, admin password), node picker with areas, reconfigure, reauth and options flows.
- Hub and child-node devices, sensors, binary sensors, reboot buttons, firmware update entities, event entity and `ha_linksys_velop_event` bus event.
- `reboot_node` and `refresh_now` actions, redacted diagnostics, repair issue for locked admin accounts.
- Emulated JNAP router test harness (router mode, bridge mode, self-signed HTTPS, failure modes).

[Unreleased]: https://github.com/nt-home/ha-linksys-velop/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/nt-home/ha-linksys-velop/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/nt-home/ha-linksys-velop/releases/tag/v0.1.0
