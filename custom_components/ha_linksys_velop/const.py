"""Shared constants for the Linksys Velop Local integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_linksys_velop"
MANUFACTURER = "Linksys"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DEVICE_TRACKER,
    Platform.EVENT,
    Platform.SENSOR,
    Platform.UPDATE,
]

CONF_USE_SSL = "use_ssl"

DEFAULT_USE_SSL = True
DEFAULT_VERIFY_SSL = False
CONFIG_ENTRY_MINOR_VERSION = 2

OPT_FAST_INTERVAL = "fast_interval"
OPT_SLOW_INTERVAL = "slow_interval"
OPT_INCLUDED_CHILDREN = "included_children"
OPT_AREAS = "areas"
OPT_TRACKED_CLIENTS = "tracked_clients"
OPT_TRACK_ALL = "track_all_clients"
OPT_CONSIDER_HOME = "consider_home"
OPT_CLIENT_EVENTS = "client_events"

DEFAULT_FAST_INTERVAL = 60
MIN_FAST_INTERVAL = 15
MAX_FAST_INTERVAL = 600
DEFAULT_SLOW_INTERVAL = 30
MIN_SLOW_INTERVAL = 5
MAX_SLOW_INTERVAL = 1440
DEFAULT_CONSIDER_HOME = 180
MAX_BACKOFF_SECONDS = 300
REBOOT_COOLDOWN_SECONDS = 60
ACTIVE_POLL_SECONDS = 5
ACTIVE_GRACE_SECONDS = 30
# The hub can stop answering while it runs a speed check or channel scan.
ACTIVE_TOLERANCE_SECONDS = 300

EVENT_BUS_NAME = "ha_linksys_velop_event"
SERVICE_REBOOT_NODE = "reboot_node"
SERVICE_REFRESH_NOW = "refresh_now"

ISSUE_ACCOUNT_LOCKED = "account_locked"

# Entity keys that only make sense when the hub routes (not in bridge mode).
ROUTER_ONLY_KEYS = frozenset({"clients_total", "client_events"})
