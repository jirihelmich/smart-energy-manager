"""GoodWe inverter (mletenay/home-assistant-goodwe-inverter)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .select_mixin import SelectInverterMixin

TEMPLATE = InverterTemplate(
    id="goodwe_ems",
    label="GoodWe EMS (mletenay)",
    description="GoodWe inverters via EMS mode selection",
    control_type="select",
    mode_self_use="Auto",
    mode_manual="Charge Battery",
    charge_force="Charge Battery",
    charge_stop="Auto",
    battery_capacity=10.0,
    entity_hints={
        "inverter_soc_sensor": "e.g. sensor.goodwe_battery_soc",
        "inverter_capacity_sensor": "e.g. sensor.goodwe_battery_capacity (Wh)",
        "inverter_actual_solar_sensor": "e.g. sensor.goodwe_today_s_pv_production",
        "inverter_mode_select": "e.g. select.goodwe_ems_mode",
        "inverter_charge_command_select": "Same as mode select (e.g. select.goodwe_ems_mode)",
        "inverter_charge_soc_limit": "e.g. number.goodwe_soc_upper_limit",
        "inverter_discharge_min_soc": "e.g. number.goodwe_soc_lower_limit",
    },
)


class GoodWeInverter(SelectInverterMixin, BaseInverterController):
    """GoodWe inverter controller.

    GoodWe uses the EMS mode select for both mode switching and charge command.
    Setting mode to "Charge Battery" starts charging; setting to "Auto" stops.
    The charge_command_select should point to the same entity as mode_select.
    """
