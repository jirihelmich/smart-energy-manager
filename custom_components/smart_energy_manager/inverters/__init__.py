"""Inverter controller registry and factory.

Adding a new inverter:
1. Create inverters/<name>.py with TEMPLATE and class
2. Import and register below
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import CONTROL_TYPE_EMS_POWER
from ..models import InverterTemplate
from .base import BaseInverterController, InverterCommandError, MODBUS_SETTLE_DELAY
from .custom import TEMPLATE as _CUSTOM_TEMPLATE, CustomInverter
from .goodwe import TEMPLATE as _GOODWE_TEMPLATE, GoodWeInverter
from .huawei import TEMPLATE as _HUAWEI_TEMPLATE, HuaweiInverter
from .solaredge import TEMPLATE as _SOLAREDGE_TEMPLATE, SolarEdgeInverter
from .solax import TEMPLATE as _SOLAX_TEMPLATE, SolaxInverter
from .wattsonic import TEMPLATE as _WATTSONIC_TEMPLATE, WattsonicInverter

# --- Template registry ---

INVERTER_TEMPLATES: dict[str, InverterTemplate] = {
    _SOLAX_TEMPLATE.id: _SOLAX_TEMPLATE,
    _GOODWE_TEMPLATE.id: _GOODWE_TEMPLATE,
    _SOLAREDGE_TEMPLATE.id: _SOLAREDGE_TEMPLATE,
    _HUAWEI_TEMPLATE.id: _HUAWEI_TEMPLATE,
    _WATTSONIC_TEMPLATE.id: _WATTSONIC_TEMPLATE,
    _CUSTOM_TEMPLATE.id: _CUSTOM_TEMPLATE,
}

# --- Controller registry ---

_CONTROLLER_REGISTRY: dict[str, type[BaseInverterController]] = {
    _SOLAX_TEMPLATE.id: SolaxInverter,
    _GOODWE_TEMPLATE.id: GoodWeInverter,
    _SOLAREDGE_TEMPLATE.id: SolarEdgeInverter,
    _HUAWEI_TEMPLATE.id: HuaweiInverter,
    _WATTSONIC_TEMPLATE.id: WattsonicInverter,
    _CUSTOM_TEMPLATE.id: CustomInverter,
}


def get_template(template_id: str) -> InverterTemplate:
    """Get a template by ID, falling back to custom."""
    return INVERTER_TEMPLATES.get(template_id, INVERTER_TEMPLATES["custom"])


def create_inverter_controller(
    hass: HomeAssistant,
    config: dict[str, Any],
    template_id: str = "custom",
    control_type: str | None = None,
) -> BaseInverterController:
    """Create an inverter controller for the given template.

    Falls back to CustomInverter for unknown template IDs.
    If control_type is explicitly "ems_power" but template_id is unknown,
    creates a WattsonicInverter (the only EMS implementation).
    """
    cls = _CONTROLLER_REGISTRY.get(template_id)
    if cls is None:
        # Fallback: use control_type to pick EMS vs select
        if control_type == CONTROL_TYPE_EMS_POWER:
            cls = WattsonicInverter
        else:
            cls = CustomInverter
    return cls(hass, config)


__all__ = [
    "BaseInverterController",
    "InverterCommandError",
    "INVERTER_TEMPLATES",
    "MODBUS_SETTLE_DELAY",
    "create_inverter_controller",
    "get_template",
    "CustomInverter",
    "GoodWeInverter",
    "HuaweiInverter",
    "SolaxInverter",
    "SolarEdgeInverter",
    "WattsonicInverter",
]
