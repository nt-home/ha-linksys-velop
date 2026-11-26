"""Sensor platform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfDataRate,
)

from .domain.models import NetworkMode, NodeRole
from .entity import VelopEntity, async_add_node_entities

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from homeassistant.helpers.typing import StateType

    from .coordinator import VelopConfigEntry, VelopCoordinator
    from .domain.models import HubSnapshot, Node

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VelopSensorDescription(SensorEntityDescription):
    """Sensor description with a value function over node and snapshot."""

    value_fn: Callable[[Node, HubSnapshot], StateType]
    exists_fn: Callable[[Node, HubSnapshot], bool] = lambda _n, _s: True


def _parent_name(node: Node, snap: HubSnapshot) -> str | None:
    for other in snap.nodes:
        if other.device_id == node.parent_device_id:
            return other.name
    return None


HUB_SENSORS: tuple[VelopSensorDescription, ...] = (
    VelopSensorDescription(
        key="wan_status",
        translation_key="wan_status",
        value_fn=lambda _n, s: s.wan.status,
    ),
    VelopSensorDescription(
        key="wan_ip",
        translation_key="wan_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _n, s: s.wan.ip_address,
    ),
    VelopSensorDescription(
        key="operating_mode",
        translation_key="operating_mode",
        device_class=SensorDeviceClass.ENUM,
        options=[m.value for m in NetworkMode],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda _n, s: s.mode.value,
    ),
    VelopSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n, _s: n.firmware,
    ),
    VelopSensorDescription(
        key="mesh_node_count",
        translation_key="mesh_node_count",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _n, s: len(s.nodes),
    ),
    VelopSensorDescription(
        key="clients_total",
        translation_key="clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda _n, s: sum(1 for c in s.clients if c.connected),
        exists_fn=lambda _n, s: s.mode is NetworkMode.ROUTER,
    ),
    VelopSensorDescription(
        key="cpu_load",
        translation_key="cpu_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _n, s: s.stats.cpu_load,
        exists_fn=lambda _n, s: s.stats.cpu_load is not None,
    ),
    VelopSensorDescription(
        key="memory_load",
        translation_key="memory_load",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda _n, s: s.stats.memory_load,
        exists_fn=lambda _n, s: s.stats.memory_load is not None,
    ),
)

NODE_SENSORS: tuple[VelopSensorDescription, ...] = (
    VelopSensorDescription(
        key="client_count",
        translation_key="client_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda n, _s: n.client_count,
    ),
    VelopSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n, _s: n.ip_address,
    ),
    VelopSensorDescription(
        key="backhaul_type",
        translation_key="backhaul_type",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n, _s: n.backhaul.connection_type if n.backhaul else None,
        exists_fn=lambda n, _s: n.role is NodeRole.CHILD,
    ),
    VelopSensorDescription(
        key="backhaul_speed",
        translation_key="backhaul_speed",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda n, _s: n.backhaul.speed_mbps if n.backhaul else None,
        exists_fn=lambda n, _s: n.role is NodeRole.CHILD,
    ),
    VelopSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda n, _s: n.backhaul.signal_dbm if n.backhaul else None,
        exists_fn=lambda n, _s: n.role is NodeRole.CHILD,
    ),
    VelopSensorDescription(
        key="parent_node",
        translation_key="parent_node",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_parent_name,
        exists_fn=lambda n, _s: n.role is NodeRole.CHILD,
    ),
)


class VelopSensor(VelopEntity, SensorEntity):
    """A sensor reading one value from the snapshot."""

    entity_description: VelopSensorDescription

    def __init__(
        self,
        coordinator: VelopCoordinator,
        serial: str,
        description: VelopSensorDescription,
    ) -> None:
        """Create the sensor."""
        super().__init__(coordinator, serial, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Return the sensor value, or None when unknown."""
        node = self.node
        if node is None:
            return None
        return self.entity_description.value_fn(node, self.snapshot)


def _factory(coordinator: VelopCoordinator, node: Node) -> list[Entity]:
    descriptions = list(NODE_SENSORS)
    if node.role is NodeRole.PRIMARY:
        descriptions = [*HUB_SENSORS, *descriptions]
    snap = coordinator.data
    return [
        VelopSensor(coordinator, node.serial, d)
        for d in descriptions
        if d.exists_fn(node, snap)
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: VelopConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every node."""
    async_add_node_entities(entry, async_add_entities, _factory)
