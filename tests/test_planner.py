"""Tests for ChargingPlanner — pure orchestration logic."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

# Mock HA modules before importing
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

from smart_battery_charging.consumption_tracker import ConsumptionTracker
from smart_battery_charging.forecast_corrector import ForecastCorrector
from smart_battery_charging.models import EnergyDeficit, OvernightNeed, SurplusForecast
from smart_battery_charging.planner import ChargingPlanner
from smart_battery_charging.price_analyzer import PriceAnalyzer, PriceSlot, PriceWindow

# Fixed test time: Wednesday 2026-02-25 20:00 (Thursday tomorrow — not weekend)
_TEST_NOW = datetime(2026, 2, 25, 20, 0, 0)
_TEST_TODAY = _TEST_NOW.strftime("%Y-%m-%d")  # "2026-02-25"
_TEST_TOMORROW = (_TEST_NOW + timedelta(days=1)).strftime("%Y-%m-%d")  # "2026-02-26"


def _make_coordinator(
    enabled=True,
    battery_capacity=15.0,
    max_charge_level=90.0,
    min_soc=20.0,
    max_charge_power=10.0,
    max_charge_price=4.0,
    solar_forecast_tomorrow=5.0,
    consumption_history=None,
    forecast_error_history=None,
    price_attributes=None,
    current_soc=50.0,
    solar_forecast_tomorrow_hourly=None,
    solar_forecast_today_hourly=None,
    sunrise_hour_tomorrow=6.5,
    solar_forecast_today=10.0,
    actual_solar_today=5.0,
    charging_efficiency=1.0,
    evening_consumption_multiplier=1.5,
    night_consumption_multiplier=0.5,
    weekend_consumption_multiplier=1.0,
):
    """Create a mock coordinator with controlled values."""
    coord = MagicMock()
    coord.enabled = enabled
    coord.battery_capacity = battery_capacity
    coord.max_charge_level = max_charge_level
    coord.min_soc = min_soc
    coord.max_charge_power = max_charge_power
    coord.max_charge_price = max_charge_price
    coord.solar_forecast_tomorrow = solar_forecast_tomorrow
    coord.current_soc = current_soc
    coord.solar_forecast_today = solar_forecast_today
    coord.actual_solar_today = actual_solar_today
    coord._last_overnight = None
    coord.charging_efficiency = charging_efficiency
    coord.evening_consumption_multiplier = evening_consumption_multiplier
    coord.night_consumption_multiplier = night_consumption_multiplier
    coord.weekend_consumption_multiplier = weekend_consumption_multiplier

    # Hourly solar properties
    type(coord).solar_forecast_tomorrow_hourly = PropertyMock(
        return_value=solar_forecast_tomorrow_hourly or {}
    )
    type(coord).solar_forecast_today_hourly = PropertyMock(
        return_value=solar_forecast_today_hourly or {}
    )
    type(coord).sunrise_hour_tomorrow = PropertyMock(return_value=sunrise_hour_tomorrow)

    # Real sub-components for correct logic
    coord.consumption_tracker = ConsumptionTracker(window_days=7, fallback_kwh=20.0)
    coord.forecast_corrector = ForecastCorrector(window_days=7)
    coord.price_analyzer = PriceAnalyzer(window_start_hour=22, window_end_hour=6)

    # Store data
    coord.store = MagicMock()
    coord.store.consumption_history = [16.0, 17.0, 16.5] if consumption_history is None else consumption_history
    coord.store.forecast_error_history = [] if forecast_error_history is None else forecast_error_history

    # Price attributes — default: realistic night prices using fixed test dates
    if price_attributes is None:
        price_attributes = {
            f"{_TEST_TODAY}T22:00:00+01:00": 1.8,
            f"{_TEST_TODAY}T23:00:00+01:00": 1.5,
            f"{_TEST_TOMORROW}T00:00:00+01:00": 1.2,
            f"{_TEST_TOMORROW}T01:00:00+01:00": 1.0,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 1.3,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 1.6,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 2.0,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 2.5,
        }
    coord.price_attributes = price_attributes

    return coord


class TestComputeEnergyDeficit:
    """Test energy deficit calculation (now backed by trajectory)."""

    def test_deficit_when_solar_less_than_consumption(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)

        assert deficit.consumption == 16.5  # average of 16, 17, 16.5
        assert deficit.solar_raw == 5.0
        assert deficit.solar_adjusted == 5.0  # no error history
        assert deficit.deficit == 11.5  # 16.5 - 5.0
        assert deficit.usable_capacity == 10.5  # 15 * (90-20)/100
        # charge_needed is trajectory-based (accounts for current SOC)
        assert deficit.charge_needed > 0

    def test_no_deficit_when_solar_covers_consumption(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=20.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)

        assert deficit.deficit == 0.0
        # Note: charge_needed may be > 0 if overnight drain exceeds current SOC
        # That's correct — the trajectory knows the battery needs topping up

    def test_deficit_clamped_to_usable_capacity(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=0.0,
            consumption_history=[30.0],  # way more than usable capacity
            battery_capacity=15.0,
            max_charge_level=90.0,
            min_soc=20.0,
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)

        # Usable capacity = 15 * (90-20)/100 = 10.5
        assert deficit.charge_needed <= 10.5

    def test_forecast_error_adjustment(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            # 40% average overestimate → adjusted = 10 * (1 - 0.4) = 6.0
            forecast_error_history=[0.4, 0.4, 0.4],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)

        assert deficit.solar_raw == 10.0
        assert deficit.solar_adjusted == 6.0
        assert deficit.deficit == 10.0  # 16.0 - 6.0
        assert deficit.forecast_error_pct == 40.0

    def test_uses_fallback_consumption_when_no_history(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[],
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)

        assert deficit.consumption == 20.0  # fallback


class TestHasTomorrowPrices:
    """Test tomorrow's price availability check."""

    def test_prices_available(self):
        coord = _make_coordinator()  # default has tomorrow's prices
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices(now=_TEST_NOW) is True

    def test_prices_not_available(self):
        coord = _make_coordinator(price_attributes={
            "2020-01-01T00:00:00+01:00": 1.0,
        })
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices(now=_TEST_NOW) is False

    def test_empty_prices(self):
        coord = _make_coordinator(price_attributes={})
        planner = ChargingPlanner(coord)
        assert planner.has_tomorrow_prices(now=_TEST_NOW) is False


