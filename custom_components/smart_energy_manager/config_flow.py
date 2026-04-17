"""Config flow for Smart Battery Charging integration."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_CHARGE_FORCE,
    CONF_CHARGE_STOP,
    CONF_CHARGING_EFFICIENCY,
    CONF_CONSUMPTION_SENSOR,
    CONF_CONTROL_TYPE,
    CONF_DAILY_SOLAR_SENSOR,
    CONF_CURRENCY,
    CONF_EMS_CHARGE_MODE_VALUE,
    CONF_EMS_NORMAL_MODE_VALUE,
    CONF_EVENING_CONSUMPTION_MULTIPLIER,
    CONF_FALLBACK_CONSUMPTION,
    CONF_GRID_EXPORT_POWER_SENSOR,
    CONF_GRID_EXPORT_SENSOR,
    CONF_HOUSE_CONSUMPTION_POWER_SENSOR,
    CONF_GRID_IMPORT_SENSOR,
    CONF_INVERTER_AC_LOWER_LIMIT_NUMBER,
    CONF_INVERTER_ACTUAL_SOLAR_SENSOR,
    CONF_INVERTER_BATTERY_DOD_NUMBER,
    CONF_INVERTER_BATTERY_POWER_NUMBER,
    CONF_INVERTER_CAPACITY_SENSOR,
    CONF_INVERTER_CHARGE_COMMAND_SELECT,
    CONF_INVERTER_CHARGE_SOC_LIMIT,
    CONF_INVERTER_DISCHARGE_MIN_SOC,
    CONF_INVERTER_MODE_SELECT,
    CONF_INVERTER_SOC_SENSOR,
    CONF_INVERTER_TEMPLATE,
    CONF_INVERTER_WORKING_MODE_NUMBER,
    CONF_MAX_CHARGE_LEVEL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_CHARGE_PRICE,
    CONF_MIN_SOC,
    CONF_MODE_MANUAL,
    CONF_MODE_SELF_USE,
    CONF_NIGHT_CONSUMPTION_MULTIPLIER,
    CONF_NOTIFICATION_SERVICE,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_NOTIFY_BATTERY_FULL,
    CONF_NOTIFY_BATTERY_LOW,
    CONF_NOTIFY_CHARGING_COMPLETE,
    CONF_NOTIFY_CHARGING_STALLED,
    CONF_NOTIFY_CHARGING_START,
    CONF_NOTIFY_MORNING_SAFETY,
    CONF_NOTIFY_PLANNING,
    CONF_NOTIFY_SENSOR_UNAVAILABLE,
    CONF_NOTIFY_SURPLUS_LOAD,
    CONF_PRICE_ATTRIBUTE_FORMAT,
    CONF_NEGATIVE_PRICE_ABSORB,
    CONF_PROACTIVE_SOC_THRESHOLD,
    CONF_PV_POWER_SENSOR,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_TODAY,
    CONF_SOLAR_FORECAST_TOMORROW,
    CONF_SURPLUS_LOADS,
    CONF_WEEKEND_CONSUMPTION_MULTIPLIER,
    CONF_WINDOW_END_HOUR,
    CONF_WINDOW_START_HOUR,
    CONTROL_TYPE_EMS_POWER,
    DEFAULT_BATTERY_CAPACITY,
    DEFAULT_CHARGING_EFFICIENCY,
    DEFAULT_CURRENCY,
    DEFAULT_EVENING_CONSUMPTION_MULTIPLIER,
    DEFAULT_FALLBACK_CONSUMPTION,
    DEFAULT_INVERTER_TEMPLATE,
    DEFAULT_MAX_CHARGE_LEVEL,
    DEFAULT_MAX_CHARGE_POWER,
    DEFAULT_MAX_CHARGE_PRICE,
    DEFAULT_MIN_SOC,
    DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER,
    DEFAULT_NOTIFICATION_SERVICE,
    DEFAULT_NOTIFY_BATTERY_FULL,
    DEFAULT_NOTIFY_BATTERY_LOW,
    DEFAULT_NOTIFY_CHARGING_COMPLETE,
    DEFAULT_NOTIFY_CHARGING_STALLED,
    DEFAULT_NOTIFY_CHARGING_START,
    DEFAULT_NOTIFY_MORNING_SAFETY,
    DEFAULT_NOTIFY_PLANNING,
    DEFAULT_NOTIFY_SENSOR_UNAVAILABLE,
    DEFAULT_NOTIFY_SURPLUS_LOAD,
    DEFAULT_PRICE_ATTRIBUTE_FORMAT,
    DEFAULT_NEGATIVE_PRICE_ABSORB,
    DEFAULT_PROACTIVE_SOC_THRESHOLD,
    DEFAULT_SURPLUS_BATTERY_OFF,
    DEFAULT_SURPLUS_BATTERY_ON,
    DEFAULT_SURPLUS_MARGIN_OFF,
    DEFAULT_SURPLUS_MARGIN_ON,
    DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL,
    DEFAULT_MAX_OUTDOOR_TEMP,
    DEFAULT_PREDICTIVE_LEAD_MINUTES,
    DEFAULT_PREDICTIVE_SCHEDULE_END,
    DEFAULT_PREDICTIVE_SCHEDULE_START,
    SURPLUS_MODE_PREDICTIVE,
    SURPLUS_MODE_REACTIVE,
    DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER,
    DEFAULT_WINDOW_END_HOUR,
    DEFAULT_WINDOW_START_HOUR,
    DOMAIN,
    PRICE_FORMAT_HOUR_INT,
    PRICE_FORMAT_ISO_DATETIME,
)
from .inverters import INVERTER_TEMPLATES, get_template

_LOGGER = logging.getLogger(__name__)


def _entity_selector(domain: str, multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domain, multiple=multiple)
    )


def _select_selector(options: list[str]) -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


class SmartBatteryChargingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Smart Battery Charging."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow handler."""
        return SmartBatteryChargingOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Name."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter_template()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default="Smart Energy Manager"): str,
                }
            ),
        )

    async def async_step_inverter_template(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Select inverter integration template."""
        if user_input is not None:
            self._data.update(user_input)
            template = get_template(
                self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
            )
            # Store control type from template
            self._data[CONF_CONTROL_TYPE] = template.control_type
            return await self.async_step_inverter()

        template_options = [
            selector.SelectOptionDict(value=tid, label=tmpl.label)
            for tid, tmpl in INVERTER_TEMPLATES.items()
        ]

        return self.async_show_form(
            step_id="inverter_template",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INVERTER_TEMPLATE,
                        default=DEFAULT_INVERTER_TEMPLATE,
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=template_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_inverter(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3: Inverter entity selectors (varies by control type)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_inverter_values()

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )
        hints = template.entity_hints
        hint_lines = "\n".join(
            f"- **{key}**: {hint}" for key, hint in hints.items()
        ) if hints else ""

        # Build schema based on control type
        schema_dict: dict[Any, Any] = {
            vol.Required(CONF_INVERTER_SOC_SENSOR): _entity_selector("sensor"),
            vol.Required(CONF_INVERTER_CAPACITY_SENSOR): _entity_selector("sensor"),
            vol.Required(CONF_INVERTER_ACTUAL_SOLAR_SENSOR): _entity_selector("sensor"),
        }

        if template.control_type == CONTROL_TYPE_EMS_POWER:
            # Wattsonic / EMS power control: number entities
            schema_dict[vol.Required(CONF_INVERTER_WORKING_MODE_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Required(CONF_INVERTER_BATTERY_POWER_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Required(CONF_INVERTER_AC_LOWER_LIMIT_NUMBER)] = _entity_selector("number")
            schema_dict[vol.Optional(CONF_INVERTER_BATTERY_DOD_NUMBER)] = _entity_selector("number")
        else:
            # Select-based control (Solax, SolarEdge, etc.)
            schema_dict[vol.Required(CONF_INVERTER_MODE_SELECT)] = _entity_selector("select")
            schema_dict[vol.Required(CONF_INVERTER_CHARGE_COMMAND_SELECT)] = _entity_selector("select")
            schema_dict[vol.Required(CONF_INVERTER_CHARGE_SOC_LIMIT)] = _entity_selector("number")
            schema_dict[vol.Optional(CONF_INVERTER_DISCHARGE_MIN_SOC)] = _entity_selector("number")

        return self.async_show_form(
            step_id="inverter",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={
                "template_name": template.label,
                "entity_hints": hint_lines,
            },
        )

    async def async_step_inverter_values(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4: Inverter option strings / EMS values (pre-filled from template)."""
        errors: dict[str, str] = {}

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )

        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_price()

        if template.control_type == CONTROL_TYPE_EMS_POWER:
            # EMS mode: show integer values for working mode registers
            return self.async_show_form(
                step_id="inverter_values",
                data_schema=vol.Schema(
                    {
                        vol.Required(
                            CONF_EMS_CHARGE_MODE_VALUE,
                            default=template.ems_charge_mode_value,
                        ): vol.Coerce(int),
                        vol.Required(
                            CONF_EMS_NORMAL_MODE_VALUE,
                            default=template.ems_normal_mode_value,
                        ): vol.Coerce(int),
                    }
                ),
                errors=errors,
            )

        # Select-based: show mode string dropdowns
        mode_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_MODE_SELECT, "")
        )
        charge_options = await self._get_select_options(
            self._data.get(CONF_INVERTER_CHARGE_COMMAND_SELECT, "")
        )

        # Use template defaults, falling back to generic defaults for custom
        default_self_use = template.mode_self_use or "Self Use Mode"
        default_manual = template.mode_manual or "Manual Mode"
        default_force = template.charge_force or "Force Charge"
        default_stop = template.charge_stop or "Stop Charge and Discharge"

        schema_dict: dict[Any, Any] = {}
        if mode_options:
            schema_dict[vol.Required(CONF_MODE_SELF_USE)] = _select_selector(mode_options)
            schema_dict[vol.Required(CONF_MODE_MANUAL)] = _select_selector(mode_options)
        else:
            schema_dict[vol.Required(CONF_MODE_SELF_USE, default=default_self_use)] = str
            schema_dict[vol.Required(CONF_MODE_MANUAL, default=default_manual)] = str

        if charge_options:
            schema_dict[vol.Required(CONF_CHARGE_FORCE)] = _select_selector(charge_options)
            schema_dict[vol.Required(CONF_CHARGE_STOP)] = _select_selector(charge_options)
        else:
            schema_dict[vol.Required(CONF_CHARGE_FORCE, default=default_force)] = str
            schema_dict[vol.Required(CONF_CHARGE_STOP, default=default_stop)] = str

        return self.async_show_form(
            step_id="inverter_values",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5: Price sensor configuration."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_solar()

        return self.async_show_form(
            step_id="price",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PRICE_SENSOR): _entity_selector("sensor"),
                    vol.Required(
                        CONF_PRICE_ATTRIBUTE_FORMAT,
                        default=DEFAULT_PRICE_ATTRIBUTE_FORMAT,
                    ): _select_selector(
                        [PRICE_FORMAT_ISO_DATETIME, PRICE_FORMAT_HOUR_INT]
                    ),
                }
            ),
        )

    async def async_step_solar(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 6: Solar forecast entities (supports multiple orientations)."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_consumption()

        return self.async_show_form(
            step_id="solar",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOLAR_FORECAST_TODAY): _entity_selector(
                        "sensor", multiple=True
                    ),
                    vol.Required(CONF_SOLAR_FORECAST_TOMORROW): _entity_selector(
                        "sensor", multiple=True
                    ),
                }
            ),
        )

    async def async_step_consumption(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 7: Daily consumption sensor."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_analytics()

        return self.async_show_form(
            step_id="consumption",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CONSUMPTION_SENSOR): _entity_selector("sensor"),
                }
            ),
        )

    async def async_step_analytics(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 7b: Optional analytics + real-time power sensors for surplus control."""
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_settings()

        return self.async_show_form(
            step_id="analytics",
            data_schema=vol.Schema(
                {
                    # Daily energy (kWh) — for analytics/dashboard
                    vol.Optional(CONF_GRID_IMPORT_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_GRID_EXPORT_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_DAILY_SOLAR_SENSOR): _entity_selector("sensor"),
                    # Real-time power (W/kW) — required for surplus load management
                    vol.Optional(CONF_GRID_EXPORT_POWER_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_PV_POWER_SENSOR): _entity_selector("sensor"),
                    vol.Optional(CONF_HOUSE_CONSUMPTION_POWER_SENSOR): _entity_selector("sensor"),
                    # Outdoor temperature (for seasonal load control, e.g. floor heating)
                    vol.Optional(CONF_OUTDOOR_TEMP_SENSOR): _entity_selector("sensor"),
                }
            ),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 8: Battery and charging settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # M3: Validate min_soc < max_charge_level
            min_soc = user_input.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
            max_charge = user_input.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL)
            if min_soc >= max_charge:
                errors["base"] = "min_soc_exceeds_max"
            else:
                self._data.update(user_input)
                await self.async_set_unique_id(
                    f"{DOMAIN}_{self._data.get('name', 'default')}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=self._data.get("name", "Smart Energy Manager"),
                    data=self._data,
                )

        template = get_template(
            self._data.get(CONF_INVERTER_TEMPLATE, DEFAULT_INVERTER_TEMPLATE)
        )
        battery_default = template.battery_capacity

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY, default=battery_default
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_LEVEL, default=DEFAULT_MAX_CHARGE_LEVEL
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MIN_SOC, default=DEFAULT_MIN_SOC
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_POWER, default=DEFAULT_MAX_CHARGE_POWER
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_PRICE, default=DEFAULT_MAX_CHARGE_PRICE
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_FALLBACK_CONSUMPTION, default=DEFAULT_FALLBACK_CONSUMPTION
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_START_HOUR, default=DEFAULT_WINDOW_START_HOUR
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_WINDOW_END_HOUR, default=DEFAULT_WINDOW_END_HOUR
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_CURRENCY, default=DEFAULT_CURRENCY
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _get_select_options(self, entity_id: str) -> list[str]:
        """Get available options from a select entity."""
        if not entity_id:
            return []
        state = self.hass.states.get(entity_id)
        if state is None:
            return []
        options = state.attributes.get("options", [])
        return list(options) if options else []


class SmartBatteryChargingOptionsFlow(OptionsFlow):
    """Handle options for Smart Battery Charging.

    Uses a menu-based flow:
      init → menu (Settings / Surplus Loads)
      settings → main charging settings form
      surplus_menu → list loads, add/remove
      surplus_add → form for new load
      surplus_remove → select load to remove
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        # Working copy of options — accumulated across sub-steps
        self._options: dict[str, Any] = {}
        # Temporary storage for multi-step surplus_add
        self._pending_load: dict[str, Any] = {}

    def _current(self) -> dict[str, Any]:
        """Merged data + options for reading current values."""
        return {**self._config_entry.data, **self._config_entry.options, **self._options}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show menu: Settings or Surplus Loads."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["settings", "surplus_menu"],
        )

    # ---- Settings (the original big form) ----

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the main charging settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # M3: Validate min_soc < max_charge_level
            min_soc = user_input.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
            max_charge = user_input.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL)
            if min_soc >= max_charge:
                errors["base"] = "min_soc_exceeds_max"
            else:
                # Preserve surplus_loads from existing options
                merged = {**self._config_entry.options, **user_input}
                return self.async_create_entry(title="", data=merged)

        current = self._current()

        return self.async_show_form(
            step_id="settings",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY,
                        default=current.get(CONF_BATTERY_CAPACITY, DEFAULT_BATTERY_CAPACITY),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_LEVEL,
                        default=current.get(CONF_MAX_CHARGE_LEVEL, DEFAULT_MAX_CHARGE_LEVEL),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MIN_SOC,
                        default=current.get(CONF_MIN_SOC, DEFAULT_MIN_SOC),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_POWER,
                        default=current.get(CONF_MAX_CHARGE_POWER, DEFAULT_MAX_CHARGE_POWER),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_MAX_CHARGE_PRICE,
                        default=current.get(CONF_MAX_CHARGE_PRICE, DEFAULT_MAX_CHARGE_PRICE),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_FALLBACK_CONSUMPTION,
                        default=current.get(CONF_FALLBACK_CONSUMPTION, DEFAULT_FALLBACK_CONSUMPTION),
                    ): vol.Coerce(float),
                    vol.Required(
                        CONF_WINDOW_START_HOUR,
                        default=current.get(CONF_WINDOW_START_HOUR, DEFAULT_WINDOW_START_HOUR),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_WINDOW_END_HOUR,
                        default=current.get(CONF_WINDOW_END_HOUR, DEFAULT_WINDOW_END_HOUR),
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
                    vol.Required(
                        CONF_CURRENCY,
                        default=current.get(CONF_CURRENCY, DEFAULT_CURRENCY),
                    ): str,
                    # Analytics sensors (optional)
                    vol.Optional(
                        CONF_GRID_IMPORT_SENSOR,
                        description={"suggested_value": current.get(CONF_GRID_IMPORT_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_GRID_EXPORT_SENSOR,
                        description={"suggested_value": current.get(CONF_GRID_EXPORT_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_DAILY_SOLAR_SENSOR,
                        description={"suggested_value": current.get(CONF_DAILY_SOLAR_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_GRID_EXPORT_POWER_SENSOR,
                        description={"suggested_value": current.get(CONF_GRID_EXPORT_POWER_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_PV_POWER_SENSOR,
                        description={"suggested_value": current.get(CONF_PV_POWER_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_HOUSE_CONSUMPTION_POWER_SENSOR,
                        description={"suggested_value": current.get(CONF_HOUSE_CONSUMPTION_POWER_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional(
                        CONF_PROACTIVE_SOC_THRESHOLD,
                        default=current.get(CONF_PROACTIVE_SOC_THRESHOLD, DEFAULT_PROACTIVE_SOC_THRESHOLD),
                    ): vol.All(vol.Coerce(float), vol.Range(min=50, max=100)),
                    vol.Optional(
                        CONF_NEGATIVE_PRICE_ABSORB,
                        default=current.get(CONF_NEGATIVE_PRICE_ABSORB, DEFAULT_NEGATIVE_PRICE_ABSORB),
                    ): bool,
                    vol.Optional(
                        CONF_OUTDOOR_TEMP_SENSOR,
                        description={"suggested_value": current.get(CONF_OUTDOOR_TEMP_SENSOR) or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    # Advanced: Efficiency & Consumption Profiles
                    vol.Optional(
                        CONF_CHARGING_EFFICIENCY,
                        default=current.get(CONF_CHARGING_EFFICIENCY, DEFAULT_CHARGING_EFFICIENCY),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.7, max=1.0)),
                    vol.Optional(
                        CONF_EVENING_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_EVENING_CONSUMPTION_MULTIPLIER, DEFAULT_EVENING_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=3.0)),
                    vol.Optional(
                        CONF_NIGHT_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_NIGHT_CONSUMPTION_MULTIPLIER, DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
                    vol.Optional(
                        CONF_WEEKEND_CONSUMPTION_MULTIPLIER,
                        default=current.get(CONF_WEEKEND_CONSUMPTION_MULTIPLIER, DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=2.0)),
                    # Notifications
                    vol.Optional(
                        CONF_NOTIFICATION_SERVICE,
                        default=current.get(CONF_NOTIFICATION_SERVICE, DEFAULT_NOTIFICATION_SERVICE),
                    ): str,
                    vol.Optional(
                        CONF_NOTIFY_PLANNING,
                        default=current.get(CONF_NOTIFY_PLANNING, DEFAULT_NOTIFY_PLANNING),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_START,
                        default=current.get(CONF_NOTIFY_CHARGING_START, DEFAULT_NOTIFY_CHARGING_START),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_COMPLETE,
                        default=current.get(CONF_NOTIFY_CHARGING_COMPLETE, DEFAULT_NOTIFY_CHARGING_COMPLETE),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_MORNING_SAFETY,
                        default=current.get(CONF_NOTIFY_MORNING_SAFETY, DEFAULT_NOTIFY_MORNING_SAFETY),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_CHARGING_STALLED,
                        default=current.get(CONF_NOTIFY_CHARGING_STALLED, DEFAULT_NOTIFY_CHARGING_STALLED),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_SENSOR_UNAVAILABLE,
                        default=current.get(CONF_NOTIFY_SENSOR_UNAVAILABLE, DEFAULT_NOTIFY_SENSOR_UNAVAILABLE),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_BATTERY_FULL,
                        default=current.get(CONF_NOTIFY_BATTERY_FULL, DEFAULT_NOTIFY_BATTERY_FULL),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_BATTERY_LOW,
                        default=current.get(CONF_NOTIFY_BATTERY_LOW, DEFAULT_NOTIFY_BATTERY_LOW),
                    ): bool,
                    vol.Optional(
                        CONF_NOTIFY_SURPLUS_LOAD,
                        default=current.get(CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD),
                    ): bool,
                }
            ),
            errors=errors,
        )

    # ---- Surplus Load Management ----

    def _get_surplus_loads(self) -> list[dict[str, Any]]:
        """Get the current surplus loads list."""
        current = self._current()
        loads = current.get(CONF_SURPLUS_LOADS, [])
        return list(loads) if isinstance(loads, list) else []

    async def async_step_surplus_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show surplus loads list and management options."""
        loads = self._get_surplus_loads()

        menu_options = ["surplus_add"]
        if loads:
            menu_options.append("surplus_edit")
            menu_options.append("surplus_remove")

        # Build description showing current loads as a table
        if loads:
            header = "| # | Name | Mode | Power | Priority |\n|---|------|------|-------|----------|\n"
            rows = "\n".join(
                f"| {i+1} | {ld['name']} | {ld.get('mode', 'reactive')} | {ld['power_kw']} kW | {ld.get('priority', 1)} |"
                for i, ld in enumerate(loads)
            )
            description = f"**Configured loads:**\n\n{header}{rows}"
        else:
            description = "No surplus loads configured."

        return self.async_show_menu(
            step_id="surplus_menu",
            menu_options=menu_options,
            description_placeholders={"load_list": description},
        )

    async def async_step_surplus_add(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Add a new surplus load — basic config."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Check for duplicate name + mode
            existing = self._get_surplus_loads()
            new_name = user_input["name"]
            new_mode = user_input.get("mode", SURPLUS_MODE_REACTIVE)
            for ex in existing:
                if ex["name"] == new_name and ex.get("mode", SURPLUS_MODE_REACTIVE) == new_mode:
                    errors["name"] = "duplicate_load_name"
                    break
            if not errors:
                self._pending_load = {
                    "id": str(uuid.uuid4()),
                    "name": user_input["name"],
                    "switch_entity": user_input["switch_entity"],
                    "power_kw": user_input["power_kw"],
                    "power_sensor": user_input.get("power_sensor", ""),
                    "priority": user_input.get("priority", len(self._get_surplus_loads()) + 1),
                    "mode": user_input.get("mode", SURPLUS_MODE_REACTIVE),
                    "battery_on_threshold": user_input.get("battery_on_threshold", DEFAULT_SURPLUS_BATTERY_ON),
                    "battery_off_threshold": user_input.get("battery_off_threshold", DEFAULT_SURPLUS_BATTERY_OFF),
                    "margin_on_kw": user_input.get("margin_on_kw", DEFAULT_SURPLUS_MARGIN_ON),
                    "margin_off_kw": user_input.get("margin_off_kw", DEFAULT_SURPLUS_MARGIN_OFF),
                    "min_switch_interval": user_input.get("min_switch_interval", DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL),
                    "max_outdoor_temp": user_input.get("max_outdoor_temp", DEFAULT_MAX_OUTDOOR_TEMP),
                }
                # If predictive mode, show schedule step
                if self._pending_load["mode"] == SURPLUS_MODE_PREDICTIVE:
                    return await self.async_step_surplus_add_predictive()
                # Reactive mode — save immediately
                return self._save_pending_load()

        loads = self._get_surplus_loads()
        next_priority = len(loads) + 1

        return self.async_show_form(
            step_id="surplus_add",
            data_schema=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Required("switch_entity"): _entity_selector("switch"),
                    vol.Required("power_kw"): vol.All(
                        vol.Coerce(float), vol.Range(min=0.1, max=50.0)
                    ),
                    vol.Optional("power_sensor"): _entity_selector("sensor"),
                    vol.Optional("priority", default=next_priority): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=10)
                    ),
                    vol.Optional("mode", default=SURPLUS_MODE_REACTIVE): _select_selector(
                        [SURPLUS_MODE_REACTIVE, SURPLUS_MODE_PREDICTIVE]
                    ),
                    vol.Optional(
                        "battery_on_threshold", default=DEFAULT_SURPLUS_BATTERY_ON
                    ): vol.All(vol.Coerce(float), vol.Range(min=50.0, max=100.0)),
                    vol.Optional(
                        "battery_off_threshold", default=DEFAULT_SURPLUS_BATTERY_OFF
                    ): vol.All(vol.Coerce(float), vol.Range(min=50.0, max=100.0)),
                    vol.Optional(
                        "margin_on_kw", default=DEFAULT_SURPLUS_MARGIN_ON
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                    vol.Optional(
                        "margin_off_kw", default=DEFAULT_SURPLUS_MARGIN_OFF
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                    vol.Optional(
                        "min_switch_interval", default=DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                    vol.Optional(
                        "max_outdoor_temp", default=DEFAULT_MAX_OUTDOOR_TEMP
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=50.0)),
                }
            ),
            errors=errors,
        )

    async def async_step_surplus_add_predictive(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure schedule for a predictive surplus load."""
        if user_input is not None:
            self._pending_load["schedule_start_hour"] = int(user_input["schedule_start_hour"])
            self._pending_load["schedule_end_hour"] = int(user_input["schedule_end_hour"])
            self._pending_load["evaluation_lead_minutes"] = int(user_input.get(
                "evaluation_lead_minutes", str(DEFAULT_PREDICTIVE_LEAD_MINUTES)
            ))
            return self._save_pending_load()

        hour_options = [
            selector.SelectOptionDict(value=str(h), label=f"{h:02d}:00")
            for h in range(24)
        ]
        lead_options = [
            selector.SelectOptionDict(value=str(m), label=f"{m} min")
            for m in [15, 30, 45, 60, 90, 120]
        ]
        return self.async_show_form(
            step_id="surplus_add_predictive",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "schedule_start_hour",
                        default=str(DEFAULT_PREDICTIVE_SCHEDULE_START),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=hour_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        "schedule_end_hour",
                        default=str(DEFAULT_PREDICTIVE_SCHEDULE_END),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=hour_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        "evaluation_lead_minutes",
                        default=str(DEFAULT_PREDICTIVE_LEAD_MINUTES),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=lead_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    def _save_pending_load(self) -> FlowResult:
        """Save the pending load to options."""
        loads = self._get_surplus_loads()
        loads.append(self._pending_load)
        self._options[CONF_SURPLUS_LOADS] = loads
        return self.async_create_entry(
            title="", data={**self._config_entry.options, **self._options}
        )

    async def async_step_surplus_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a surplus load to edit."""
        loads = self._get_surplus_loads()

        if user_input is not None:
            edit_id = user_input.get("load_to_edit", "")
            for i, ld in enumerate(loads):
                if ld.get("id") == edit_id:
                    self._editing_load_index = i
                    self._pending_load = dict(ld)
                    return await self.async_step_surplus_edit_form()
            return await self.async_step_surplus_menu()

        if not loads:
            return await self.async_step_surplus_menu()

        load_options = [
            selector.SelectOptionDict(
                value=ld["id"],
                label=f"{ld['name']} ({ld.get('mode', 'reactive')})",
            )
            for ld in loads
        ]
        return self.async_show_form(
            step_id="surplus_edit",
            data_schema=vol.Schema(
                {
                    vol.Required("load_to_edit"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=load_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_surplus_edit_form(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit a surplus load's configuration."""
        ld = self._pending_load

        if user_input is not None:
            updated = {
                "name": user_input["name"],
                "switch_entity": user_input["switch_entity"],
                "power_kw": user_input["power_kw"],
                "power_sensor": user_input.get("power_sensor", ""),
                "priority": user_input.get("priority", ld.get("priority", 1)),
                "mode": user_input.get("mode", ld.get("mode", SURPLUS_MODE_REACTIVE)),
                "battery_on_threshold": user_input.get("battery_on_threshold", ld.get("battery_on_threshold", DEFAULT_SURPLUS_BATTERY_ON)),
                "battery_off_threshold": user_input.get("battery_off_threshold", ld.get("battery_off_threshold", DEFAULT_SURPLUS_BATTERY_OFF)),
                "margin_on_kw": user_input.get("margin_on_kw", ld.get("margin_on_kw", DEFAULT_SURPLUS_MARGIN_ON)),
                "margin_off_kw": user_input.get("margin_off_kw", ld.get("margin_off_kw", DEFAULT_SURPLUS_MARGIN_OFF)),
                "min_switch_interval": user_input.get("min_switch_interval", ld.get("min_switch_interval", DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL)),
                "max_outdoor_temp": user_input.get("max_outdoor_temp", ld.get("max_outdoor_temp", DEFAULT_MAX_OUTDOOR_TEMP)),
            }
            # Preserve predictive fields
            if updated["mode"] == SURPLUS_MODE_PREDICTIVE:
                updated["schedule_start_hour"] = ld.get("schedule_start_hour", DEFAULT_PREDICTIVE_SCHEDULE_START)
                updated["schedule_end_hour"] = ld.get("schedule_end_hour", DEFAULT_PREDICTIVE_SCHEDULE_END)
                updated["evaluation_lead_minutes"] = ld.get("evaluation_lead_minutes", DEFAULT_PREDICTIVE_LEAD_MINUTES)
                self._pending_load = updated
                # If mode changed to predictive, show schedule step
                if ld.get("mode") != SURPLUS_MODE_PREDICTIVE:
                    return await self.async_step_surplus_edit_predictive()

            loads = self._get_surplus_loads()
            loads[self._editing_load_index] = updated
            self._options[CONF_SURPLUS_LOADS] = loads
            return self.async_create_entry(
                title="", data={**self._config_entry.options, **self._options}
            )

        return self.async_show_form(
            step_id="surplus_edit_form",
            data_schema=vol.Schema(
                {
                    vol.Required("name", default=ld.get("name", "")): str,
                    vol.Required("switch_entity", default=ld.get("switch_entity", "")): _entity_selector("switch"),
                    vol.Required("power_kw", default=ld.get("power_kw", 1.0)): vol.All(
                        vol.Coerce(float), vol.Range(min=0.1, max=50.0)
                    ),
                    vol.Optional(
                        "power_sensor",
                        description={"suggested_value": ld.get("power_sensor") or vol.UNDEFINED},
                    ): _entity_selector("sensor"),
                    vol.Optional("priority", default=ld.get("priority", 1)): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=10)
                    ),
                    vol.Optional("mode", default=ld.get("mode", SURPLUS_MODE_REACTIVE)): _select_selector(
                        [SURPLUS_MODE_REACTIVE, SURPLUS_MODE_PREDICTIVE]
                    ),
                    vol.Optional(
                        "battery_on_threshold", default=ld.get("battery_on_threshold", DEFAULT_SURPLUS_BATTERY_ON)
                    ): vol.All(vol.Coerce(float), vol.Range(min=50.0, max=100.0)),
                    vol.Optional(
                        "battery_off_threshold", default=ld.get("battery_off_threshold", DEFAULT_SURPLUS_BATTERY_OFF)
                    ): vol.All(vol.Coerce(float), vol.Range(min=50.0, max=100.0)),
                    vol.Optional(
                        "margin_on_kw", default=ld.get("margin_on_kw", DEFAULT_SURPLUS_MARGIN_ON)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                    vol.Optional(
                        "margin_off_kw", default=ld.get("margin_off_kw", DEFAULT_SURPLUS_MARGIN_OFF)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=5.0)),
                    vol.Optional(
                        "min_switch_interval", default=ld.get("min_switch_interval", DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL)
                    ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                    vol.Optional(
                        "max_outdoor_temp", default=ld.get("max_outdoor_temp", DEFAULT_MAX_OUTDOOR_TEMP)
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=50.0)),
                }
            ),
        )

    async def async_step_surplus_edit_predictive(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Edit predictive schedule for a surplus load."""
        ld = self._pending_load

        if user_input is not None:
            ld["schedule_start_hour"] = int(user_input["schedule_start_hour"])
            ld["schedule_end_hour"] = int(user_input["schedule_end_hour"])
            ld["evaluation_lead_minutes"] = int(user_input.get(
                "evaluation_lead_minutes", str(DEFAULT_PREDICTIVE_LEAD_MINUTES)
            ))
            loads = self._get_surplus_loads()
            loads[self._editing_load_index] = ld
            self._options[CONF_SURPLUS_LOADS] = loads
            return self.async_create_entry(
                title="", data={**self._config_entry.options, **self._options}
            )

        hour_options = [
            selector.SelectOptionDict(value=str(h), label=f"{h:02d}:00")
            for h in range(24)
        ]
        lead_options = [
            selector.SelectOptionDict(value=str(m), label=f"{m} min")
            for m in [15, 30, 45, 60, 90, 120]
        ]
        return self.async_show_form(
            step_id="surplus_edit_predictive",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "schedule_start_hour",
                        default=str(ld.get("schedule_start_hour", DEFAULT_PREDICTIVE_SCHEDULE_START)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=hour_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        "schedule_end_hour",
                        default=str(ld.get("schedule_end_hour", DEFAULT_PREDICTIVE_SCHEDULE_END)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=hour_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        "evaluation_lead_minutes",
                        default=str(ld.get("evaluation_lead_minutes", DEFAULT_PREDICTIVE_LEAD_MINUTES)),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=lead_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_surplus_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Remove a surplus load."""
        loads = self._get_surplus_loads()

        if user_input is not None:
            remove_id = user_input.get("load_to_remove", "")
            new_loads = [ld for ld in loads if ld.get("id") != remove_id]
            self._options[CONF_SURPLUS_LOADS] = new_loads
            return self.async_create_entry(
                title="", data={**self._config_entry.options, **self._options}
            )

        if not loads:
            return await self.async_step_surplus_menu()

        load_options = [
            selector.SelectOptionDict(
                value=ld["id"],
                label=f"{ld['name']} ({ld.get('mode', 'reactive')})",
            )
            for ld in loads
        ]
        return self.async_show_form(
            step_id="surplus_remove",
            data_schema=vol.Schema(
                {
                    vol.Required("load_to_remove"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=load_options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
