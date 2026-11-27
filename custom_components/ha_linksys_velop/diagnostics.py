"""Redacted diagnostics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from .const import OPT_AREAS, OPT_INCLUDED_CHILDREN, OPT_TRACKED_CLIENTS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import VelopConfigEntry
    from .domain.models import HubSnapshot

TO_REDACT = {
    CONF_PASSWORD,
    CONF_HOST,
    OPT_AREAS,
    OPT_INCLUDED_CHILDREN,
    OPT_TRACKED_CLIENTS,
    "unique_id",
    "title",
}


def _speed_summary(snapshot: HubSnapshot) -> dict[str, Any] | None:
    state = snapshot.speed_test
    if state is None:
        return None
    latest = state.latest
    return {
        "running": state.running,
        "last_exit_code": latest.exit_code if latest else None,
        "last_download_mbps": latest.download_mbps if latest else None,
        "last_upload_mbps": latest.upload_mbps if latest else None,
        "last_latency_ms": latest.latency_ms if latest else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: VelopConfigEntry
) -> dict[str, Any]:
    """Return counts and capabilities without identifying data."""
    snapshot = entry.runtime_data.data
    return {
        "entry": async_redact_data(
            {
                "data": dict(entry.data),
                "options": dict(entry.options),
                "unique_id": entry.unique_id,
                "title": entry.title,
            },
            TO_REDACT,
        ),
        "mode": snapshot.mode.value,
        "model": snapshot.identity.model,
        "firmware": snapshot.identity.firmware,
        "advertised_services": len(snapshot.identity.services),
        "node_count": len(snapshot.nodes),
        "nodes": [
            {
                "model": n.model,
                "role": n.role.value,
                "online": n.online,
                "firmware": n.firmware,
                "backhaul": n.backhaul.connection_type if n.backhaul else None,
            }
            for n in snapshot.nodes
        ],
        "client_count": len(snapshot.clients),
        "wan_status": snapshot.wan.status,
        "channel_scan_running": snapshot.channel_scan_running,
        "speed_test": _speed_summary(snapshot),
        "last_update_success": entry.runtime_data.last_update_success,
    }