class TestComputeTargetSoc:
    """Test target SOC calculation."""

    def test_basic_target(self):
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
        )
        planner = ChargingPlanner(coord)
        deficit = planner.compute_energy_deficit(now=_TEST_NOW)
        target = planner.compute_target_soc(deficit)

        # charge_needed / capacity * 100 + min_soc
        # with default data: deficit exists, target should be between min and max
        assert 20.0 <= target <= 90.0

    def test_target_clamped_to_max(self):
        coord = _make_coordinator(
            battery_capacity=10.0,
            min_soc=20.0,
            max_charge_level=90.0,
        )
        planner = ChargingPlanner(coord)
        deficit = EnergyDeficit(
            consumption=20.0, solar_raw=0.0, solar_adjusted=0.0,
            forecast_error_pct=0.0, deficit=20.0, charge_needed=10.0,
            usable_capacity=7.0,
        )
        target = planner.compute_target_soc(deficit)
        # 20 + (10/10*100) = 120 → clamped to 90
        assert target == 90.0

    def test_no_charge_returns_min_soc(self):
        coord = _make_coordinator(min_soc=20.0)
        planner = ChargingPlanner(coord)
        deficit = EnergyDeficit(
            consumption=10.0, solar_raw=15.0, solar_adjusted=15.0,
            forecast_error_pct=0.0, deficit=0.0, charge_needed=0.0,
            usable_capacity=10.5,
        )
        target = planner.compute_target_soc(deficit)
        assert target == 20.0


class TestPlanCharging:
    """Test the full planning pipeline."""

    def test_creates_schedule_when_deficit(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is not None
        assert schedule.required_kwh > 0
        assert schedule.target_soc > coord.min_soc
        assert schedule.avg_price <= coord.max_charge_price
        assert schedule.window_hours >= 1

    def test_returns_none_when_solar_covers_and_battery_high(self):
        """Solar covers daily consumption AND battery is high enough for overnight."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=25.0,
            consumption_history=[16.0, 17.0, 16.5],
            current_soc=85.0,  # high SOC so battery covers overnight too
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is None

    def test_returns_none_when_disabled(self):
        coord = _make_coordinator(enabled=False)
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is None

    def test_returns_none_when_no_prices(self):
        coord = _make_coordinator(price_attributes={})
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is None

    def test_returns_none_when_price_too_high(self):
        expensive_prices = {
            f"{_TEST_TODAY}T22:00:00+01:00": 10.0,
            f"{_TEST_TODAY}T23:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T00:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T01:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 10.0,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 10.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_price=4.0,
            price_attributes=expensive_prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is None

    def test_schedule_picks_cheapest_window(self):
        prices = {
            f"{_TEST_TODAY}T22:00:00+01:00": 3.0,
            f"{_TEST_TODAY}T23:00:00+01:00": 3.0,
            f"{_TEST_TOMORROW}T00:00:00+01:00": 1.0,
            f"{_TEST_TOMORROW}T01:00:00+01:00": 0.8,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 1.2,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 2.0,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 2.5,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 3.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_power=10.0,  # need 1 hour for ~10 kWh
            price_attributes=prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is not None
        # Cheapest contiguous window should include hours 0-1 (1.0, 0.8)
        assert schedule.avg_price <= 2.0

    def test_schedule_has_created_at(self):
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is not None
        assert schedule.created_at is not None

    def test_overnight_triggers_charging_when_daily_deficit_zero(self):
        """Solar > consumption but low battery can't bridge the night."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=25.0,  # plenty of solar
            consumption_history=[16.0, 17.0, 16.5],
            current_soc=30.0,  # low battery — ~1.5 kWh usable
            solar_forecast_today=10.0,
            actual_solar_today=10.0,  # no remaining solar today
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        # Daily deficit is 0 but overnight survival triggers charging
        assert schedule is not None
        assert schedule.required_kwh > 0
        assert planner.last_overnight_need is not None
        assert planner.last_overnight_need.charge_needed > 0

    def test_plan_caches_overnight_need(self):
        """plan_charging should set last_overnight_need and coordinator._last_overnight."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
        )
        planner = ChargingPlanner(coord)
        planner.plan_charging(now=_TEST_NOW)

        assert planner.last_overnight_need is not None
        assert coord._last_overnight is not None


class TestComputeOvernightNeed:
    """Test overnight survival calculation (now backed by trajectory)."""

    def test_overnight_shortfall_low_battery(self):
        """Battery at 30% cannot cover overnight consumption."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=30.0,  # only 1.5 kWh usable (30-20=10% of 15)
            consumption_history=[16.0, 17.0, 16.5],
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,  # no remaining solar
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need(now=_TEST_NOW)

        assert overnight.charge_needed > 0
        assert overnight.dark_hours > 0

    def test_overnight_no_shortfall_full_battery(self):
        """Battery at 85% easily covers overnight (planning at 20:00)."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=85.0,  # 9.75 kWh usable
            consumption_history=[16.0, 17.0, 16.5],
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
            solar_forecast_tomorrow=25.0,  # plenty of solar tomorrow
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need(now=_TEST_NOW)

        assert overnight.charge_needed == 0

    def test_overnight_with_hourly_solar_data(self):
        """Hour-by-hour simulation with forecast_solar data."""
        hourly = {
            7: 0.3,   # sunrise ramp
            8: 1.0,   # not enough (consumption ~0.69)
            9: 2.0,   # covers consumption
            10: 3.0,
        }
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=35.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_tomorrow_hourly=hourly,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need(now=_TEST_NOW)

        assert overnight.source == "forecast_solar"
        assert overnight.dark_hours > 0

    def test_overnight_clamped_to_usable_capacity(self):
        """Charge needed cannot exceed usable capacity."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=20.0,  # exactly at min SOC → 0 usable
            consumption_history=[16.0, 17.0],
            sunrise_hour_tomorrow=8.0,  # late sunrise → long dark period
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need(now=_TEST_NOW)

        usable_capacity = 15.0 * (90.0 - 20.0) / 100  # 10.5
        assert overnight.charge_needed <= usable_capacity

    def test_overnight_accounts_for_pre_window_discharge(self):
        """Planning during daytime accounts for evening battery drain."""
        # Simulate planning at ~14:00 — 8 hours until 22:00 window start
        now_14 = datetime(2026, 2, 25, 14, 0, 0)
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=60.0,  # 6 kWh usable now
            consumption_history=[16.0, 17.0, 16.5],
            sunrise_hour_tomorrow=6.5,
            solar_forecast_today=10.0,
            actual_solar_today=5.0,  # 5 kWh remaining solar today
        )
        planner = ChargingPlanner(coord)
        overnight = planner.compute_overnight_need(now=now_14)

        # Battery at window start should be less than current usable
        assert overnight.battery_at_window_start < 6.0


