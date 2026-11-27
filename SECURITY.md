# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub **Security → Report a vulnerability** on this repository. Do not open a public issue with exploit details.

Target response times: critical 7 days, high 30 days, medium 90 days.

## How credentials are protected

- The router admin password is encrypted (Fernet: AES with HMAC-SHA256) before it is saved in the config entry. The key is kept in a separate private Home Assistant store (`.storage/ha_linksys_velop.key`, mode 0600) and is deleted when the last hub is removed.
- The password is decrypted only to build the client and is never logged, shown in diagnostics, or sent back to the browser by a failed form.
- Changing the address, port or SSL setting in **Reconfigure** requires entering the password again, so a stored password is never sent to a new address unprompted.
- The `reboot_node` action is restricted to Home Assistant administrators.
- Addresses are validated: loopback, link-local, multicast, reserved and shorthand forms such as `127.1` are refused, and the fallback reboot only contacts a private IP address reported by the hub.

## Known, accepted deviations

The accepted deviations from the workspace security requirements are listed in `docs/requirements/product.requirements.instructions.md` (DEV-01 to DEV-08). The user-visible ones:

- **The Velop certificate is not verified by default** (self-signed certificates must work). Anyone able to impersonate the router on your LAN can capture the admin password. Use a trusted network, or turn on **Verify SSL certificate** when the router sits behind a trusted certificate. Certificate pinning is planned.
- **With "Use SSL" off, the admin password is sent unencrypted** (HTTP, as the Linksys app does). Turn SSL on if your firmware supports it.
- **The encryption key lives next to the ciphertext** in Home Assistant's `.storage` folder and in its backups, because Home Assistant offers integrations no keyring. Encryption therefore protects against casual exposure of `core.config_entries`, not against someone holding the whole `.storage` folder. Protect disk access and backups.
