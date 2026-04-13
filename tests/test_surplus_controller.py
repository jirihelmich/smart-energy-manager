"""Tests for the Surplus Load Controller."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

from smart_energy_manager.models import SurplusLoadConfig, SurplusLoadState
from smart_energy_manager.surplus_controller import (
    SurplusLoadController,
    _load_configs_from_options,
)


def _make_coordinator(
    soc: float = 99.0,
    surplus_loads: list | None = None,
    grid_export_sensor: str = "sensor.grid_export_power",
) -> MagicMock:
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.current_soc = soc
    coord._opt = MagicMock(side_effect=lambda key, default: {
        "surplus_loads": surplus_loads or [],
        "grid_export_power_sensor": grid_export_sensor,
        "notify_surplus_load": True,
    }.get(key, default))
    coord.store = MagicMock()
    coord.store.surplus_runtime_history = []
    coord.async_record_surplus_runtime = AsyncMock()
    coord.grid_export_today = 0.0
    # Planner mock for surplus forecast (used by midnight recording)
    forecast_mock = MagicMock()
    forecast_mock.surplus_hours = 7
    coord.planner = MagicMock()
    coord.planner.forecast_today_surplus = MagicMock(return_value=forecast_mock)
    return coord


def _make_hass(
    switch_states: dict[str, str] | None = None,
    grid_export_kw: float = 5.0,
    grid_uom: str = "kW",
) -> MagicMock:
    """Create a mock hass with entity states."""
    switch_states = switch_states or {}
    hass = MagicMock()

    def get_state(entity_id):
        if entity_id == "sensor.grid_export_power":
            s = MagicMock()
            s.state = str(grid_export_kw)
            s.attributes = {"unit_of_measurement": grid_uom}
            return s
        if entity_id in switch_states:
            s = MagicMock()
            s.state = switch_states[entity_id]
            return s
        return None

    hass.states.get = get_state
    hass.services.async_call = AsyncMock()
    return hass


WATER_HEATER_LOAD = {
    "id": "test-water-heater",
    "name": "Water Heater",
    "switch_entity": "switch.water_heater",
    "power_kw": 2.3,
    "priority": 1,
    "battery_on_threshold": 98.0,
    "battery_off_threshold": 95.0,
    "margin_on_kw": 0.3,
    "margin_off_kw": 0.5,
    "min_switch_interval": 300,
}

FLOOR_HEATING_LOAD = {
    "id": "test-floor-heating",
    "name": "Floor Heating",
    "switch_entity": "switch.floor_heating",
    "power_kw": 1.5,
    "priority": 2,
    "battery_on_threshold": 98.0,
    "battery_off_threshold": 95.0,
    "margin_on_kw": 0.3,
    "margin_off_kw": 0.5,
    "min_switch_interval": 300,
}


class TestLoadConfigs:
    """Test config parsing."""

    def test_parse_single_load(self):
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 1
        assert configs[0].name == "Water Heater"
        assert configs[0].power_kw == 2.3
        assert configs[0].priority == 1

    def test_parse_multiple_loads_sorted_by_priority(self):
        coord = _make_coordinator(surplus_loads=[FLOOR_HEATING_LOAD, WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 2
        assert configs[0].name == "Water Heater"  # priority 1
        assert configs[1].name == "Floor Heating"  # priority 2

    def test_parse_empty(self):
        coord = _make_coordinator(surplus_loads=[])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 0

    def test_parse_invalid_item_skipped(self):
        coord = _make_coordinator(surplus_loads=[{"invalid": True}, WATER_HEATER_LOAD])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 1


class TestTrueSurplus:
    """Test true surplus calculation."""

    def test_no_loads_running(self):
        hass = _make_hass(grid_export_kw=3.0)
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        assert ctrl._compute_true_surplus(3.0) == 3.0

    def test_load_running_adds_power(self):
        hass = _make_hass(grid_export_kw=1.0, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()
        # true_surplus = grid_export (1.0) + running load (2.3) = 3.3
        assert ctrl._compute_true_surplus(1.0) == pytest.approx(3.3)

    def test_multiple_loads_running(self):
        hass = _make_hass(
            grid_export_kw=0.5,
            switch_states={"switch.water_heater": "on", "switch.floor_heating": "on"},
        )
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()
        # true_surplus = 0.5 + 2.3 + 1.5 = 4.3
        assert ctrl._compute_true_surplus(0.5) == pytest.approx(4.3)


class TestSurplusTick:
    """Test the main tick logic."""

    @pytest.mark.asyncio
    async def test_turn_on_when_surplus(self):
        """Turn on load when SOC high and surplus exceeds power + margin."""
        hass = _make_hass(grid_export_kw=3.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_called_once_with(
            "switch", "turn_on", {"entity_id": "switch.water_heater"}
        )

    @pytest.mark.asyncio
    async def test_no_turn_on_when_soc_low(self):
        """Don't turn on when SOC below threshold."""
        hass = _make_hass(grid_export_kw=5.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=90.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_turn_on_when_surplus_below_margin(self):
        """Don't turn on when surplus doesn't cover the ON margin."""
        hass = _make_hass(grid_export_kw=0.2, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # 0.2 < 0.3 (margin_on) -> no turn on
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_when_soc_drops(self):
        """Turn off when SOC drops below off threshold (after 3 consecutive ticks)."""
        hass = _make_hass(grid_export_kw=0.5, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=93.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        # Need 3 consecutive off ticks before actual turn-off
        await ctrl.async_on_tick()
        await ctrl.async_on_tick()
        await ctrl.async_on_tick()

        hass.services.async_call.assert_any_call(
            "switch", "turn_off", {"entity_id": "switch.water_heater"}
        )
        assert not ctrl._states["test-water-heater"].is_running

    @pytest.mark.asyncio
    async def test_stay_on_when_true_surplus_ok(self):
        """Stay on when true surplus (accounting for own consumption) is sufficient."""
        # Grid export is 0.5 kW, but load is running (2.3 kW)
        # True surplus = 0.5 + 2.3 = 2.8 kW
        # Stay on: true_surplus (2.8) >= power_kw (2.3) - margin_off (0.5) = 1.8
        hass = _make_hass(grid_export_kw=0.5, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off_when_soc_below_off_threshold(self):
        """Turn off when SOC drops below off threshold (after 3 ticks)."""
        # SOC 90% < off threshold 95% → should turn off
        hass = _make_hass(grid_export_kw=0.0, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(soc=90.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()
        await ctrl.async_on_tick()
        await ctrl.async_on_tick()

        hass.services.async_call.assert_any_call(
            "switch", "turn_off", {"entity_id": "switch.water_heater"}
        )
        assert not ctrl._states["test-water-heater"].is_running

    @pytest.mark.asyncio
    async def test_anti_flap_blocks_switch(self):
        """Anti-flap prevents switching within min_switch_interval."""
        hass = _make_hass(grid_export_kw=3.0, switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        # Set last switch time to just now
        ctrl._states["test-water-heater"].last_switch_time = time.monotonic()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Higher priority load gets surplus first; second load turns on if margin met."""
        # 3.0 kW surplus, water heater margin 0.3 -> on, remaining = 3.0 - 2.3 = 0.7
        # Floor heating margin 0.3 -> 0.7 >= 0.3 -> also on (battery absorbs deficit)
        hass = _make_hass(
            grid_export_kw=3.0,
            switch_states={"switch.water_heater": "off", "switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # Both turn on — water heater first (priority), floor heating has enough margin
        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2
        assert calls[0][0] == ("switch", "turn_on", {"entity_id": "switch.water_heater"})
        assert calls[1][0] == ("switch", "turn_on", {"entity_id": "switch.floor_heating"})

    @pytest.mark.asyncio
    async def test_both_loads_when_enough_surplus(self):
        """Both loads turn on when surplus covers both."""
        # 5.0 kW surplus, water heater needs 2.6, remaining 2.7, floor heating needs 1.8
        hass = _make_hass(
            grid_export_kw=5.0,
            switch_states={"switch.water_heater": "off", "switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD, FLOOR_HEATING_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        calls = hass.services.async_call.call_args_list
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_grid_export_sensor_unavailable(self):
        """Skip tick when grid export sensor is unavailable."""
        hass = MagicMock()
        hass.states.get = MagicMock(return_value=None)
        hass.services.async_call = AsyncMock()
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_watts_conversion(self):
        """Grid export in W is converted to kW."""
        hass = _make_hass(grid_export_kw=3000, grid_uom="W", switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(soc=99.0, surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        await ctrl.async_on_tick()

        # 3000 W = 3.0 kW, enough for 2.3 + 0.3 = 2.6
        hass.services.async_call.assert_called_once()


class TestRestoreState:
    """Test state restoration."""

    def test_restore_states(self):
        hass = _make_hass(switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        ctrl.restore_states({
            "switch.water_heater": {
                "last_switch_time": 1000.0,
                "daily_runtime_seconds": 3600.0,
            }
        })

        st = ctrl.states["test-water-heater"]
        assert st.last_switch_time == 0.0  # monotonic not restored (invalid after restart)
        assert st.daily_runtime_seconds == 3600.0

    def test_restore_unknown_entity_ignored(self):
        hass = _make_hass(switch_states={"switch.water_heater": "off"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()

        # Should not raise
        ctrl.restore_states({"switch.unknown": {"last_switch_time": 1000.0}})


class TestSensorData:
    """Test sensor data generation."""

    def test_no_loads(self):
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        data = ctrl.get_sensor_data()
        assert data["surplus_active_loads"] == 0

    def test_with_running_load(self):
        hass = _make_hass(grid_export_kw=1.0, switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._sync_actual_switch_states()

        data = ctrl.get_sensor_data()
        assert data["surplus_active_loads"] == 1
        assert data["surplus_active_load_names"] == "Water Heater"
        assert data["surplus_total_power_kw"] == 2.3
        assert data["surplus_true_surplus_kw"] == pytest.approx(3.3)


class TestMidnight:
    """Test midnight reset."""

    @pytest.mark.asyncio
    async def test_midnight_records_runtime(self):
        hass = _make_hass(switch_states={"switch.water_heater": "on"})
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-water-heater"].daily_runtime_seconds = 7200.0

        await ctrl.async_on_midnight()

        coord.async_record_surplus_runtime.assert_called_once_with(
            {"Water Heater": 2.0}, surplus_hours=0, energy_data={},  # 7200s = 2h, surplus_hours=0 (no ticks ran)
            grid_export_kwh=0.0,
        )
        # Runtime reset
        assert ctrl._states["test-water-heater"].daily_runtime_seconds == 0.0

    @pytest.mark.asyncio
    async def test_midnight_resets_predictive_state(self):
        """Midnight resets predictive approval for next day."""
        load = {**FLOOR_HEATING_LOAD, "mode": "predictive", "schedule_start_hour": 5, "schedule_end_hour": 8}
        hass = _make_hass(switch_states={"switch.floor_heating": "off"})
        coord = _make_coordinator(surplus_loads=[load])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        # Simulate that it was evaluated today
        ctrl._states["test-floor-heating"].predictive_approved = True
        ctrl._states["test-floor-heating"].predictive_aborted = True

        await ctrl.async_on_midnight()

        assert ctrl._states["test-floor-heating"].predictive_approved is None
        assert ctrl._states["test-floor-heating"].predictive_aborted is False


class TestUtilizationFactors:
    """Tests for historical utilization factor computation."""

    def test_no_history_returns_empty(self):
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        assert ctrl.get_utilization_factors() == {}

    def test_single_day_history(self):
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        coord.store.surplus_runtime_history = [
            {"date": "2026-03-11", "loads": {"Water Heater": 4.0}, "surplus_hours": 8},
        ]
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        factors = ctrl.get_utilization_factors()
        assert factors == {"Water Heater": 0.5}  # 4h / 8h

    def test_multi_day_average(self):
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        coord.store.surplus_runtime_history = [
            {"date": "2026-03-11", "loads": {"Water Heater": 4.0}, "surplus_hours": 8},
            {"date": "2026-03-10", "loads": {"Water Heater": 6.0}, "surplus_hours": 8},
        ]
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        factors = ctrl.get_utilization_factors()
        # (4/8 + 6/8) / 2 = (0.5 + 0.75) / 2 = 0.625
        assert factors == {"Water Heater": 0.62}

    def test_capped_at_one(self):
        """Runtime can exceed surplus hours (e.g. manual runs), cap at 1.0."""
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        coord.store.surplus_runtime_history = [
            {"date": "2026-03-11", "loads": {"Water Heater": 12.0}, "surplus_hours": 8},
        ]
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        factors = ctrl.get_utilization_factors()
        assert factors == {"Water Heater": 1.0}

    def test_zero_surplus_hours_skipped(self):
        """Days with zero surplus hours should not affect utilization."""
        hass = _make_hass()
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD])
        coord.store.surplus_runtime_history = [
            {"date": "2026-03-11", "loads": {"Water Heater": 0.0}, "surplus_hours": 0},
            {"date": "2026-03-10", "loads": {"Water Heater": 4.0}, "surplus_hours": 8},
        ]
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        factors = ctrl.get_utilization_factors()
        assert factors == {"Water Heater": 0.5}


PREDICTIVE_FLOOR_HEATING = {
    **FLOOR_HEATING_LOAD,
    "mode": "predictive",
    "schedule_start_hour": 5,
    "schedule_end_hour": 8,
    "evaluation_lead_minutes": 30,
}


class TestPredictiveLoadConfig:
    """Test predictive load config parsing."""

    def test_parse_predictive_fields(self):
        coord = _make_coordinator(surplus_loads=[PREDICTIVE_FLOOR_HEATING])
        configs = _load_configs_from_options(coord)
        assert len(configs) == 1
        assert configs[0].mode == "predictive"
        assert configs[0].schedule_start_hour == 5
        assert configs[0].schedule_end_hour == 8
        assert configs[0].evaluation_lead_minutes == 30

    def test_reactive_and_predictive_mixed(self):
        coord = _make_coordinator(surplus_loads=[WATER_HEATER_LOAD, PREDICTIVE_FLOOR_HEATING])
        ctrl = SurplusLoadController(_make_hass(), coord)
        ctrl.load_configs()

        assert len(ctrl._reactive_configs()) == 1
        assert ctrl._reactive_configs()[0].name == "Water Heater"
        assert len(ctrl._predictive_configs()) == 1
        assert ctrl._predictive_configs()[0].name == "Floor Heating"


class TestPredictiveTick:
    """Test predictive load tick behavior."""

    def _make_dt_util_mock(self, hour, minute=0):
        """Create a mock for _get_now that returns specific time."""
        from datetime import datetime
        return datetime(2026, 3, 9, hour, minute, 0)

    @pytest.mark.asyncio
    async def test_predictive_turn_on_at_schedule_start(self):
        """Approved predictive load turns on when schedule starts."""
        hass = _make_hass(
            grid_export_kw=0.0,
            switch_states={"switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=70.0, surplus_loads=[PREDICTIVE_FLOOR_HEATING])
        coord.planner = None  # No planner means no evaluation — set approved manually
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-floor-heating"].predictive_approved = True
        ctrl._get_now = lambda: self._make_dt_util_mock(5, 0)

        await ctrl.async_on_tick()

        # Should turn on the predictive load
        calls = [c for c in hass.services.async_call.call_args_list
                 if c[0][1] == "turn_on" and c[0][2]["entity_id"] == "switch.floor_heating"]
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_predictive_not_turned_on_if_not_approved(self):
        """Predictive load stays off if not approved."""
        hass = _make_hass(
            grid_export_kw=0.0,
            switch_states={"switch.floor_heating": "off"},
        )
        coord = _make_coordinator(soc=70.0, surplus_loads=[PREDICTIVE_FLOOR_HEATING])
        coord.planner = None
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-floor-heating"].predictive_approved = False
        ctrl._get_now = lambda: self._make_dt_util_mock(5, 0)

        await ctrl.async_on_tick()

        # Should NOT turn on
        calls = [c for c in hass.services.async_call.call_args_list
                 if c[0][2].get("entity_id") == "switch.floor_heating"]
        assert len(calls) == 0

    @pytest.mark.asyncio
    async def test_predictive_turn_off_at_schedule_end(self):
        """Running predictive load turns off when schedule ends."""
        hass = _make_hass(
            grid_export_kw=0.0,
            switch_states={"switch.floor_heating": "on"},
        )
        coord = _make_coordinator(soc=50.0, surplus_loads=[PREDICTIVE_FLOOR_HEATING])
        coord.planner = None
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-floor-heating"].predictive_approved = True
        ctrl._get_now = lambda: self._make_dt_util_mock(8, 0)  # At schedule end

        await ctrl.async_on_tick()

        # Should turn off
        calls = [c for c in hass.services.async_call.call_args_list
                 if c[0][1] == "turn_off" and c[0][2]["entity_id"] == "switch.floor_heating"]
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_predictive_does_not_affect_reactive(self):
        """Reactive loads still work normally alongside predictive loads."""
        hass = _make_hass(
            grid_export_kw=3.0,
            switch_states={"switch.water_heater": "off", "switch.floor_heating": "off"},
        )
        coord = _make_coordinator(
            soc=99.0,
            surplus_loads=[WATER_HEATER_LOAD, PREDICTIVE_FLOOR_HEATING],
        )
        coord.planner = None
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-floor-heating"].predictive_approved = False  # Denied
        ctrl._get_now = lambda: self._make_dt_util_mock(10, 0)

        await ctrl.async_on_tick()

        # Water heater (reactive) should turn on, floor heating (predictive) should not
        calls = hass.services.async_call.call_args_list
        on_calls = [c for c in calls if c[0][1] == "turn_on"]
        assert len(on_calls) == 1
        assert on_calls[0][0][2]["entity_id"] == "switch.water_heater"


class TestSensorDataPredictive:
    """Test sensor data includes predictive info."""

    def test_load_details_include_predictive_fields(self):
        hass = _make_hass(switch_states={"switch.floor_heating": "off"})
        coord = _make_coordinator(surplus_loads=[PREDICTIVE_FLOOR_HEATING])
        ctrl = SurplusLoadController(hass, coord)
        ctrl.load_configs()
        ctrl._states["test-floor-heating"].predictive_approved = True

        data = ctrl.get_sensor_data()
        details = data["surplus_load_details"]
        assert len(details) == 1
        assert details[0]["mode"] == "predictive"
        assert details[0]["schedule"] == "05:00-08:00"
        assert details[0]["approved"] is True
        assert details[0]["aborted"] is False