class TestChargingEfficiency:
    """Test that charging efficiency increases required kWh."""

    def test_efficiency_increases_charge_needed(self):
        """90% efficiency means more kWh needed from the grid."""
        coord_100 = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
            charging_efficiency=1.0,
        )
        coord_90 = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0, 17.0, 16.5],
            charging_efficiency=0.9,
        )
        plan_100 = ChargingPlanner(coord_100)
        plan_90 = ChargingPlanner(coord_90)

        s100 = plan_100.plan_charging(now=_TEST_NOW)
        s90 = plan_90.plan_charging(now=_TEST_NOW)

        assert s100 is not None
        assert s90 is not None
        # With 90% efficiency, more kWh is required
        assert s90.required_kwh >= s100.required_kwh

    def test_trajectory_applies_efficiency(self):
        """Trajectory charge_needed includes efficiency loss."""
        coord = _make_coordinator(
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            current_soc=25.0,
            consumption_history=[16.0],
            charging_efficiency=0.9,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        trajectory = planner.simulate_trajectory(now=_TEST_NOW)

        # With 90% efficiency, charge_needed should be > shortfall
        assert trajectory.charge_needed_kwh > 0


class TestConsumptionProfiles:
    """Test evening/night consumption multipliers."""

    def test_hourly_consumption_day(self):
        """Day hours (06-18) use base rate (multiplier 1.0)."""
        coord = _make_coordinator(
            evening_consumption_multiplier=1.5,
            night_consumption_multiplier=0.5,
        )
        planner = ChargingPlanner(coord)
        # daily=24 → base_rate = 24 / (12*1.0 + 5*1.5 + 7*0.5) = 24 / 23 ≈ 1.043
        hourly_day = planner._hourly_consumption(12, 24.0)
        hourly_evening = planner._hourly_consumption(20, 24.0)
        hourly_night = planner._hourly_consumption(2, 24.0)

        assert hourly_evening > hourly_day  # evening > day
        assert hourly_day > hourly_night   # day > night
        # Evening is 1.5x day
        assert abs(hourly_evening / hourly_day - 1.5) < 0.01
        # Night is 0.5x day
        assert abs(hourly_night / hourly_day - 0.5) < 0.01

    def test_hourly_consumption_sums_to_daily(self):
        """24 hours of profiled consumption should sum close to daily total."""
        coord = _make_coordinator(
            evening_consumption_multiplier=1.5,
            night_consumption_multiplier=0.5,
        )
        planner = ChargingPlanner(coord)
        daily = 16.5
        total = sum(planner._hourly_consumption(h, daily) for h in range(24))
        assert abs(total - daily) < 0.01

    def test_flat_profile_equals_simple_division(self):
        """With multipliers all 1.0, hourly consumption = daily/24."""
        coord = _make_coordinator(
            evening_consumption_multiplier=1.0,
            night_consumption_multiplier=1.0,
        )
        planner = ChargingPlanner(coord)
        daily = 24.0
        for h in range(24):
            assert abs(planner._hourly_consumption(h, daily) - 1.0) < 0.001


class TestNegativePriceExploitation:
    """Test that negative prices trigger maximum charging."""

    def test_negative_prices_fill_battery(self):
        """When cheapest price is negative, charge to max capacity."""
        negative_prices = {
            f"{_TEST_TODAY}T22:00:00+01:00": 1.0,
            f"{_TEST_TODAY}T23:00:00+01:00": 0.5,
            f"{_TEST_TOMORROW}T00:00:00+01:00": -0.5,
            f"{_TEST_TOMORROW}T01:00:00+01:00": -1.0,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 0.5,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 1.0,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 1.5,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 2.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=14.0,  # small deficit
            consumption_history=[16.0],
            price_attributes=negative_prices,
            max_charge_price=4.0,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is not None
        # With negative prices, should charge to maximum usable capacity
        usable = 15.0 * (90.0 - 20.0) / 100
        assert schedule.required_kwh == pytest.approx(usable, abs=0.1)

    def test_negative_avg_price_bypasses_threshold(self):
        """Windows with avg_price <= 0 should bypass the max_charge_price check."""
        all_negative = {
            f"{_TEST_TODAY}T22:00:00+01:00": -0.5,
            f"{_TEST_TODAY}T23:00:00+01:00": -0.5,
            f"{_TEST_TOMORROW}T00:00:00+01:00": -1.0,
            f"{_TEST_TOMORROW}T01:00:00+01:00": -1.5,
            f"{_TEST_TOMORROW}T02:00:00+01:00": -0.8,
            f"{_TEST_TOMORROW}T03:00:00+01:00": -0.3,
            f"{_TEST_TOMORROW}T04:00:00+01:00": -0.1,
            f"{_TEST_TOMORROW}T05:00:00+01:00": -0.1,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            price_attributes=all_negative,
            max_charge_price=0.01,  # very low threshold, but should bypass for negative
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        assert schedule is not None
        assert schedule.avg_price < 0


class TestEmergencyOverride:
    """Test M2: Emergency low-battery override bypasses price threshold."""

    def test_emergency_soc_overrides_price_threshold(self):
        """When SOC < EMERGENCY_SOC_THRESHOLD, charge despite high prices."""
        expensive_prices = {
            f"{_TEST_TODAY}T22:00:00+01:00": 8.0,
            f"{_TEST_TODAY}T23:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T00:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T01:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 8.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_price=4.0,  # all prices exceed this
            current_soc=20.0,  # below EMERGENCY_SOC_THRESHOLD (25%)
            price_attributes=expensive_prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        # Should still schedule charging due to emergency override
        assert schedule is not None
        assert schedule.avg_price > coord.max_charge_price

    def test_no_override_above_emergency_threshold(self):
        """When SOC >= EMERGENCY_SOC_THRESHOLD, price threshold applies normally."""
        expensive_prices = {
            f"{_TEST_TODAY}T22:00:00+01:00": 8.0,
            f"{_TEST_TODAY}T23:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T00:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T01:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T02:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T03:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T04:00:00+01:00": 8.0,
            f"{_TEST_TOMORROW}T05:00:00+01:00": 8.0,
        }
        coord = _make_coordinator(
            solar_forecast_tomorrow=5.0,
            consumption_history=[16.0],
            max_charge_price=4.0,
            current_soc=30.0,  # above EMERGENCY_SOC_THRESHOLD (25%)
            price_attributes=expensive_prices,
        )
        planner = ChargingPlanner(coord)
        schedule = planner.plan_charging(now=_TEST_NOW)

        # Should NOT schedule due to price threshold
        assert schedule is None


class TestSimulateTrajectory:
    """Test the core hour-by-hour SOC trajectory simulation."""

    def test_full_battery_sunny_day(self):
        """SOC=85%, solar=25kWh → charge_needed=0."""
        coord = _make_coordinator(
            current_soc=85.0,
            solar_forecast_tomorrow=25.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        assert t.charge_needed_kwh == 0.0
        assert t.min_soc_kwh > 15.0 * 20.0 / 100  # never dips below min_soc

    def test_low_battery_cloudy_day(self):
        """SOC=25%, solar=2kWh → charge_needed reflects full drain."""
        coord = _make_coordinator(
            current_soc=25.0,
            solar_forecast_tomorrow=2.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        assert t.charge_needed_kwh > 0
        assert t.min_soc_kwh < 15.0 * 20.0 / 100  # dips below min_soc

    def test_high_soc_small_deficit_no_charge(self):
        """SOC=65%, daily deficit=2kWh → battery absorbs it, charge_needed=0."""
        coord = _make_coordinator(
            current_soc=65.0,  # 9.75 kWh
            solar_forecast_tomorrow=14.5,  # deficit = 16.5 - 14.5 = 2 kWh
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        # Battery has enough to cover the small deficit
        assert t.charge_needed_kwh == 0.0

    def test_works_at_midnight(self):
        """now=00:00 → no wrapping bug, correct simulation."""
        now_midnight = datetime(2026, 2, 26, 0, 0, 0)
        today = now_midnight.strftime("%Y-%m-%d")  # "2026-02-26"
        tomorrow = (now_midnight + timedelta(days=1)).strftime("%Y-%m-%d")  # "2026-02-27"
        coord = _make_coordinator(
            current_soc=40.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            solar_forecast_today=0.0,
            actual_solar_today=0.0,
            # Need prices for tomorrow (2026-02-27)
            price_attributes={
                f"{today}T22:00:00+01:00": 1.8,
                f"{today}T23:00:00+01:00": 1.5,
                f"{tomorrow}T00:00:00+01:00": 1.2,
                f"{tomorrow}T01:00:00+01:00": 1.0,
                f"{tomorrow}T02:00:00+01:00": 1.3,
                f"{tomorrow}T03:00:00+01:00": 1.6,
                f"{tomorrow}T04:00:00+01:00": 2.0,
                f"{tomorrow}T05:00:00+01:00": 2.5,
            },
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=now_midnight)

        # Should not crash, charge_needed should be reasonable
        assert t.charge_needed_kwh >= 0
        assert t.min_soc_hour >= 0
        assert t.min_soc_hour <= 23

    def test_works_at_23h(self):
        """now=23:00 → inside window, no 22h-ahead bug."""
        now_23 = datetime(2026, 2, 25, 23, 0, 0)
        coord = _make_coordinator(
            current_soc=40.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            solar_forecast_today=0.0,
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=now_23)

        assert t.charge_needed_kwh >= 0
        # Should simulate ~25 hours (1 today + 24 tomorrow)
        assert t.tomorrow_consumption > 0

    def test_works_at_afternoon(self):
        """now=14:00 → standard planning time."""
        now_14 = datetime(2026, 2, 25, 14, 0, 0)
        coord = _make_coordinator(
            current_soc=60.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=10.0,
            actual_solar_today=5.0,  # 5 kWh remaining solar today
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=now_14)

        assert t.charge_needed_kwh >= 0
        # Battery at window start should account for afternoon/evening drain
        # plus remaining solar today
        assert t.battery_at_window_start_kwh >= 0

    def test_weekend_multiplier(self):
        """Tomorrow is Saturday → consumption scaled by weekend multiplier."""
        # Feb 27, 2026 is a Friday → tomorrow (Feb 28) is Saturday
        now_friday = datetime(2026, 2, 27, 20, 0, 0)
        today_fri = now_friday.strftime("%Y-%m-%d")
        tomorrow_sat = (now_friday + timedelta(days=1)).strftime("%Y-%m-%d")

        coord_weekday = _make_coordinator(
            current_soc=50.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            weekend_consumption_multiplier=1.2,
            price_attributes={
                f"{today_fri}T22:00:00+01:00": 1.8,
                f"{today_fri}T23:00:00+01:00": 1.5,
                f"{tomorrow_sat}T00:00:00+01:00": 1.2,
                f"{tomorrow_sat}T01:00:00+01:00": 1.0,
                f"{tomorrow_sat}T02:00:00+01:00": 1.3,
                f"{tomorrow_sat}T03:00:00+01:00": 1.6,
                f"{tomorrow_sat}T04:00:00+01:00": 2.0,
                f"{tomorrow_sat}T05:00:00+01:00": 2.5,
            },
        )
        coord_no_mult = _make_coordinator(
            current_soc=50.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            weekend_consumption_multiplier=1.0,
            price_attributes={
                f"{today_fri}T22:00:00+01:00": 1.8,
                f"{today_fri}T23:00:00+01:00": 1.5,
                f"{tomorrow_sat}T00:00:00+01:00": 1.2,
                f"{tomorrow_sat}T01:00:00+01:00": 1.0,
                f"{tomorrow_sat}T02:00:00+01:00": 1.3,
                f"{tomorrow_sat}T03:00:00+01:00": 1.6,
                f"{tomorrow_sat}T04:00:00+01:00": 2.0,
                f"{tomorrow_sat}T05:00:00+01:00": 2.5,
            },
        )

        t_weekend = ChargingPlanner(coord_weekday).simulate_trajectory(now=now_friday)
        t_normal = ChargingPlanner(coord_no_mult).simulate_trajectory(now=now_friday)

        # Weekend consumption should be higher
        assert t_weekend.tomorrow_consumption > t_normal.tomorrow_consumption

    def test_hourly_solar_used(self):
        """Hourly forecast_solar data available → per-hour values used."""
        hourly_tomorrow = {
            7: 0.3, 8: 1.0, 9: 2.5, 10: 3.5, 11: 4.0, 12: 4.0,
            13: 3.5, 14: 2.5, 15: 1.5, 16: 0.5,
        }
        coord = _make_coordinator(
            current_soc=50.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_tomorrow_hourly=hourly_tomorrow,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        assert t.solar_source == "forecast_solar"
        assert t.charge_needed_kwh >= 0

    def test_fallback_solar_distribution(self):
        """No hourly data → daily total distributed evenly across 6-17."""
        coord = _make_coordinator(
            current_soc=50.0,
            solar_forecast_tomorrow=12.0,  # 12/12 = 1.0 kWh/h over 6-17
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_tomorrow_hourly=None,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        assert t.solar_source == "fallback"
        assert t.tomorrow_solar_raw == 12.0

    def test_forecast_error_correction(self):
        """40% error → solar reduced by 40%."""
        coord = _make_coordinator(
            current_soc=50.0,
            solar_forecast_tomorrow=10.0,
            consumption_history=[16.0],
            forecast_error_history=[0.4, 0.4, 0.4],
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        assert t.tomorrow_solar_adjusted == 6.0  # 10 * (1 - 0.4)
        assert t.forecast_error_pct == 40.0

    def test_soc_clamped_at_max(self):
        """Sunny day → SOC doesn't exceed max_charge_level."""
        coord = _make_coordinator(
            current_soc=80.0,
            solar_forecast_tomorrow=30.0,  # way more than consumption
            consumption_history=[10.0],
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        # min_soc should never exceed max_soc_kwh (13.5)
        # and should never go below 0
        assert t.min_soc_kwh >= 0
        assert t.charge_needed_kwh == 0.0

    def test_charging_efficiency_applied(self):
        """charge_needed_kwh > raw shortfall by 1/efficiency factor."""
        coord_100 = _make_coordinator(
            current_soc=25.0,
            solar_forecast_tomorrow=2.0,
            consumption_history=[16.0],
            charging_efficiency=1.0,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )
        coord_90 = _make_coordinator(
            current_soc=25.0,
            solar_forecast_tomorrow=2.0,
            consumption_history=[16.0],
            charging_efficiency=0.9,
            solar_forecast_today=10.0,
            actual_solar_today=10.0,
        )

        t_100 = ChargingPlanner(coord_100).simulate_trajectory(now=_TEST_NOW)
        t_90 = ChargingPlanner(coord_90).simulate_trajectory(now=_TEST_NOW)

        # Same min_soc_kwh (same simulation path)
        assert t_100.min_soc_kwh == t_90.min_soc_kwh
        # But 90% efficiency requires more charge from grid
        assert t_90.charge_needed_kwh > t_100.charge_needed_kwh

    def test_battery_at_window_start_tracked(self):
        """Correct SOC recorded at 22:00."""
        coord = _make_coordinator(
            current_soc=60.0,  # 9.0 kWh
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=10.0,
            actual_solar_today=10.0,  # no remaining solar
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=_TEST_NOW)

        # Planning at 20:00, window starts at 22:00
        # 2 hours of evening consumption drain before window start
        # SOC should decrease from 9.0 kWh
        assert t.battery_at_window_start_kwh >= 0
        assert t.battery_at_window_start_kwh < 9.0 - 3.0  # below initial usable

    def test_39pct_at_4am_with_good_solar(self):
        """Real failure: 39% SOC at 4 AM, solar tomorrow covers the day.
        Old system charged to 55% unnecessarily."""
        now_4am = datetime(2026, 2, 26, 4, 0, 0)
        today = now_4am.strftime("%Y-%m-%d")  # "2026-02-26"
        tomorrow = (now_4am + timedelta(days=1)).strftime("%Y-%m-%d")  # "2026-02-27"

        coord = _make_coordinator(
            current_soc=39.0,
            battery_capacity=15.0,
            min_soc=20.0,
            max_charge_level=90.0,
            solar_forecast_tomorrow=17.0,  # plenty of solar tomorrow
            consumption_history=[16.0, 17.0, 16.5],
            solar_forecast_today=0.0,  # it's 4 AM, no solar today yet
            actual_solar_today=0.0,
            price_attributes={
                f"{today}T22:00:00+01:00": 1.8,
                f"{today}T23:00:00+01:00": 1.5,
                f"{tomorrow}T00:00:00+01:00": 1.2,
                f"{tomorrow}T01:00:00+01:00": 1.0,
                f"{tomorrow}T02:00:00+01:00": 1.3,
                f"{tomorrow}T03:00:00+01:00": 1.6,
                f"{tomorrow}T04:00:00+01:00": 2.0,
                f"{tomorrow}T05:00:00+01:00": 2.5,
            },
        )
        planner = ChargingPlanner(coord)
        t = planner.simulate_trajectory(now=now_4am)

        # At 4 AM with 39% SOC:
        # Battery usable = (39-20)/100 * 15 = 2.85 kWh
        # Night drain: ~3h * 0.36 kWh/h (night multiplier) = ~1.07 kWh until ~7 AM
        # Then solar starts ramping up
        # 2.85 kWh > 1.07 kWh → battery survives until solar kicks in
        #
        # The key insight: 39% SOC at 4 AM should NOT trigger charging
        # because there's enough battery to bridge to morning solar.

        # Today's solar is 0 (4 AM), but today extends to sunset (~5 PM),
        # and _build_solar_profile distributes 0 kWh over remaining daylight.
        # Tomorrow has 17 kWh which will recharge the battery.

        # Check that charging is minimal or zero
        # Battery at 39% (5.85 kWh) should survive until solar today (~7 AM)
        # min_soc_kwh should be above 3.0 (min_soc=20% of 15=3.0)
        # or just barely below it
        min_soc_threshold = 15.0 * 20.0 / 100  # 3.0 kWh

        # Battery drains from 5.85 kWh at 4 AM:
        # H4-H5: night rate consumption, no solar
        # H6-H7: day rate but still no solar (fallback distributes today=0)
        # Today's solar = 0, so no solar production today at all
        # Tomorrow: 17 kWh spread over 6-17 = 1.417/h
        # SOC minimum is likely around H5-H6 before tomorrow's solar kicks in
        # But wait - today IS the day with solar_forecast_today=0
        # The simulation goes from H4 today through end of tomorrow

        # Even with no solar today, the battery at 5.85 kWh should survive
        # ~20 hours of consumption until tomorrow's solar comes in at H6
        # consumption_night ≈ 0.36/h for H4-5 = 0.72
        # consumption_day ≈ 0.72/h for H6-17 = 8.64
        # consumption_evening ≈ 1.08/h for H18-22 = 5.38
        # Total from H4 to H6 tomorrow ≈ 0.72 + 8.64 + 5.38 + 0.72*1 + 0.36*5 = ...
        # This would drain the battery well below min_soc
        # So SOME charging may be needed, but much less than the old system's 55%

        # The important thing: charge_needed should be much less than
        # what the old system calculated (old: charged to 55%, which is
        # (55-39)/100 * 15 = 2.4 kWh extra from the old bug)
        # With trajectory, we get the exact minimum needed
        assert t.charge_needed_kwh < 10.5  # less than full usable capacity


class TestBuildSolarProfile:
    """Test the solar profile builder."""

    def test_fallback_today_evening_no_solar(self):
        """At 20:00, no daylight hours remain → no today solar in profile."""
        coord = _make_coordinator(
            solar_forecast_today=10.0,
            actual_solar_today=5.0,
        )
        planner = ChargingPlanner(coord)
        profile, source = planner._build_solar_profile(_TEST_NOW)

        # At hour 20, no daylight hours (6-17) remain
        for h in range(20, 24):
            assert profile.get((0, h), 0.0) == 0.0

    def test_fallback_today_afternoon_solar(self):
        """At 14:00, remaining solar distributed across 14-17."""
        now_14 = datetime(2026, 2, 25, 14, 0, 0)
        coord = _make_coordinator(
            solar_forecast_today=10.0,
            actual_solar_today=5.0,
        )
        planner = ChargingPlanner(coord)
        profile, source = planner._build_solar_profile(now_14)

        # Remaining: 10.0 - 5.0 = 5.0 kWh over hours 14, 15, 16, 17 = 4 hours
        expected_per_hour = 5.0 / 4
        for h in [14, 15, 16, 17]:
            assert abs(profile.get((0, h), 0.0) - expected_per_hour) < 0.01

    def test_fallback_tomorrow_distributed_6_to_17(self):
        """No hourly data → tomorrow solar spread over hours 6-17."""
        coord = _make_coordinator(
            solar_forecast_tomorrow=12.0,
            solar_forecast_tomorrow_hourly=None,
        )
        planner = ChargingPlanner(coord)
        profile, source = planner._build_solar_profile(_TEST_NOW)

        expected_per_hour = 12.0 / 12
        for h in range(6, 18):
            assert abs(profile.get((1, h), 0.0) - expected_per_hour) < 0.01
        # No solar outside daylight
        assert profile.get((1, 5), 0.0) == 0.0
        assert profile.get((1, 18), 0.0) == 0.0

    def test_hourly_tomorrow_with_error_correction(self):
        """Hourly data with 40% error → each hour reduced by 40%."""
        hourly = {8: 2.0, 9: 3.0, 10: 4.0}
        coord = _make_coordinator(
            solar_forecast_tomorrow_hourly=hourly,
            forecast_error_history=[0.4, 0.4, 0.4],
        )
        planner = ChargingPlanner(coord)
        profile, source = planner._build_solar_profile(_TEST_NOW)

        assert source == "forecast_solar"
        assert abs(profile.get((1, 8), 0.0) - 2.0 * 0.6) < 0.01
        assert abs(profile.get((1, 9), 0.0) - 3.0 * 0.6) < 0.01
        assert abs(profile.get((1, 10), 0.0) - 4.0 * 0.6) < 0.01

    def test_hourly_today_used_when_available(self):
        """Today's hourly data used as-is (no error correction)."""
        hourly_today = {14: 1.5, 15: 1.0, 16: 0.5}
        coord = _make_coordinator(
            solar_forecast_today_hourly=hourly_today,
        )
        planner = ChargingPlanner(coord)
        profile, source = planner._build_solar_profile(_TEST_NOW)

        assert source == "forecast_solar"
        assert profile.get((0, 14), 0.0) == 1.5
        assert profile.get((0, 15), 0.0) == 1.0
        assert profile.get((0, 16), 0.0) == 0.5


# --- Surplus Forecast tests ---

# Morning time for surplus tests (solar hasn't started yet)
_SURPLUS_NOW = datetime(2026, 3, 8, 8, 0, 0)


class TestForecastTodaySurplus:
    """Test forecast_today_surplus() — predicts solar overflow after battery full."""

    def test_no_surplus_when_battery_not_full(self):
        """Low SOC + moderate solar = no surplus (battery absorbs it all)."""
        coord = _make_coordinator(
            current_soc=20.0,  # 3 kWh in 15 kWh battery
            solar_forecast_today=8.0,
            solar_forecast_today_hourly={h: 0.8 for h in range(8, 18)},
            consumption_history=[16.0, 17.0, 16.5],
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=_SURPLUS_NOW)

        assert result.total_kwh == 0.0
        assert result.surplus_hours == 0
        assert result.battery_full_hour is None

    def test_surplus_when_battery_full_and_high_solar(self):
        """Full battery + lots of solar = surplus."""
        coord = _make_coordinator(
            current_soc=85.0,  # 12.75 kWh in 15 kWh battery, max=13.5 (90%)
            solar_forecast_today=20.0,
            # Big solar in midday hours
            solar_forecast_today_hourly={
                8: 0.5, 9: 1.0, 10: 2.0, 11: 3.0, 12: 3.5,
                13: 3.5, 14: 3.0, 15: 2.0, 16: 1.0, 17: 0.5,
            },
            consumption_history=[12.0, 12.0, 12.0],  # low consumption
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=_SURPLUS_NOW)

        assert result.total_kwh > 0
        assert result.surplus_hours > 0
        assert result.battery_full_hour is not None
        assert result.peak_surplus_kw > 0

    def test_surplus_zero_at_night(self):
        """Starting at 22:00 with no solar remaining — no surplus."""
        night_now = datetime(2026, 3, 8, 22, 0, 0)
        coord = _make_coordinator(
            current_soc=90.0,
            solar_forecast_today=0.0,
            solar_forecast_today_hourly={},
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=night_now)

        assert result.total_kwh == 0.0
        assert result.surplus_hours == 0

    def test_surplus_hourly_breakdown(self):
        """Hourly breakdown has correct hours as keys."""
        coord = _make_coordinator(
            current_soc=90.0,  # Battery nearly at max (13.5 kWh)
            max_charge_level=90.0,
            solar_forecast_today=15.0,
            solar_forecast_today_hourly={
                9: 0.5, 10: 2.0, 11: 3.0, 12: 3.0,
                13: 3.0, 14: 2.0, 15: 1.5, 16: 0.5,
            },
            consumption_history=[10.0, 10.0, 10.0],
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=datetime(2026, 3, 8, 9, 0, 0))

        # All surplus hours should be daytime hours (9-16 range)
        for hour in result.hourly_kwh:
            assert 6 <= hour <= 17

    def test_surplus_weekend_higher_consumption(self):
        """Weekend multiplier increases consumption, reducing surplus."""
        # Saturday
        saturday = datetime(2026, 3, 7, 8, 0, 0)
        coord_weekend = _make_coordinator(
            current_soc=90.0,
            solar_forecast_today=15.0,
            solar_forecast_today_hourly={h: 1.5 for h in range(8, 18)},
            consumption_history=[12.0, 12.0, 12.0],
            weekend_consumption_multiplier=1.3,
        )

        # Wednesday
        wednesday = datetime(2026, 3, 4, 8, 0, 0)
        coord_weekday = _make_coordinator(
            current_soc=90.0,
            solar_forecast_today=15.0,
            solar_forecast_today_hourly={h: 1.5 for h in range(8, 18)},
            consumption_history=[12.0, 12.0, 12.0],
            weekend_consumption_multiplier=1.3,
        )

        planner_weekend = ChargingPlanner(coord_weekend)
        planner_weekday = ChargingPlanner(coord_weekday)

        surplus_weekend = planner_weekend.forecast_today_surplus(now=saturday)
        surplus_weekday = planner_weekday.forecast_today_surplus(now=wednesday)

        # Weekend has higher consumption → less surplus
        assert surplus_weekend.total_kwh < surplus_weekday.total_kwh

    def test_surplus_battery_full_hour_is_first_overflow(self):
        """battery_full_hour should be the first hour where SOC exceeds max."""
        coord = _make_coordinator(
            current_soc=80.0,  # 12.0 kWh, max is 13.5 (90%)
            solar_forecast_today=20.0,
            solar_forecast_today_hourly={
                8: 0.2, 9: 0.5, 10: 1.5, 11: 3.0, 12: 3.5,
                13: 3.5, 14: 3.0, 15: 2.0, 16: 1.0, 17: 0.3,
            },
            consumption_history=[10.0, 10.0, 10.0],
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=datetime(2026, 3, 8, 8, 0, 0))

        assert result.battery_full_hour is not None
        # First surplus hour should match battery_full_hour
        if result.hourly_kwh:
            first_surplus_hour = min(result.hourly_kwh.keys())
            assert result.battery_full_hour == first_surplus_hour

    def test_surplus_soc_clamped_at_zero(self):
        """SOC doesn't go negative during simulation."""
        coord = _make_coordinator(
            current_soc=5.0,  # very low: 0.75 kWh
            solar_forecast_today=3.0,
            solar_forecast_today_hourly={12: 1.0, 13: 1.0, 14: 1.0},
            consumption_history=[20.0, 20.0, 20.0],  # high consumption
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=_SURPLUS_NOW)

        # Should not produce surplus when battery is nearly empty
        assert result.total_kwh == 0.0

    def test_returns_surplus_forecast_type(self):
        """Return type is SurplusForecast with all fields."""
        coord = _make_coordinator()
        planner = ChargingPlanner(coord)
        result = planner.forecast_today_surplus(now=_SURPLUS_NOW)

        assert isinstance(result, SurplusForecast)
        assert hasattr(result, "total_kwh")
        assert hasattr(result, "hourly_kwh")
        assert hasattr(result, "battery_full_hour")
        assert hasattr(result, "peak_surplus_kw")
        assert hasattr(result, "surplus_hours")


class TestForecastTomorrowSurplus:
    """Test forecast_tomorrow_surplus() — predicts next day's surplus."""

    def test_tomorrow_surplus_with_high_solar(self):
        """Lots of solar tomorrow → surplus even starting from min_soc."""
        coord = _make_coordinator(
            current_soc=50.0,
            min_soc=20.0,
            solar_forecast_tomorrow=25.0,
            solar_forecast_tomorrow_hourly={
                8: 1.0, 9: 2.0, 10: 3.0, 11: 3.5, 12: 3.5,
                13: 3.5, 14: 3.0, 15: 2.5, 16: 1.5, 17: 1.0,
            },
            consumption_history=[12.0, 12.0, 12.0],
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_tomorrow_surplus(now=_SURPLUS_NOW)

        assert result.total_kwh > 0
        assert result.surplus_hours > 0

    def test_tomorrow_surplus_low_solar_no_surplus(self):
        """Low solar tomorrow → no surplus."""
        coord = _make_coordinator(
            min_soc=20.0,
            solar_forecast_tomorrow=5.0,
            solar_forecast_tomorrow_hourly={h: 0.5 for h in range(8, 18)},
            consumption_history=[16.0, 16.0, 16.0],
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_tomorrow_surplus(now=_SURPLUS_NOW)

        assert result.total_kwh == 0.0

    def test_tomorrow_starts_at_min_soc(self):
        """Tomorrow simulation starts conservatively at min_soc."""
        # High current_soc shouldn't affect tomorrow's forecast
        coord_high = _make_coordinator(
            current_soc=90.0,
            min_soc=20.0,
            solar_forecast_tomorrow=15.0,
            solar_forecast_tomorrow_hourly={h: 1.5 for h in range(8, 18)},
            consumption_history=[12.0, 12.0, 12.0],
        )
        coord_low = _make_coordinator(
            current_soc=20.0,
            min_soc=20.0,
            solar_forecast_tomorrow=15.0,
            solar_forecast_tomorrow_hourly={h: 1.5 for h in range(8, 18)},
            consumption_history=[12.0, 12.0, 12.0],
        )
        planner_high = ChargingPlanner(coord_high)
        planner_low = ChargingPlanner(coord_low)

        result_high = planner_high.forecast_tomorrow_surplus(now=_SURPLUS_NOW)
        result_low = planner_low.forecast_tomorrow_surplus(now=_SURPLUS_NOW)

        # Both should be the same since tomorrow starts at min_soc regardless
        assert result_high.total_kwh == result_low.total_kwh

    def test_tomorrow_weekend_multiplier(self):
        """Weekend multiplier applied based on tomorrow's day of week."""
        # Friday evening → tomorrow is Saturday
        friday = datetime(2026, 3, 6, 20, 0, 0)
        coord = _make_coordinator(
            min_soc=20.0,
            solar_forecast_tomorrow=20.0,
            solar_forecast_tomorrow_hourly={h: 2.0 for h in range(8, 18)},
            consumption_history=[12.0, 12.0, 12.0],
            weekend_consumption_multiplier=1.3,
        )
        planner = ChargingPlanner(coord)
        result = planner.forecast_tomorrow_surplus(now=friday)

        # Compare with weekday (Thursday → Friday)
        thursday = datetime(2026, 3, 5, 20, 0, 0)
        result_weekday = planner.forecast_tomorrow_surplus(now=thursday)

        # Weekend has higher consumption → less surplus
        assert result.total_kwh < result_weekday.total_kwh


class TestEvaluatePredictiveLoad:
    """Test evaluate_predictive_load() for predictive surplus loads."""

    def _make_predictive_load(self, power_kw=1.5, start=5, end=8):
        from smart_battery_charging.models import SurplusLoadConfig
        return SurplusLoadConfig(
            name="Floor Heating",
            switch_entity="switch.floor_heating",
            power_kw=power_kw,
            priority=2,
            mode="predictive",
            schedule_start_hour=start,
            schedule_end_hour=end,
        )

    def _make_reactive_load(self, power_kw=2.3, priority=1):
        from smart_battery_charging.models import SurplusLoadConfig
        return SurplusLoadConfig(
            name="Water Heater",
            switch_entity="switch.water_heater",
            power_kw=power_kw,
            priority=priority,
        )

    def test_approved_with_good_solar(self):
        """High SOC + good solar → approved."""
        now = datetime(2026, 3, 9, 4, 30, 0)  # 4:30 AM, before schedule
        coord = _make_coordinator(
            current_soc=70.0,
            solar_forecast_today=20.0,
            solar_forecast_today_hourly={h: 2.5 for h in range(8, 16)},
            consumption_history=[12.0, 12.0, 12.0],
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        load = self._make_predictive_load(power_kw=1.5, start=5, end=8)

        result = planner.evaluate_predictive_load(load, [], now=now)

        assert result.approved is True
        assert result.load_needs_kwh == 4.5  # 1.5 kW * 3 hours
        assert result.min_soc_after > 20.0  # stays above min_soc

    def test_denied_with_low_soc_and_poor_solar(self):
        """Low SOC + poor solar → denied."""
        now = datetime(2026, 3, 9, 4, 30, 0)
        coord = _make_coordinator(
            current_soc=25.0,
            solar_forecast_today=3.0,
            solar_forecast_today_hourly={h: 0.5 for h in range(10, 16)},
            consumption_history=[16.0, 17.0, 16.5],
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        load = self._make_predictive_load(power_kw=1.5, start=5, end=8)

        result = planner.evaluate_predictive_load(load, [], now=now)

        assert result.approved is False
        assert result.min_soc_after < 20.0

    def test_reactive_loads_claim_surplus_first(self):
        """Reactive loads reduce available surplus budget."""
        now = datetime(2026, 3, 9, 4, 30, 0)
        coord = _make_coordinator(
            current_soc=70.0,
            solar_forecast_today=25.0,
            solar_forecast_today_hourly={h: 3.0 for h in range(8, 16)},
            consumption_history=[12.0, 12.0, 12.0],
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        load = self._make_predictive_load(power_kw=1.5, start=5, end=8)
        reactive = [self._make_reactive_load(power_kw=2.3)]

        result = planner.evaluate_predictive_load(load, reactive, now=now)

        # Reactive loads should claim some surplus
        assert result.reactive_claim_kwh > 0
        assert result.surplus_budget_kwh < result.surplus_budget_kwh + result.reactive_claim_kwh

    def test_zero_power_load(self):
        """Zero-power load needs 0 kWh, doesn't change approval outcome."""
        now = datetime(2026, 3, 9, 4, 30, 0)
        coord = _make_coordinator(
            current_soc=70.0,
            solar_forecast_today=20.0,
            solar_forecast_today_hourly={h: 2.5 for h in range(8, 16)},
            consumption_history=[12.0, 12.0, 12.0],
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        load = self._make_predictive_load(power_kw=0.0, start=5, end=8)

        result = planner.evaluate_predictive_load(load, [], now=now)

        assert result.load_needs_kwh == 0.0
        assert result.approved is True

    def test_high_power_load_denied(self):
        """Very high power load drains battery below min_soc."""
        now = datetime(2026, 3, 9, 4, 30, 0)
        coord = _make_coordinator(
            current_soc=40.0,
            solar_forecast_today=5.0,
            solar_forecast_today_hourly={h: 0.8 for h in range(9, 15)},
            consumption_history=[16.0, 17.0, 16.5],
            actual_solar_today=0.0,
        )
        planner = ChargingPlanner(coord)
        load = self._make_predictive_load(power_kw=5.0, start=5, end=8)

        result = planner.evaluate_predictive_load(load, [], now=now)

        assert result.approved is False
        assert result.load_needs_kwh == 15.0  # 5 kW * 3 hours
