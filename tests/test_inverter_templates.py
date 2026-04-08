"""Tests for inverter templates."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Mock HA modules before importing the package
for mod_name in [
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.storage",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.selector",
    "homeassistant.helpers.event",
    "homeassistant.components",
    "homeassistant.components.switch",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.components.number",
    "homeassistant.data_entry_flow",
    "homeassistant.util",
    "homeassistant.util.dt",
    "voluptuous",
]:
    sys.modules.setdefault(mod_name, MagicMock())

_COMPONENTS_DIR = Path(__file__).parent.parent / "custom_components"
sys.path.insert(0, str(_COMPONENTS_DIR))

from smart_energy_manager.inverters import INVERTER_TEMPLATES, get_template
from smart_energy_manager.models import InverterTemplate


class TestInverterTemplates:
    """Tests for the INVERTER_TEMPLATES registry."""

    def test_all_templates_have_required_fields(self) -> None:
        """Every template must have all required string fields set."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            assert isinstance(tmpl, InverterTemplate), f"{tid}: not an InverterTemplate"
            assert isinstance(tmpl.id, str), f"{tid}.id should be str"
            assert isinstance(tmpl.label, str), f"{tid}.label should be str"
            assert isinstance(tmpl.description, str), f"{tid}.description should be str"
            assert isinstance(tmpl.control_type, str), f"{tid}.control_type should be str"
            assert isinstance(tmpl.battery_capacity, float), f"{tid}.battery_capacity should be float"
            assert tmpl.battery_capacity > 0, f"{tid}.battery_capacity must be positive"
            assert isinstance(tmpl.entity_hints, dict), f"{tid}.entity_hints should be dict"

    def test_all_templates_id_matches_key(self) -> None:
        """Template .id must match the dict key."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            assert tmpl.id == tid, f"Key {tid!r} != template.id {tmpl.id!r}"

    def test_control_type_valid(self) -> None:
        """All templates must have a valid control_type."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            assert tmpl.control_type in ("select", "ems_power"), \
                f"{tid}.control_type is {tmpl.control_type!r}, expected 'select' or 'ems_power'"

    def test_custom_template_has_empty_strings(self) -> None:
        """Custom template should have empty mode/command strings."""
        custom = INVERTER_TEMPLATES["custom"]
        assert custom.mode_self_use == ""
        assert custom.mode_manual == ""
        assert custom.charge_force == ""
        assert custom.charge_stop == ""
        assert custom.entity_hints == {}
        assert custom.control_type == "select"

    def test_solax_template_correct_values(self) -> None:
        """Solax template should have the known Modbus mode strings."""
        solax = INVERTER_TEMPLATES["solax_modbus"]
        assert solax.mode_self_use == "Self Use Mode"
        assert solax.mode_manual == "Manual Mode"
        assert solax.charge_force == "Force Charge"
        assert solax.charge_stop == "Stop Charge and Discharge"
        assert solax.battery_capacity == 15.0
        assert len(solax.entity_hints) == 7
        assert solax.control_type == "select"

    def test_wattsonic_template_correct_values(self) -> None:
        """Wattsonic template should have EMS control type and correct register values."""
        ws = INVERTER_TEMPLATES["wattsonic_ems"]
        assert ws.control_type == "ems_power"
        assert ws.ems_charge_mode_value == 771  # 0x0303
        assert ws.ems_normal_mode_value == 257   # 0x0101
        assert ws.battery_capacity == 10.0
        assert len(ws.entity_hints) == 7
        # EMS templates don't use mode strings
        assert ws.mode_self_use == ""
        assert ws.mode_manual == ""

    def test_non_custom_select_templates_have_nonempty_modes(self) -> None:
        """All select-type templates except 'custom' must have non-empty mode strings."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            if tid == "custom" or tmpl.control_type != "select":
                continue
            assert tmpl.mode_self_use, f"{tid}.mode_self_use is empty"
            assert tmpl.mode_manual, f"{tid}.mode_manual is empty"
            assert tmpl.charge_force, f"{tid}.charge_force is empty"
            assert tmpl.charge_stop, f"{tid}.charge_stop is empty"

    def test_ems_templates_have_mode_values(self) -> None:
        """EMS-type templates must have non-zero mode values."""
        for tid, tmpl in INVERTER_TEMPLATES.items():
            if tmpl.control_type != "ems_power":
                continue
            assert tmpl.ems_charge_mode_value > 0, f"{tid}.ems_charge_mode_value must be > 0"
            assert tmpl.ems_normal_mode_value > 0, f"{tid}.ems_normal_mode_value must be > 0"

    def test_get_template_known_id(self) -> None:
        """get_template returns the correct template for a known ID."""
        assert get_template("solax_modbus") is INVERTER_TEMPLATES["solax_modbus"]
        assert get_template("custom") is INVERTER_TEMPLATES["custom"]
        assert get_template("wattsonic_ems") is INVERTER_TEMPLATES["wattsonic_ems"]

    def test_get_template_unknown_id_falls_back_to_custom(self) -> None:
        """get_template returns custom for unknown IDs."""
        result = get_template("nonexistent_inverter")
        assert result is INVERTER_TEMPLATES["custom"]

    def test_expected_template_count(self) -> None:
        """We expect exactly 6 templates (4 select integrations + 1 EMS + custom)."""
        assert len(INVERTER_TEMPLATES) == 6

    def test_templates_are_frozen(self) -> None:
        """Templates should be immutable (frozen dataclass)."""
        tmpl = INVERTER_TEMPLATES["solax_modbus"]
        with pytest.raises(AttributeError):
            tmpl.mode_self_use = "changed"  # type: ignore[misc]
