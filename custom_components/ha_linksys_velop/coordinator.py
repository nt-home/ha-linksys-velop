"""Data update coordinator publishing one immutable snapshot per refresh."""

from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .application.events import EventDetector, EventType, VelopEvent
from .const import (
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
    EVENT_BUS_NAME,
    ISSUE_ACCOUNT_LOCKED,
    MANUFACTURER,
    MAX_BACKOFF_SECONDS,
    OPT_AREAS,
    OPT_FAST_INTERVAL,
    OPT_INCLUDED_CHILDREN,
    OPT_SLOW_INTERVAL,
    REBOOT_COOLDOWN_SECONDS,
)
from .domain.errors import (
    AccountLockedError,
    InvalidAuthError,
    NotPrimaryNodeError,
    VelopError,
)
from .domain.models import HubIdentity, HubSnapshot, Node, NodeRole, SlowSnapshot

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from .domain.ports import HubPort

_LOGGER = logging.getLogger(__name__)

_ROLE_LABEL = {NodeRole.PRIMARY: "primary node", NodeRole.CHILD: "child node"}

type VelopConfigEntry = ConfigEntry[VelopCoordinator]


class VelopCoordinator(DataUpdateCoordinator[HubSnapshot]):
    """Refresh the hub on a fast cadence, with slow data on its own cadence."""

    config_entry: VelopConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: VelopConfigEntry,
        client: HubPort,
        identity: HubIdentity,
    ) -> None:
        """Create the coordinator."""
        self._fast = int(entry.options.get(OPT_FAST_INTERVAL, DEFAULT_FAST_INTERVAL))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{identity.serial}",
            update_interval=timedelta(seconds=self._fast),
        )
        self.client = client
        self.identity = identity
        self._slow_seconds = (
            int(entry.options.get(OPT_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL)) * 60
        )
        self._slow: SlowSnapshot | None = None
        self._slow_at: float | None = None
        self.all_nodes: tuple[Node, ...] = ()
        self._failures = 0
        self._detector = EventDetector()
        self._event_listeners: list[Callable[[VelopEvent], None]] = []
        self._reboot_at: dict[str, float] = {}
        self._mode_reload = False

    @callback
    def async_subscribe_events(
        self, listener: Callable[[VelopEvent], None]
    ) -> Callable[[], None]:
        """Register an event listener and return its unsubscribe function."""
        self._event_listeners.append(listener)

        def _remove() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return _remove

    async def _async_update_data(self) -> HubSnapshot:
        try:
            fast = await self.client.async_fetch_fast(self.identity)
            slow = await self._async_slow()
        except InvalidAuthError as exc:
            self._fire(VelopEvent(EventType.AUTHENTICATION_FAILED))
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from exc
        except AccountLockedError as exc:
            self._fire(VelopEvent(EventType.AUTHENTICATION_FAILED))
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"{ISSUE_ACCOUNT_LOCKED}_{self.identity.serial}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_ACCOUNT_LOCKED,
            )
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="account_locked"
            ) from exc
        except NotPrimaryNodeError as exc:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="not_primary_node"
            ) from exc
        except VelopError as exc:
            self._register_failure()
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key="cannot_connect"
            ) from exc
        self._register_success()
        ir.async_delete_issue(
            self.hass, DOMAIN, f"{ISSUE_ACCOUNT_LOCKED}_{self.identity.serial}"
        )
        self.all_nodes = fast.nodes
        snapshot = HubSnapshot(
            identity=self.identity,
            wan=fast.wan,
            nodes=self._filter_nodes(fast.nodes),
            clients=fast.clients,
            firmware=slow.firmware,
            stats=slow.stats,
        )
        excluded = {n.serial for n in fast.nodes} - {n.serial for n in snapshot.nodes}
        self._publish(snapshot, excluded)
        return snapshot

    async def _async_slow(self) -> SlowSnapshot:
        now = time.monotonic()
        if (
            self._slow is None
            or self._slow_at is None
            or now - self._slow_at >= self._slow_seconds
        ):
            self._slow = await self.client.async_fetch_slow(self.identity)
            self._slow_at = now
        return self._slow

    def _filter_nodes(self, nodes: tuple[Node, ...]) -> tuple[Node, ...]:
        included = self.config_entry.options.get(OPT_INCLUDED_CHILDREN)
        if included is None:
            return nodes
        allowed = set(included)
        return tuple(
            n for n in nodes if n.role is NodeRole.PRIMARY or n.serial in allowed
        )

    def _register_success(self) -> None:
        if self._failures >= 3:  # noqa: PLR2004
            _LOGGER.info("Connection to the hub recovered")
        self._failures = 0
        self.update_interval = timedelta(seconds=self._fast)

    def _register_failure(self) -> None:
        self._failures += 1
        if self._failures == 3:  # noqa: PLR2004
            _LOGGER.warning("The hub has not answered for 3 consecutive refreshes")
        backoff = min(self._fast * 2**self._failures, MAX_BACKOFF_SECONDS)
        jitter = random.uniform(0, backoff * 0.1)  # noqa: S311
        self.update_interval = timedelta(seconds=max(self._fast, backoff + jitter))

    def _publish(self, snapshot: HubSnapshot, excluded: set[str]) -> None:
        events, removed = self._detector.update(snapshot)
        # Nodes the user switched off in the options leave Home Assistant too.
        self._sync_devices(snapshot, [*removed, *sorted(excluded)])
        for event in events:
            self._fire(event)
            if event.type is EventType.MODE_CHANGED and not self._mode_reload:
                self._mode_reload = True
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )

    def _sync_devices(self, snapshot: HubSnapshot, removed: list[str]) -> None:
        registry = dr.async_get(self.hass)
        entry_id = self.config_entry.entry_id
        areas: dict[str, str] = self.config_entry.options.get(OPT_AREAS, {})
        hub_id: str | None = None
        ordered = sorted(snapshot.nodes, key=lambda n: n.role is not NodeRole.PRIMARY)
        for node in ordered:
            identifiers = {(DOMAIN, node.serial)}
            existing = registry.async_get_device_by_identifier(
                next(iter(identifiers)), entry_id
            )
            device = registry.async_get_or_create(
                config_entry_id=entry_id,
                identifiers=identifiers,
                connections={("mac", node.mac)} if node.mac else None,
                manufacturer=MANUFACTURER,
                model=f"{node.model} ({_ROLE_LABEL[node.role]})",
                name=node.name,
                serial_number=node.serial,
                sw_version=node.firmware,
                hw_version=node.hardware,
                via_device_id=None if node.role is NodeRole.PRIMARY else hub_id,
            )
            if node.role is NodeRole.PRIMARY:
                hub_id = device.id
            if existing is None and (area := areas.get(node.serial)):
                registry.async_update_device(device.id, area_id=area)
        for serial in removed:
            stale = registry.async_get_device_by_identifier((DOMAIN, serial), entry_id)
            if stale is not None:
                registry.async_update_device(stale.id, remove_config_entry_id=entry_id)

    def _fire(self, event: VelopEvent) -> None:
        payload: dict[str, str] = {"type": event.type.value}
        if event.node_name:
            payload["node_name"] = event.node_name
        if event.node_serial:
            payload["node_serial"] = event.node_serial
        if event.detail:
            payload["detail"] = event.detail
        registry = dr.async_get(self.hass)
        hub = registry.async_get_device_by_identifier(
            (DOMAIN, self.identity.serial), self.config_entry.entry_id
        )
        if hub is not None:
            payload["device_id"] = hub.id
        self.hass.bus.async_fire(EVENT_BUS_NAME, payload)
        for listener in list(self._event_listeners):
            listener(event)

    async def async_reboot_node(self, serial: str) -> None:
        """Reboot one node, ignoring repeats inside the cooldown window."""
        node = self.data.node_by_serial.get(serial)
        if node is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="unknown_node"
            )
        now = time.monotonic()
        last = self._reboot_at.get(serial)
        if last is not None and now - last < REBOOT_COOLDOWN_SECONDS:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reboot_recent"
            )
        self._reboot_at[serial] = now
        try:
            await self.client.async_reboot(node)
        except InvalidAuthError as exc:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from exc
        except VelopError as exc:
            self._reboot_at.pop(serial, None)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="reboot_failed"
            ) from exc
