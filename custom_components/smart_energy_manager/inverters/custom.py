"""Custom / generic inverter (manual configuration)."""

from __future__ import annotations

from ..models import InverterTemplate
from .base import BaseInverterController
from .select_mixin import SelectInverterMixin

TEMPLATE = InverterTemplate(
    id="custom",
    label="Custom / Other",
    description="Manual configuration for any inverter",
    control_type="select",
    battery_capacity=10.0,
    entity_hints={},
)


class CustomInverter(SelectInverterMixin, BaseInverterController):
    """Custom inverter controller with select-based control."""
