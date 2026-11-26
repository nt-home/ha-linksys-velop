# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub **Security → Report a vulnerability** on this repository. Do not open a public issue with exploit details.

Target response times: critical 7 days, high 30 days, medium 90 days.

## Known, accepted deviations

The accepted deviations from the workspace security requirements are listed in `docs/requirements/product.requirements.instructions.md` (DEV-01 to DEV-08). The user-visible ones:

- **The Velop certificate is not verified** (self-signed certificates must work). Anyone able to impersonate the router on your LAN can capture the admin password. Use a trusted network.
- **With "Use SSL" off, the admin password is sent unencrypted** (HTTP, as the Linksys app does). Turn SSL on if your firmware supports it.
- **The password is stored in the config entry** (`.storage/core.config_entries`), like other Home Assistant integrations. Home Assistant itself does not encrypt this file. Protect disk access and backups.
