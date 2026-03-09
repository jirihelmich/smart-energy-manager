"""Charging planner — pure orchestration, no HA service calls.

Reads all data through the coordinator and its sub-components.
Decides whether charging is needed, how much, and when.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .const import (
    EMERGENCY_SOC_THRESHOLD,
    PV_FALLBACK_BUFFER_HOURS,
)
from .models import (
    ChargingSchedule,
    EnergyDeficit,
    OvernightNeed,
    PredictiveEvaluation,
    SurplusForecast,
    SurplusLoadConfig,
    TrajectoryResult,
)

if TYPE_CHECKING:
    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


def _default_now() -> datetime:
    """Fallback for when no `now` is passed (e.g. in tests)."""
    return datetime.now()


class ChargingPlanner:
    """Plans charging sessions based on energy deficit and price analysis."""

    def __init__(self, coordinator: SmartBatteryCoordinator) -> None:
        self._coordinator = coordinator
        self.last_overnight_need: OvernightNeed | None = None

    # --- Hourly consumption model ---

    def _hourly_consumption(self, hour: int, daily: float) -> float:
        """Return kWh consumption for a given hour using the 3-period model.

        Periods: Day (06-18) multiplier=1.0, Evening (18-23) multiplier=E, Night (23-06) multiplier=N.
        base_rate = daily / (12*1.0 + 5*E + 7*N)
        """
        c = self._coordinator
        e = c.evening_consumption_multiplier
        n = c.night_consumption_multiplier
        base_rate = daily / (12 * 1.0 + 5 * e + 7 * n)

        if 6 <= hour < 18:
            return base_rate * 1.0
        elif 18 <= hour < 23:
            return base_rate * e
        else:  # 23-06
            return base_rate * n

    # --- Solar profile builder ---

    def _build_solar_profile(
        self, now: datetime,
    ) -> tuple[dict[tuple[int, int], float], str]:
        """Build hourly solar production profile for today and tomorrow.

        Returns (profile, source):
          profile: {(day_offset, clock_hour): kwh} where day_offset 0=today, 1=tomorrow
          source: "forecast_solar" or "fallback"
        """
        c = self._coordinator
        error_history = c.store.forecast_error_history
        hourly_today = c.solar_forecast_today_hourly
        hourly_tomorrow = c.solar_forecast_tomorrow_hourly

        profile: dict[tuple[int, int], float] = {}
        source = "fallback"

        # --- Today's remaining solar ---
        if hourly_today:
            source = "forecast_solar"
            for h in range(24):
                profile[(0, h)] = hourly_today.get(h, 0.0)
        else:
            adjusted_today = c.forecast_corrector.adjust_forecast(
                c.solar_forecast_today, error_history
            )
            remaining = max(0.0, adjusted_today - c.actual_solar_today)
            daylight_remaining = [h for h in range(6, 18) if h >= now.hour]
            if daylight_remaining and remaining > 0:
                per_hour = remaining / len(daylight_remaining)
                for h in daylight_remaining:
                    profile[(0, h)] = per_hour

        # --- Tomorrow's solar (with error correction) ---
        if hourly_tomorrow:
            source = "forecast_solar"
            avg_error = c.forecast_corrector.average_error(error_history)
            for h in range(24):
                raw = hourly_tomorrow.get(h, 0.0)
                profile[(1, h)] = raw * (1 - avg_error)
        else:
            solar_adjusted = c.forecast_corrector.adjust_forecast(
                c.solar_forecast_tomorrow, error_history
            )
            if solar_adjusted > 0:
                for h in range(6, 18):
                    profile[(1, h)] = solar_adjusted / 12

        return profile, source

    # --- Core trajectory simulation ---

    def simulate_trajectory(
        self, *, now: datetime | None = None,
    ) -> TrajectoryResult:
        """Simulate battery SOC hour-by-hour from now through end of tomorrow.

        Single forward pass that naturally handles:
        - Current SOC being high enough -> no charging needed
        - Running at any hour (no wraparound bugs)
        - Solar ramping up gradually in the morning
        - Daytime solar surplus recharging the battery

        Returns a TrajectoryResult with the charge needed to keep SOC above
        min_soc at all times, plus backward-compatible data.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        # --- Base parameters ---
        daily_consumption = c.consumption_tracker.average(c.store.consumption_history)
        tomorrow = now + timedelta(days=1)
        if tomorrow.weekday() >= 5:
            daily_consumption *= c.weekend_consumption_multiplier

        capacity = c.battery_capacity
        min_soc_kwh = capacity * c.min_soc / 100
        max_soc_kwh = capacity * c.max_charge_level / 100
        usable_capacity = max_soc_kwh - min_soc_kwh

        soc_kwh = capacity * c.current_soc / 100

        # --- Solar profile ---
        solar_profile, solar_source = self._build_solar_profile(now)

        window_start = c.price_analyzer._window_start  # e.g. 22
        window_end = c.price_analyzer._window_end  # e.g. 6

        # --- Tracking ---
        min_soc_reached = soc_kwh
        min_soc_hour = now.hour
        battery_at_window_start: float | None = None
        dark_hours = 0.0
        overnight_consumption = 0.0
        solar_start_hour: float | None = None
        in_overnight = False
        tomorrow_consumption_total = 0.0

        # --- Simulate: from now.hour through end of tomorrow ---
        start_hour = now.hour
        total_steps = (24 - start_hour) + 24

        for step in range(total_steps):
            absolute_hour = start_hour + step
            if absolute_hour < 24:
                day_offset = 0
                clock_hour = absolute_hour
            else:
                day_offset = 1
                clock_hour = absolute_hour - 24

            hourly_cons = self._hourly_consumption(clock_hour, daily_consumption)
            solar_h = solar_profile.get((day_offset, clock_hour), 0.0)

            soc_kwh += solar_h - hourly_cons
            soc_kwh = max(0.0, min(soc_kwh, max_soc_kwh))

            # Track minimum SOC
            if soc_kwh < min_soc_reached:
                min_soc_reached = soc_kwh
                min_soc_hour = clock_hour

            # Track battery at window start (first occurrence after now)
            if clock_hour == window_start and battery_at_window_start is None:
                battery_at_window_start = soc_kwh
                in_overnight = True

            # Track overnight period (window_start to solar coverage)
            if in_overnight and solar_start_hour is None:
                net_drain = max(0.0, hourly_cons - solar_h)
                overnight_consumption += net_drain
                dark_hours += 1.0
                past_window = (
                    clock_hour >= window_end and clock_hour < window_start
                )
                if solar_h >= hourly_cons and past_window:
                    solar_start_hour = float(clock_hour)

            # Track tomorrow's total consumption
            if day_offset == 1:
                tomorrow_consumption_total += hourly_cons

        # --- Defaults for untracked values ---
        if battery_at_window_start is None:
            # Now is already past window_start (e.g., running at 04:00)
            battery_at_window_start = capacity * c.current_soc / 100

        if solar_start_hour is None:
            solar_start_hour = float(window_end) + PV_FALLBACK_BUFFER_HOURS

        # --- Charge needed ---
        shortfall = min_soc_kwh - min_soc_reached
        if shortfall > 0:
            charge_needed = shortfall / c.charging_efficiency
            charge_needed = min(charge_needed, usable_capacity)
        else:
            charge_needed = 0.0

        # --- Backward-compat daily totals ---
        solar_raw_tomorrow = c.solar_forecast_tomorrow
        error_history = c.store.forecast_error_history
        solar_adjusted_tomorrow = c.forecast_corrector.adjust_forecast(
            solar_raw_tomorrow, error_history
        )
        forecast_error_pct = c.forecast_corrector.average_error_pct(error_history)
        daily_deficit = max(0.0, tomorrow_consumption_total - solar_adjusted_tomorrow)
        daily_charge = max(0.0, min(daily_deficit, usable_capacity))

        bws_usable = max(0.0, battery_at_window_start - min_soc_kwh)

        return TrajectoryResult(
            charge_needed_kwh=round(charge_needed, 2),
            min_soc_kwh=round(min_soc_reached, 2),
            min_soc_hour=min_soc_hour,
            daily_deficit_kwh=round(daily_deficit, 2),
            daily_charge_kwh=round(daily_charge, 2),
            battery_at_window_start_kwh=round(bws_usable, 2),
            dark_hours=round(dark_hours, 1),
            overnight_consumption_kwh=round(overnight_consumption, 2),
            solar_start_hour=round(solar_start_hour, 2),
            solar_source=solar_source,
            tomorrow_consumption=round(tomorrow_consumption_total, 2),
            tomorrow_solar_raw=round(solar_raw_tomorrow, 2),
            tomorrow_solar_adjusted=round(solar_adjusted_tomorrow, 2),
            forecast_error_pct=forecast_error_pct,
            usable_capacity_kwh=round(usable_capacity, 2),
        )

    # --- Public API (backward-compatible wrappers) ---

    def compute_energy_deficit(self, *, now: datetime | None = None) -> EnergyDeficit:
        """Compute energy deficit — thin wrapper over simulate_trajectory.

        Returns an EnergyDeficit with trajectory-based charge_needed that
        accounts for current SOC and hour-by-hour solar/consumption.
        """
        t = self.simulate_trajectory(now=now)
        return EnergyDeficit(
            consumption=t.tomorrow_consumption,
            solar_raw=t.tomorrow_solar_raw,
            solar_adjusted=t.tomorrow_solar_adjusted,
            forecast_error_pct=t.forecast_error_pct,
            deficit=t.daily_deficit_kwh,
            charge_needed=t.charge_needed_kwh,
            usable_capacity=t.usable_capacity_kwh,
        )

    def has_tomorrow_prices(self, *, now: datetime | None = None) -> bool:
        """Check if tomorrow's prices are available in the price sensor attributes."""
        if now is None:
            now = _default_now()
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        attrs = self._coordinator.price_attributes
        for key in attrs:
            key_str = str(key)
            if len(key_str) >= 10 and key_str[:10] == tomorrow:
                return True
        return False

    def compute_overnight_need(self, *, now: datetime | None = None) -> OvernightNeed:
        """Compute overnight survival — thin wrapper over simulate_trajectory."""
        t = self.simulate_trajectory(now=now)
        return OvernightNeed(
            dark_hours=t.dark_hours,
            overnight_consumption=t.overnight_consumption_kwh,
            battery_at_window_start=t.battery_at_window_start_kwh,
            charge_needed=t.charge_needed_kwh,
            solar_start_hour=t.solar_start_hour,
            source=t.solar_source,
        )

    def compute_target_soc(
        self, deficit: EnergyDeficit, *, charge_kwh: float | None = None
    ) -> float:
        """Compute target SOC from energy deficit or explicit charge amount.

        target = min_soc + (charge_needed / capacity * 100), clamped to max_charge_level.
        """
        c = self._coordinator
        effective_charge = charge_kwh if charge_kwh is not None else deficit.charge_needed
        if effective_charge <= 0:
            return c.min_soc

        charge_pct = effective_charge / c.battery_capacity * 100
        target = c.min_soc + charge_pct
        return min(round(target, 1), c.max_charge_level)

    def plan_charging(self, *, now: datetime | None = None) -> ChargingSchedule | None:
        """Full planning pipeline using trajectory simulation.

        Returns a ChargingSchedule if charging is needed and prices are acceptable,
        or None if no charging needed / prices not available / prices too high.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        if not c.enabled:
            _LOGGER.debug("Charging disabled, skipping planning")
            return None

        if not self.has_tomorrow_prices(now=now):
            _LOGGER.debug("Tomorrow's prices not available yet")
            return None

        # Single trajectory simulation
        trajectory = self.simulate_trajectory(now=now)

        # Cache backward-compat views for sensors
        self.last_overnight_need = OvernightNeed(
            dark_hours=trajectory.dark_hours,
            overnight_consumption=trajectory.overnight_consumption_kwh,
            battery_at_window_start=trajectory.battery_at_window_start_kwh,
            charge_needed=trajectory.charge_needed_kwh,
            solar_start_hour=trajectory.solar_start_hour,
            source=trajectory.solar_source,
        )
        c._last_overnight = self.last_overnight_need

        _LOGGER.info(
            "Trajectory: min_soc=%.1f kWh @ H%02d, charge_needed=%.1f kWh, "
            "daily_deficit=%.1f, battery_at_ws=%.1f, dark_hours=%.1f (%s)",
            trajectory.min_soc_kwh, trajectory.min_soc_hour,
            trajectory.charge_needed_kwh, trajectory.daily_deficit_kwh,
            trajectory.battery_at_window_start_kwh, trajectory.dark_hours,
            trajectory.solar_source,
        )

        effective_charge = trajectory.charge_needed_kwh
        if effective_charge <= 0:
            _LOGGER.info("No charging needed — trajectory shows SOC stays above min_soc")
            return None

        # Extract night prices and find cheapest window
        today = now.strftime("%Y-%m-%d")
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")

        night_slots = c.price_analyzer.extract_night_prices(
            c.price_attributes, today, tomorrow_str
        )

        if not night_slots:
            _LOGGER.warning("No night price slots available")
            return None

        # Negative price exploitation — charge to max when free/profitable
        usable_capacity = trajectory.usable_capacity_kwh
        cheapest_price = min(slot.price for slot in night_slots)
        if cheapest_price <= 0 and effective_charge < usable_capacity:
            _LOGGER.info(
                "Negative prices detected (%.2f), charging to maximum %.1f kWh",
                cheapest_price, usable_capacity,
            )
            effective_charge = usable_capacity

        hours_needed = c.price_analyzer.calculate_hours_needed(
            effective_charge, c.max_charge_power
        )
        if hours_needed == 0:
            return None

        window = c.price_analyzer.find_cheapest_window(night_slots, hours_needed)
        if window is None:
            _LOGGER.warning(
                "Could not find contiguous %d-hour window in night prices",
                hours_needed,
            )
            return None

        # Price threshold check — skip for negative/zero avg price
        if window.avg_price > 0 and window.avg_price > c.max_charge_price:
            current_soc = c.current_soc
            if current_soc < EMERGENCY_SOC_THRESHOLD and effective_charge > 0:
                _LOGGER.warning(
                    "Battery at %.0f%% (below emergency threshold %.0f%%) — "
                    "overriding price threshold (%.2f > %.2f)",
                    current_soc, EMERGENCY_SOC_THRESHOLD,
                    window.avg_price, c.max_charge_price,
                )
            else:
                _LOGGER.info(
                    "Cheapest window avg price %.2f exceeds threshold %.2f, skipping",
                    window.avg_price,
                    c.max_charge_price,
                )
                return None

        # Build EnergyDeficit for notification
        deficit = EnergyDeficit(
            consumption=trajectory.tomorrow_consumption,
            solar_raw=trajectory.tomorrow_solar_raw,
            solar_adjusted=trajectory.tomorrow_solar_adjusted,
            forecast_error_pct=trajectory.forecast_error_pct,
            deficit=trajectory.daily_deficit_kwh,
            charge_needed=effective_charge,
            usable_capacity=trajectory.usable_capacity_kwh,
        )

        # Target SOC = projected SOC at window start + charge percentage
        # battery_at_window_start_kwh is usable kWh above min_soc
        soc_at_ws = c.min_soc + (
            trajectory.battery_at_window_start_kwh / c.battery_capacity * 100
        )
        charge_pct = effective_charge / c.battery_capacity * 100
        target_soc = min(round(soc_at_ws + charge_pct, 1), c.max_charge_level)

        schedule = ChargingSchedule(
            start_hour=window.start_hour,
            end_hour=window.end_hour,
            window_hours=window.window_hours,
            avg_price=window.avg_price,
            required_kwh=round(effective_charge, 2),
            target_soc=target_soc,
            created_at=now,
        )

        _LOGGER.info(
            "Charging scheduled: %02d:00-%02d:00, %.1f kWh, target %.0f%%, avg price %.2f",
            schedule.start_hour,
            schedule.end_hour,
            schedule.required_kwh,
            schedule.target_soc,
            schedule.avg_price,
        )

        return schedule

    def forecast_today_surplus(self, *, now: datetime | None = None) -> SurplusForecast:
        """Forecast today's solar surplus — excess after battery is full.

        Simulates SOC from now through end of today. At each hour where
        battery is at max and solar > consumption, the excess is surplus.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        daily_consumption = c.consumption_tracker.average(c.store.consumption_history)
        if now.weekday() >= 5:
            daily_consumption *= c.weekend_consumption_multiplier

        capacity = c.battery_capacity
        max_soc_kwh = capacity * c.max_charge_level / 100
        soc_kwh = capacity * c.current_soc / 100

        solar_profile, _ = self._build_solar_profile(now)

        hourly_surplus: dict[int, float] = {}
        battery_full_hour: int | None = None
        total_surplus = 0.0
        peak_surplus = 0.0

        # Simulate from now.hour through end of today
        for hour in range(now.hour, 24):
            cons = self._hourly_consumption(hour, daily_consumption)
            solar = solar_profile.get((0, hour), 0.0)

            net = solar - cons
            soc_kwh += net

            # Check if battery overflows — that's surplus
            if soc_kwh > max_soc_kwh:
                surplus = soc_kwh - max_soc_kwh
                soc_kwh = max_soc_kwh
                hourly_surplus[hour] = round(surplus, 3)
                total_surplus += surplus
                peak_surplus = max(peak_surplus, surplus)

                if battery_full_hour is None:
                    battery_full_hour = hour
            else:
                soc_kwh = max(0.0, soc_kwh)

        return SurplusForecast(
            total_kwh=round(total_surplus, 2),
            hourly_kwh=hourly_surplus,
            battery_full_hour=battery_full_hour,
            peak_surplus_kw=round(peak_surplus, 2),
            surplus_hours=len(hourly_surplus),
        )

    def evaluate_predictive_load(
        self,
        load: SurplusLoadConfig,
        reactive_loads: list[SurplusLoadConfig],
        *,
        now: datetime | None = None,
    ) -> PredictiveEvaluation:
        """Evaluate whether a predictive load should run today.

        Simulates today's SOC trajectory with:
        1. Solar production filling the battery
        2. Higher-priority reactive loads claiming surplus first
        3. The predictive load draining the battery during its schedule
        4. Remaining surplus recharging the battery

        Approved if SOC never drops below min_soc during or after the load runs.
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        daily_consumption = c.consumption_tracker.average(c.store.consumption_history)
        if now.weekday() >= 5:
            daily_consumption *= c.weekend_consumption_multiplier

        capacity = c.battery_capacity
        min_soc_kwh = capacity * c.min_soc / 100
        max_soc_kwh = capacity * c.max_charge_level / 100
        soc_kwh = capacity * c.current_soc / 100

        solar_profile, _ = self._build_solar_profile(now)

        # Predictive load schedule
        sched_start = load.schedule_start_hour
        sched_end = load.schedule_end_hour
        schedule_hours = sched_end - sched_start
        if schedule_hours <= 0:
            schedule_hours += 24  # wrap around midnight

        load_needs_kwh = load.power_kw * schedule_hours

        # Simulate from now through end of today
        min_soc_reached = soc_kwh
        reactive_claim_total = 0.0
        total_surplus = 0.0

        for hour in range(now.hour, 24):
            cons = self._hourly_consumption(hour, daily_consumption)
            solar = solar_profile.get((0, hour), 0.0)

            soc_kwh += solar - cons

            # Predictive load drains battery during its schedule
            if sched_start <= hour < sched_end:
                soc_kwh -= load.power_kw

            # Battery overflow = surplus available for reactive loads
            if soc_kwh > max_soc_kwh:
                overflow = soc_kwh - max_soc_kwh
                soc_kwh = max_soc_kwh

                # Reactive loads claim surplus by priority
                remaining = overflow
                for rl in sorted(reactive_loads, key=lambda x: x.priority):
                    claim = min(remaining, rl.power_kw)
                    reactive_claim_total += claim
                    remaining -= claim
                    if remaining <= 0:
                        break
                total_surplus += overflow
            else:
                soc_kwh = max(0.0, soc_kwh)

            if soc_kwh < min_soc_reached:
                min_soc_reached = soc_kwh

        min_soc_pct = round(min_soc_reached / capacity * 100, 1)
        surplus_budget = max(0.0, total_surplus - reactive_claim_total)

        approved = min_soc_reached >= min_soc_kwh
        if approved:
            reason = (
                f"Approved: surplus {surplus_budget:.1f} kWh, "
                f"min SOC {min_soc_pct:.0f}% stays above {c.min_soc:.0f}%"
            )
        else:
            reason = (
                f"Denied: min SOC would drop to {min_soc_pct:.0f}% "
                f"(below {c.min_soc:.0f}%)"
            )

        return PredictiveEvaluation(
            approved=approved,
            reason=reason,
            surplus_budget_kwh=round(surplus_budget, 2),
            load_needs_kwh=round(load_needs_kwh, 2),
            min_soc_after=min_soc_pct,
            reactive_claim_kwh=round(reactive_claim_total, 2),
        )

    def forecast_tomorrow_surplus(self, *, now: datetime | None = None) -> SurplusForecast:
        """Forecast tomorrow's solar surplus.

        Simulates SOC from hour 0 through 23 using tomorrow's solar profile.
        Starting SOC: assumes battery at min_soc (conservative — overnight drain,
        charging would raise it but surplus is best estimated conservatively).
        """
        if now is None:
            now = _default_now()
        c = self._coordinator

        tomorrow = now + timedelta(days=1)
        daily_consumption = c.consumption_tracker.average(c.store.consumption_history)
        if tomorrow.weekday() >= 5:
            daily_consumption *= c.weekend_consumption_multiplier

        capacity = c.battery_capacity
        max_soc_kwh = capacity * c.max_charge_level / 100
        # Conservative: assume battery starts at min_soc after overnight drain
        soc_kwh = capacity * c.min_soc / 100

        solar_profile, _ = self._build_solar_profile(now)

        hourly_surplus: dict[int, float] = {}
        battery_full_hour: int | None = None
        total_surplus = 0.0
        peak_surplus = 0.0

        for hour in range(24):
            cons = self._hourly_consumption(hour, daily_consumption)
            solar = solar_profile.get((1, hour), 0.0)

            net = solar - cons
            soc_kwh += net

            if soc_kwh > max_soc_kwh:
                surplus = soc_kwh - max_soc_kwh
                soc_kwh = max_soc_kwh
                hourly_surplus[hour] = round(surplus, 3)
                total_surplus += surplus
                peak_surplus = max(peak_surplus, surplus)

                if battery_full_hour is None:
                    battery_full_hour = hour
            else:
                soc_kwh = max(0.0, soc_kwh)

        return SurplusForecast(
            total_kwh=round(total_surplus, 2),
            hourly_kwh=hourly_surplus,
            battery_full_hour=battery_full_hour,
            peak_surplus_kw=round(peak_surplus, 2),
            surplus_hours=len(hourly_surplus),
        )
