"""Config, reconfigure, reauth and options flows."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    AreaSelector,
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .application.factory import create_client
from .application.onboarding import OnboardingResult, async_probe_hub
from .const import (
    CONF_USE_SSL,
    DEFAULT_CONSIDER_HOME,
    DEFAULT_FAST_INTERVAL,
    DEFAULT_SLOW_INTERVAL,
    DOMAIN,
    MAX_FAST_INTERVAL,
    MAX_SLOW_INTERVAL,
    MIN_FAST_INTERVAL,
    MIN_SLOW_INTERVAL,
    OPT_AREAS,
    OPT_CONSIDER_HOME,
    OPT_FAST_INTERVAL,
    OPT_INCLUDED_CHILDREN,
    OPT_SLOW_INTERVAL,
    OPT_TRACK_ALL,
    OPT_TRACKED_CLIENTS,
)
from .domain.errors import (
    AccountLockedError,
    CannotConnectError,
    ConnectionTimeoutError,
    InvalidAuthError,
    InvalidHostError,
    NotPrimaryNodeError,
    UnsupportedDeviceError,
    VelopError,
)
from .domain.models import Node, NodeRole
from .domain.validation import validate_host, validate_port

if TYPE_CHECKING:
    from .coordinator import VelopConfigEntry

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_PORT = NumberSelector(
    NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
)


def _user_schema(defaults: dict[str, Any] | None = None) -> probatio.Schema:
    d = defaults or {}
    port_key = (
        probatio.Optional(CONF_PORT, default=d[CONF_PORT])
        if CONF_PORT in d
        else probatio.Optional(CONF_PORT)
    )
    return probatio.Schema(
        {
            probatio.Required(CONF_HOST, default=d.get(CONF_HOST, "")): TextSelector(),
            port_key: _PORT,
            probatio.Required(CONF_PASSWORD): _PASSWORD,
            probatio.Required(CONF_USE_SSL, default=d.get(CONF_USE_SSL, False)): (
                BooleanSelector()
            ),
        }
    )


def _error_key(exc: VelopError) -> str:
    """Translate a domain error into a form error key."""
    if isinstance(exc, ConnectionTimeoutError):
        return "timeout"
    if isinstance(exc, CannotConnectError):
        return "cannot_connect"
    if isinstance(exc, InvalidAuthError):
        return "invalid_auth"
    if isinstance(exc, AccountLockedError):
        return "account_locked"
    if isinstance(exc, UnsupportedDeviceError):
        return "unsupported_device"
    if isinstance(exc, NotPrimaryNodeError):
        return "not_primary_node"
    if isinstance(exc, InvalidHostError):
        return "invalid_host"
    return "unknown"


def _node_label(node: Node) -> str:
    return f"{node.name} ({node.model} {node.serial[-4:]})"


class VelopConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guided setup of one Velop hub."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise flow state."""
        self._data: dict[str, Any] = {}
        self._probe: OnboardingResult | None = None

    async def _async_probe(self, user_input: dict[str, Any]) -> OnboardingResult:
        """Validate input and probe the hub, raising a domain error on failure."""
        host = validate_host(user_input[CONF_HOST])
        raw_port = user_input.get(CONF_PORT)
        port = validate_port(
            None if raw_port in (None, "") else int(raw_port),
            use_ssl=user_input[CONF_USE_SSL],
        )
        client = create_client(
            self.hass,
            host,
            port,
            use_ssl=user_input[CONF_USE_SSL],
            password=user_input[CONF_PASSWORD],
        )
        result = await async_probe_hub(client)
        self._data = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_USE_SSL: user_input[CONF_USE_SSL],
            CONF_PASSWORD: user_input[CONF_PASSWORD],
        }
        return result

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect connection details and validate them."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._probe = await self._async_probe(user_input)
            except VelopError as exc:
                errors["base"] = _error_key(exc)
                if isinstance(exc, InvalidHostError):
                    errors = {CONF_HOST: "invalid_host"}
            else:
                await self.async_set_unique_id(self._probe.identity.serial)
                self._abort_if_unique_id_configured(
                    updates={
                        k: self._data[k] for k in (CONF_HOST, CONF_PORT, CONF_USE_SSL)
                    }
                )
                return await self.async_step_nodes()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_user_schema(), user_input),
            errors=errors,
        )

    async def async_step_nodes(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which mesh nodes to include and assign areas."""
        probe = self._probe
        if probe is None:
            return self.async_abort(reason="unknown")
        nodes = probe.nodes
        children = [n for n in nodes if n.role is NodeRole.CHILD]
        labels = {n.serial: _node_label(n) for n in nodes}
        if user_input is not None:
            included = list(user_input.get(OPT_INCLUDED_CHILDREN, []))
            areas = {
                serial: user_input[label]
                for serial, label in labels.items()
                if user_input.get(label)
            }
            identity = probe.identity
            return self.async_create_entry(
                title=f"Velop {identity.model} {identity.serial[-4:]}",
                data=self._data,
                options={
                    OPT_INCLUDED_CHILDREN: included,
                    OPT_AREAS: areas,
                },
            )
        schema: dict[Any, Any] = {}
        if children:
            schema[
                probatio.Optional(
                    OPT_INCLUDED_CHILDREN, default=[n.serial for n in children]
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=n.serial, label=labels[n.serial])
                        for n in children
                    ],
                    multiple=True,
                )
            )
        for label in labels.values():
            schema[probatio.Optional(label)] = AreaSelector()
        return self.async_show_form(
            step_id="nodes",
            data_schema=probatio.Schema(schema),
            description_placeholders={"node_count": str(len(nodes))},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host, port, SSL or password of an existing hub."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = dict(user_input)
            if not merged.get(CONF_PASSWORD):
                merged[CONF_PASSWORD] = entry.data[CONF_PASSWORD]
            try:
                result = await self._async_probe(merged)
            except VelopError as exc:
                errors["base"] = _error_key(exc)
                if isinstance(exc, InvalidHostError):
                    errors = {CONF_HOST: "invalid_host"}
            else:
                await self.async_set_unique_id(result.identity.serial)
                self._abort_if_unique_id_mismatch(reason="wrong_hub")
                return self.async_update_reload_and_abort(
                    entry, data_updates=self._data
                )
        defaults = {
            CONF_HOST: entry.data[CONF_HOST],
            CONF_PORT: entry.data[CONF_PORT],
            CONF_USE_SSL: entry.data[CONF_USE_SSL],
        }
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_HOST, default=defaults[CONF_HOST]
                ): TextSelector(),
                probatio.Optional(CONF_PORT, default=defaults[CONF_PORT]): _PORT,
                probatio.Optional(CONF_PASSWORD): _PASSWORD,
                probatio.Required(CONF_USE_SSL, default=defaults[CONF_USE_SSL]): (
                    BooleanSelector()
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the hub rejected the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password and validate it before saving."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_PORT: entry.data[CONF_PORT],
                CONF_USE_SSL: entry.data[CONF_USE_SSL],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                result = await self._async_probe(merged)
            except VelopError as exc:
                errors["base"] = _error_key(exc)
            else:
                await self.async_set_unique_id(result.identity.serial)
                self._abort_if_unique_id_mismatch(reason="wrong_hub")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): _PASSWORD}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> VelopOptionsFlow:
        """Return the options flow."""
        return VelopOptionsFlow()


class VelopOptionsFlow(OptionsFlowWithReload):
    """Intervals, node inclusion, areas and presence tracking."""

    config_entry: VelopConfigEntry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit options."""
        entry = self.config_entry
        snapshot = entry.runtime_data.data if hasattr(entry, "runtime_data") else None
        nodes = snapshot.nodes if snapshot else ()
        clients = snapshot.clients if snapshot else ()
        labels = {n.serial: _node_label(n) for n in nodes}
        children = [n for n in nodes if n.role is NodeRole.CHILD]
        if user_input is not None:
            options = self._build_options(user_input, labels)
            self._apply_areas(options[OPT_AREAS], entry.options.get(OPT_AREAS, {}))
            return self.async_create_entry(data=options)

        opts = entry.options
        schema: dict[Any, Any] = {
            probatio.Required(
                OPT_FAST_INTERVAL,
                default=opts.get(OPT_FAST_INTERVAL, DEFAULT_FAST_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_FAST_INTERVAL,
                    max=MAX_FAST_INTERVAL,
                    step=1,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Required(
                OPT_SLOW_INTERVAL,
                default=opts.get(OPT_SLOW_INTERVAL, DEFAULT_SLOW_INTERVAL),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SLOW_INTERVAL,
                    max=MAX_SLOW_INTERVAL,
                    step=1,
                    unit_of_measurement="min",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if children:
            current = opts.get(OPT_INCLUDED_CHILDREN, [n.serial for n in children])
            schema[probatio.Optional(OPT_INCLUDED_CHILDREN, default=current)] = (
                SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=n.serial, label=labels[n.serial])
                            for n in children
                        ],
                        multiple=True,
                    )
                )
            )
        saved_areas = opts.get(OPT_AREAS, {})
        for serial, label in labels.items():
            key = (
                probatio.Optional(
                    label, description={"suggested_value": saved_areas[serial]}
                )
                if serial in saved_areas
                else probatio.Optional(label)
            )
            schema[key] = AreaSelector()
        schema[
            probatio.Optional(OPT_TRACK_ALL, default=opts.get(OPT_TRACK_ALL, False))
        ] = BooleanSelector()
        if clients:
            schema[
                probatio.Optional(
                    OPT_TRACKED_CLIENTS, default=opts.get(OPT_TRACKED_CLIENTS, [])
                )
            ] = SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SelectOptionDict(value=c.mac, label=c.name) for c in clients
                    ],
                    multiple=True,
                )
            )
        schema[
            probatio.Optional(
                OPT_CONSIDER_HOME,
                default=opts.get(OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME),
            )
        ] = NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=3600,
                step=1,
                unit_of_measurement="s",
                mode=NumberSelectorMode.BOX,
            )
        )
        return self.async_show_form(step_id="init", data_schema=probatio.Schema(schema))

    def _build_options(
        self, user_input: dict[str, Any], labels: dict[str, str]
    ) -> dict[str, Any]:
        prior = self.config_entry.options
        options: dict[str, Any] = {
            OPT_FAST_INTERVAL: int(user_input[OPT_FAST_INTERVAL]),
            OPT_SLOW_INTERVAL: int(user_input[OPT_SLOW_INTERVAL]),
            OPT_TRACK_ALL: user_input.get(OPT_TRACK_ALL, False),
            OPT_TRACKED_CLIENTS: list(user_input.get(OPT_TRACKED_CLIENTS, [])),
            OPT_CONSIDER_HOME: int(
                user_input.get(OPT_CONSIDER_HOME, DEFAULT_CONSIDER_HOME)
            ),
            OPT_AREAS: self._merge_areas(user_input, labels),
        }
        if OPT_INCLUDED_CHILDREN in user_input:
            options[OPT_INCLUDED_CHILDREN] = list(user_input[OPT_INCLUDED_CHILDREN])
        elif OPT_INCLUDED_CHILDREN in prior:
            options[OPT_INCLUDED_CHILDREN] = prior[OPT_INCLUDED_CHILDREN]
        return options

    def _merge_areas(
        self, user_input: dict[str, Any], labels: dict[str, str]
    ) -> dict[str, str]:
        """Keep areas of nodes not shown in the form; update the shown ones."""
        areas = dict(self.config_entry.options.get(OPT_AREAS, {}))
        for serial, label in labels.items():
            if area := user_input.get(label):
                areas[serial] = area
            else:
                areas.pop(serial, None)
        return areas

    def _apply_areas(self, new: dict[str, str], old: dict[str, str]) -> None:
        """Push explicit area changes to the device registry."""
        registry = dr.async_get(self.hass)
        for serial, area_id in new.items():
            if old.get(serial) == area_id:
                continue
            device = registry.async_get_device_by_identifier(
                (DOMAIN, serial), self.config_entry.entry_id
            )
            if device is not None:
                registry.async_update_device(device.id, area_id=area_id)
