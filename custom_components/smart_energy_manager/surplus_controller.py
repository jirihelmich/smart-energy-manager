"""Surplus Load Controller — manages loads that consume solar surplus.

Turns ON loads (water heater, floor heating, etc.) when battery is full
and solar surplus exceeds the load's power draw. Uses priority ordering
so higher-priority loads get surplus first. Includes anti-flap protection
and true surplus calculation (accounts for running load's own consumption).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GRID_EXPORT_POWER_SENSOR,
    CONF_HOUSE_CONSUMPTION_POWER_SENSOR,
    CONF_NEGATIVE_PRICE_ABSORB,
    CONF_NOTIFY_SURPLUS_LOAD,
    CONF_OUTDOOR_TEMP_SENSOR,
    CONF_PROACTIVE_SOC_THRESHOLD,
    CONF_PV_POWER_SENSOR,
    CONF_SURPLUS_LOADS,
    DEFAULT_MAX_OUTDOOR_TEMP,
    DEFAULT_NEGATIVE_PRICE_ABSORB,
    DEFAULT_PROACTIVE_SOC_THRESHOLD,
    DEFAULT_PREDICTIVE_LEAD_MINUTES,
    DEFAULT_PREDICTIVE_SCHEDULE_END,
    DEFAULT_PREDICTIVE_SCHEDULE_START,
    DEFAULT_NOTIFY_SURPLUS_LOAD,
    DEFAULT_SURPLUS_BATTERY_OFF,
    DEFAULT_SURPLUS_BATTERY_ON,
    DEFAULT_SURPLUS_MARGIN_OFF,
    DEFAULT_SURPLUS_MARGIN_ON,
    DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL,
    SURPLUS_MODE_PREDICTIVE,
    SURPLUS_RUNTIME_HISTORY_DAYS,
)
from .models import PredictiveEvaluation, SurplusLoadConfig, SurplusLoadState

if TYPE_CHECKING:
    from .coordinator import SmartBatteryCoordinator
    from .notifier import ChargingNotifier

_LOGGER = logging.getLogger(__name__)


def _load_configs_from_options(coordinator: SmartBatteryCoordinator) -> list[SurplusLoadConfig]:
    """Parse surplus load configs from config entry options."""
    raw = coordinator._opt(CONF_SURPLUS_LOADS, [])
    if not raw or not isinstance(raw, list):
        return []
    configs = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            configs.append(SurplusLoadConfig(
                id=item.get("id", f"{item['switch_entity']}_{item.get('mode', 'reactive')}"),
                name=item["name"],
                switch_entity=item["switch_entity"],
                power_kw=float(item["power_kw"]),
                priority=int(item.get("priority", 1)),
                battery_on_threshold=float(item.get("battery_on_threshold", DEFAULT_SURPLUS_BATTERY_ON)),
                battery_off_threshold=float(item.get("battery_off_threshold", DEFAULT_SURPLUS_BATTERY_OFF)),
                margin_on_kw=float(item.get("margin_on_kw", DEFAULT_SURPLUS_MARGIN_ON)),
                margin_off_kw=float(item.get("margin_off_kw", DEFAULT_SURPLUS_MARGIN_OFF)),
                min_switch_interval=int(item.get("min_switch_interval", DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL)),
                power_sensor=str(item.get("power_sensor", "")),
                mode=str(item.get("mode", "reactive")),
                schedule_start_hour=int(item.get("schedule_start_hour", DEFAULT_PREDICTIVE_SCHEDULE_START)),
                schedule_end_hour=int(item.get("schedule_end_hour", DEFAULT_PREDICTIVE_SCHEDULE_END)),
                evaluation_lead_minutes=int(item.get("evaluation_lead_minutes", DEFAULT_PREDICTIVE_LEAD_MINUTES)),
                max_outdoor_temp=float(item.get("max_outdoor_temp", DEFAULT_MAX_OUTDOOR_TEMP)),
            ))
        except (KeyError, ValueError, TypeError):
            _LOGGER.warning("Invalid surplus load config: %s", item)
    configs.sort(key=lambda c: c.priority)
    return configs


class SurplusLoadController:
    """Manages surplus loads based on solar export and battery SOC."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SmartBatteryCoordinator,
        notifier: ChargingNotifier | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._notifier = notifier
        self._states: dict[str, SurplusLoadState] = {}
        self._configs: list[SurplusLoadConfig] = []
        self._daily_surplus_seconds: float = 0.0  # Actual seconds with grid export > 0 today
        self._last_surplus_tick_time: float = 0.0
        self._last_surplus_energy_today: float = 0.0

    @property
    def surplus_energy_today_kwh(self) -> float:
        """Total energy consumed by surplus loads today (kWh)."""
        return self._last_surplus_energy_today

    def load_configs(self) -> None:
        """Reload configs from options and sync states."""
        self._configs = _load_configs_from_options(self._coordinator)
        # Initialize state for new loads, keep existing state for known loads
        # Key by load ID (unique per load, even if sharing switch entity)
        new_states: dict[str, SurplusLoadState] = {}
        for cfg in self._configs:
            if cfg.id in self._states:
                new_states[cfg.id] = self._states[cfg.id]
            else:
                new_states[cfg.id] = SurplusLoadState()
        self._states = new_states

    def restore_states(self, stored: dict[str, Any]) -> None:
        """Restore runtime states from storage.

        Accepts both old format (keyed by switch_entity) and new (keyed by load id).
        """
        for key, data in stored.items():
            if key in self._states:
                # Direct match by load ID (new format)
                self._states[key].last_switch_time = 0.0
                self._states[key].daily_runtime_seconds = data.get("daily_runtime_seconds", 0.0)
                self._states[key].controlled_by_automation = data.get("controlled_by_automation", False)
                self._states[key].daily_energy_kwh = data.get("daily_energy_kwh", 0.0)
            else:
                # Legacy: try matching by switch_entity for migration
                for cfg in self._configs:
                    if cfg.switch_entity == key and cfg.id in self._states:
                        st = self._states[cfg.id]
                        st.last_switch_time = 0.0
                        st.daily_runtime_seconds = data.get("daily_runtime_seconds", 0.0)
                        st.controlled_by_automation = data.get("controlled_by_automation", False)
                        st.daily_energy_kwh = data.get("daily_energy_kwh", 0.0)
                        break

    def _sync_actual_switch_states(self) -> None:
        """Sync load states with actual HA switch states.

        For loads NOT controlled by automation: mirror the physical switch state
        so the tick logic can evaluate correctly (e.g. device was on at startup).
        BUT skip if another load on the same switch is already controlling it
        (prevents reactive loads from mirroring a predictive load's switch state).
        For loads controlled by automation: only detect external turn-off.
        """
        # Build set of switch entities that are automation-controlled by any load
        controlled_switches: set[str] = set()
        for cfg in self._configs:
            st = self._states[cfg.id]
            if st.controlled_by_automation and st.is_running:
                controlled_switches.add(cfg.switch_entity)

        for cfg in self._configs:
            st = self._states[cfg.id]
            state = self._hass.states.get(cfg.switch_entity)
            if state is None:
                continue
            actual_on = state.state == "on"

            if not st.controlled_by_automation:
                # Not automation-controlled: mirror physical state
                # BUT skip if another load already owns this switch
                if cfg.switch_entity in controlled_switches:
                    continue
                if actual_on != st.is_running:
                    st.is_running = actual_on
            else:
                # Automation-controlled: detect external turn-off
                if not actual_on and st.is_running:
                    st.is_running = False
                    st.controlled_by_automation = False

    async def _reconcile_switches(self) -> None:
        """Reconcile physical switches with load desires.

        A switch is ON if ANY load on that switch has is_running=True.
        A switch is OFF only when ALL loads on that switch have is_running=False.
        """
        # Group loads by switch entity
        switch_desires: dict[str, bool] = {}
        for cfg in self._configs:
            st = self._states.get(cfg.id, SurplusLoadState())
            current = switch_desires.get(cfg.switch_entity, False)
            switch_desires[cfg.switch_entity] = current or st.is_running

        for switch_entity, should_be_on in switch_desires.items():
            actual_on = self._is_device_on(switch_entity)
            if should_be_on == actual_on:
                continue
            action = "turn_on" if should_be_on else "turn_off"
            try:
                domain = switch_entity.split(".")[0]
                await self._hass.services.async_call(
                    domain, action, {"entity_id": switch_entity}
                )
                _LOGGER.info("Switch %s → %s", switch_entity, action)
            except Exception:
                _LOGGER.exception("Failed to %s %s", action, switch_entity)

    def _get_outdoor_temp(self) -> float | None:
        """Read outdoor temperature from configured sensor."""
        sensor = self._coordinator._opt(CONF_OUTDOOR_TEMP_SENSOR, "")
        if not sensor:
            return None
        state = self._hass.states.get(sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _is_temp_blocked(self, cfg: SurplusLoadConfig) -> bool:
        """Check if load should be skipped due to outdoor temperature."""
        if cfg.max_outdoor_temp <= 0:
            return False
        temp = self._get_outdoor_temp()
        if temp is None:
            return False  # Sensor unavailable — don't block
        return temp > cfg.max_outdoor_temp

    def _is_device_on(self, entity_id: str) -> bool:
        """Read actual switch state from HA."""
        state = self._hass.states.get(entity_id)
        return state is not None and state.state == "on"

    def _read_power_sensor(self, cfg: SurplusLoadConfig) -> float | None:
        """Read real-time power from a load's power sensor (kW).

        Returns None if no sensor configured or unavailable.
        """
        if not cfg.power_sensor:
            return None
        state = self._hass.states.get(cfg.power_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
            uom = state.attributes.get("unit_of_measurement", "")
            if uom in ("W", "w"):
                value = value / 1000.0
            return value
        except (ValueError, TypeError):
            return None

    @property
    def configs(self) -> list[SurplusLoadConfig]:
        return self._configs

    @property
    def states(self) -> dict[str, SurplusLoadState]:
        return self._states

    def _get_grid_export_power(self) -> float | None:
        """Get instantaneous grid export power in kW."""
        entity_id = self._coordinator._opt(CONF_GRID_EXPORT_POWER_SENSOR, "")
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
            # Check unit — if W, convert to kW
            uom = state.attributes.get("unit_of_measurement", "")
            if uom in ("W", "w"):
                value = value / 1000.0
            return value
        except (ValueError, TypeError):
            return None

    def _actual_power_kw(self, cfg: SurplusLoadConfig) -> float:
        """Get actual power draw for a load (real sensor or fallback to configured max)."""
        real = self._read_power_sensor(cfg)
        return real if real is not None else cfg.power_kw

    def _get_pv_power(self) -> float | None:
        """Get instantaneous PV production power in kW."""
        entity_id = self._coordinator._opt(CONF_PV_POWER_SENSOR, "")
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
            uom = state.attributes.get("unit_of_measurement", "")
            if uom in ("W", "w"):
                value = value / 1000.0
            return value
        except (ValueError, TypeError):
            return None

    def _get_house_consumption_power(self) -> float | None:
        """Get instantaneous house consumption power in kW."""
        entity_id = self._coordinator._opt(CONF_HOUSE_CONSUMPTION_POWER_SENSOR, "")
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            value = float(state.state)
            uom = state.attributes.get("unit_of_measurement", "")
            if uom in ("W", "w"):
                value = value / 1000.0
            return abs(value)  # Ensure positive
        except (ValueError, TypeError):
            return None

    def _compute_anticipated_surplus(self) -> float | None:
        """Compute anticipated surplus from PV power minus house consumption.

        Returns kW of anticipated surplus, or None if sensors unavailable.
        Used for proactive reactive mode: turn on loads before actual export
        starts, based on PV production exceeding consumption while SOC is high.
        """
        pv = self._get_pv_power()
        consumption = self._get_house_consumption_power()
        if pv is None or consumption is None:
            return None
        # Add back power of running loads (they reduce the consumption sensor reading
        # but we want to know if PV covers base consumption + this load)
        seen_switches: set[str] = set()
        running_power = 0.0
        for cfg in self._configs:
            if cfg.switch_entity in seen_switches:
                continue
            if self._is_device_on(cfg.switch_entity):
                seen_switches.add(cfg.switch_entity)
                running_power += self._actual_power_kw(cfg)
        # Anticipated surplus = PV - (consumption - running_loads)
        # = PV - base_consumption
        base_consumption = max(0.0, consumption - running_power)
        return pv - base_consumption

    def _get_spot_price(self) -> float | None:
        """Get current spot electricity price in CZK/kWh."""
        return self._coordinator.current_price

    def _is_negative_price_absorb(self) -> bool:
        """Check if negative price absorption is enabled and price is <= 0."""
        if not self._coordinator._opt(CONF_NEGATIVE_PRICE_ABSORB, DEFAULT_NEGATIVE_PRICE_ABSORB):
            return False
        price = self._get_spot_price()
        if price is None:
            return False
        return price <= 0

    def _compute_true_surplus(self, grid_export_kw: float) -> float:
        """Compute true surplus by adding back running loads' consumption.

        When a load is running, it reduces grid_export by its actual power draw.
        True surplus = grid_export + sum(running_load.actual_power).

        Deduplicates by switch_entity: multiple loads sharing a switch only
        count the physical device's power once.
        """
        seen_switches: set[str] = set()
        running_power = 0.0
        for cfg in self._configs:
            if cfg.switch_entity in seen_switches:
                continue
            if self._is_device_on(cfg.switch_entity):
                seen_switches.add(cfg.switch_entity)
                running_power += self._actual_power_kw(cfg)
        return grid_export_kw + running_power

    def _get_now(self) -> datetime:
        """Get current time (for testability)."""
        try:
            from homeassistant.util import dt as dt_util
            return dt_util.now()
        except Exception:
            return datetime.now()

    def _reactive_configs(self) -> list[SurplusLoadConfig]:
        """Get reactive-mode configs."""
        return [c for c in self._configs if c.mode != SURPLUS_MODE_PREDICTIVE]

    def _predictive_configs(self) -> list[SurplusLoadConfig]:
        """Get predictive-mode configs."""
        return [c for c in self._configs if c.mode == SURPLUS_MODE_PREDICTIVE]

    async def _evaluate_predictive_loads(self, now: datetime) -> None:
        """Evaluate predictive loads before their schedule starts.

        Runs at evaluation_lead_minutes before schedule_start_hour.
        Uses planner.evaluate_predictive_load() to simulate SOC trajectory.
        """
        planner = self._coordinator.planner
        if planner is None:
            return

        reactive = self._reactive_configs()

        for cfg in self._predictive_configs():
            st = self._states[cfg.id]

            # Skip if already evaluated today or aborted
            if st.predictive_approved is not None or st.predictive_aborted:
                continue

            # Skip if outdoor temp exceeds threshold
            if self._is_temp_blocked(cfg):
                st.predictive_approved = False
                _LOGGER.info("Predictive '%s': skipped — outdoor temp too high", cfg.name)
                continue

            # Check if we're within the evaluation window or in-schedule (late eval)
            current_minutes = now.hour * 60 + now.minute
            start_minutes = cfg.schedule_start_hour * 60
            end_minutes = cfg.schedule_end_hour * 60
            eval_minutes = start_minutes - cfg.evaluation_lead_minutes

            # Evaluate if: (a) in pre-schedule lead window, or
            # (b) already in schedule but not yet evaluated (e.g. after HA restart)
            in_eval_window = eval_minutes <= current_minutes < start_minutes
            in_schedule_unevaluated = start_minutes <= current_minutes < end_minutes

            if not (in_eval_window or in_schedule_unevaluated):
                continue

            _LOGGER.info(
                "Evaluating predictive load '%s' (schedule %02d:00-%02d:00)",
                cfg.name, cfg.schedule_start_hour, cfg.schedule_end_hour,
            )

            try:
                factors = self.get_utilization_factors()
                evaluation = planner.evaluate_predictive_load(
                    cfg, reactive, now=now, utilization_factors=factors
                )
            except Exception:
                _LOGGER.exception("Failed to evaluate predictive load '%s'", cfg.name)
                st.predictive_approved = False
                continue

            st.predictive_approved = evaluation.approved
            _LOGGER.info(
                "Predictive '%s': %s — %s",
                cfg.name,
                "APPROVED" if evaluation.approved else "DENIED",
                evaluation.reason,
            )

            # Notify
            if self._notifier and self._coordinator._opt(
                CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
            ):
                await self._notifier.async_notify_predictive_evaluation(
                    cfg.name, evaluation
                )

    async def _tick_predictive_loads(self, now: datetime, monotonic_now: float) -> None:
        """Handle predictive loads during their schedule.

        Turns on at schedule_start, off at schedule_end.
        Re-evaluates mid-run: aborts if SOC projection drops below min_soc.
        """
        planner = self._coordinator.planner
        soc = self._coordinator.current_soc

        for cfg in self._predictive_configs():
            st = self._states[cfg.id]
            current_hour = now.hour

            in_schedule = cfg.schedule_start_hour <= current_hour < cfg.schedule_end_hour

            if in_schedule and st.predictive_approved and not st.predictive_aborted:
                if not st.is_running:
                    # Schedule start — set desire to ON
                    st.is_running = True
                    st.controlled_by_automation = True
                    st.last_switch_time = monotonic_now
                    _LOGGER.info(
                        "Predictive: %s wants ON (schedule %02d:00-%02d:00, SOC=%.0f%%)",
                        cfg.name, cfg.schedule_start_hour, cfg.schedule_end_hour, soc,
                    )
                    if not st.predictive_notified and self._notifier and self._coordinator._opt(
                        CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                    ):
                        await self._notifier.async_notify_surplus_load(
                            cfg.name, True, 0.0, soc
                        )
                        st.predictive_notified = True
                else:
                    # Mid-run re-evaluation: abort if SOC trajectory goes below min_soc
                    if planner is not None:
                        try:
                            reactive = self._reactive_configs()
                            factors = self.get_utilization_factors()
                            evaluation = planner.evaluate_predictive_load(
                                cfg, reactive, now=now,
                                utilization_factors=factors,
                            )
                            if not evaluation.approved:
                                _LOGGER.warning(
                                    "Predictive '%s': aborting mid-run — %s",
                                    cfg.name, evaluation.reason,
                                )
                                st.predictive_aborted = True
                                st.is_running = False
                                st.controlled_by_automation = True
                                st.last_switch_time = monotonic_now
                                if self._notifier and self._coordinator._opt(
                                    CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                                ):
                                    await self._notifier.async_notify_surplus_load(
                                        cfg.name, False, 0.0, soc
                                    )
                        except Exception:
                            _LOGGER.debug("Mid-run evaluation failed for %s", cfg.name)

            elif not in_schedule and st.is_running and cfg.mode == SURPLUS_MODE_PREDICTIVE:
                # Schedule ended — set desire to OFF
                st.is_running = False
                st.controlled_by_automation = True
                st.last_switch_time = monotonic_now
                _LOGGER.info(
                    "Predictive: %s wants OFF (schedule ended, SOC=%.0f%%)",
                    cfg.name, soc,
                )
                if not st.predictive_aborted and self._notifier and self._coordinator._opt(
                    CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                ):
                    st.predictive_aborted = True
                    await self._notifier.async_notify_surplus_load(
                        cfg.name, False, 0.0, soc
                    )

    async def async_on_tick(self) -> None:
        """Main tick — evaluate all loads and switch as needed.

        Called from the 2-minute tick in __init__.py.
        """
        if not self._configs:
            return

        monotonic_now = time.monotonic()
        now = self._get_now()

        # Track actual surplus time (grid export > 0)
        grid_export_now = self._get_grid_export_power()
        if grid_export_now is not None and grid_export_now > 0 and self._last_surplus_tick_time > 0:
            elapsed = monotonic_now - self._last_surplus_tick_time
            if 0 < elapsed < 600:
                self._daily_surplus_seconds += elapsed
        self._last_surplus_tick_time = monotonic_now

        # Sync with actual HA switch states (detects manual changes)
        self._sync_actual_switch_states()

        # Accumulate runtime and energy for loads that want to be on AND device is on
        for cfg in self._configs:
            st = self._states[cfg.id]
            device_on = self._is_device_on(cfg.switch_entity)
            if st.is_running and device_on and st.last_tick_time > 0:
                elapsed = monotonic_now - st.last_tick_time
                if 0 < elapsed < 600:  # Sanity: max 10 min per tick
                    st.daily_runtime_seconds += elapsed
                    # Accumulate energy from real power sensor
                    real_power = self._read_power_sensor(cfg)
                    power_kw = real_power if real_power is not None else cfg.power_kw
                    st.daily_energy_kwh += power_kw * (elapsed / 3600)
            st.last_tick_time = monotonic_now

        # --- Predictive loads: evaluate and manage schedule ---
        await self._evaluate_predictive_loads(now)
        await self._tick_predictive_loads(now, monotonic_now)

        # --- Reactive loads: surplus-based switching ---
        reactive_configs = self._reactive_configs()
        if not reactive_configs:
            await self._reconcile_switches()
            return

        # Get grid export power
        grid_export = self._get_grid_export_power()
        if grid_export is None:
            _LOGGER.debug("Grid export power sensor unavailable, skipping surplus tick")
            await self._reconcile_switches()
            return

        soc = self._coordinator.current_soc

        true_surplus = self._compute_true_surplus(grid_export)

        # Proactive mode: use anticipated surplus (PV - consumption) when SOC
        # is high enough but actual export hasn't started yet.
        proactive_threshold = self._coordinator._opt(
            CONF_PROACTIVE_SOC_THRESHOLD, DEFAULT_PROACTIVE_SOC_THRESHOLD
        )
        anticipated = self._compute_anticipated_surplus()
        if anticipated is not None and soc >= proactive_threshold and anticipated > true_surplus:
            _LOGGER.info(
                "Proactive surplus: anticipated=%.2f kW > true=%.2f kW (SOC=%.0f%% >= %.0f%%)",
                anticipated, true_surplus, soc, proactive_threshold,
            )
            true_surplus = anticipated

        _LOGGER.info(
            "Surplus tick: grid_export=%.2f kW, true_surplus=%.2f kW, SOC=%.0f%%",
            grid_export, true_surplus, soc,
        )

        # Remaining surplus available for allocation
        available_surplus = true_surplus

        # Negative price absorption: force all loads ON to avoid exporting at a loss
        negative_absorb = self._is_negative_price_absorb()
        if negative_absorb:
            _LOGGER.info("Negative spot price — forcing all reactive loads ON to absorb energy")

        # Phase 1: Decide what should be ON/OFF (reactive loads only)
        desired: dict[str, bool] = {}
        for cfg in reactive_configs:  # sorted by priority (low first)
            st = self._states[cfg.id]

            temp_blocked = self._is_temp_blocked(cfg)

            # During negative prices: force ON (skip SOC/surplus checks)
            if negative_absorb and not temp_blocked:
                desired[cfg.switch_entity] = True
                st.consecutive_off_ticks = 0
                st.last_reason = "Negative price — forced ON"
                available_surplus -= cfg.power_kw
                continue

            if st.is_running:
                # Already running — should we turn OFF?
                should_off = (
                    temp_blocked
                    or soc < cfg.battery_off_threshold
                    or available_surplus < cfg.power_kw - cfg.margin_off_kw
                )
                if should_off:
                    if temp_blocked:
                        reason = "Outdoor temp too high"
                    elif soc < cfg.battery_off_threshold:
                        reason = f"SOC {soc:.0f}% < {cfg.battery_off_threshold:.0f}%"
                    else:
                        reason = f"Surplus {available_surplus:.2f} kW too low"
                    st.consecutive_off_ticks += 1
                    # Require 3 consecutive ticks (~6 min) before turning off
                    # to ride through brief clouds
                    if st.consecutive_off_ticks >= 3:
                        desired[cfg.switch_entity] = False
                        st.last_reason = f"OFF: {reason}"
                    else:
                        _LOGGER.info(
                            "Turn-off delayed %s: %d/3 ticks",
                            cfg.name, st.consecutive_off_ticks,
                        )
                        desired[cfg.switch_entity] = True
                        available_surplus -= cfg.power_kw
                else:
                    st.consecutive_off_ticks = 0
                    desired[cfg.switch_entity] = True
                    st.last_reason = "Running — surplus OK"
                    available_surplus -= cfg.power_kw
            else:
                # Not running — should we turn ON?
                should_on = (
                    not temp_blocked
                    and soc >= cfg.battery_on_threshold
                    and available_surplus >= cfg.margin_on_kw
                )
                if not should_on:
                    if temp_blocked:
                        st.last_reason = "Blocked: outdoor temp too high"
                    elif soc < cfg.battery_on_threshold:
                        st.last_reason = f"Waiting: SOC {soc:.0f}% < {cfg.battery_on_threshold:.0f}%"
                    else:
                        st.last_reason = f"Waiting: surplus {available_surplus:.2f} kW < {cfg.margin_on_kw:.2f} kW margin"
                    _LOGGER.info(
                        "Reactive skip %s: %s",
                        cfg.name, st.last_reason,
                    )
                if should_on:
                    desired[cfg.switch_entity] = True
                    st.last_reason = f"ON: surplus {available_surplus:.2f} kW, SOC {soc:.0f}%"
                    available_surplus -= cfg.power_kw
                else:
                    desired[cfg.switch_entity] = False

        # Phase 2: Update each load's desire (is_running) with anti-flap
        for cfg in reactive_configs:
            st = self._states[cfg.id]
            want_on = desired.get(cfg.switch_entity, False)

            if want_on == st.is_running:
                if want_on and not st.controlled_by_automation:
                    st.controlled_by_automation = True
                continue

            # Anti-flap: protect relay from rapid switching
            if st.last_switch_time > 0:
                elapsed = monotonic_now - st.last_switch_time
                if 0 < elapsed < cfg.min_switch_interval:
                    _LOGGER.info(
                        "Anti-flap: %s blocked (%.0fs < %ds)",
                        cfg.name, elapsed, cfg.min_switch_interval,
                    )
                    continue

            # Update this load's desire
            st.is_running = want_on
            st.controlled_by_automation = True
            st.last_switch_time = monotonic_now

            _LOGGER.info(
                "Surplus: %s wants %s (surplus=%.2f kW, SOC=%.0f%%)",
                cfg.name, "ON" if want_on else "OFF", true_surplus, soc,
            )

            # Notify only on actual state transitions (deduplicate)
            if st.last_notified_on != want_on and self._notifier and self._coordinator._opt(
                CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
            ):
                st.last_notified_on = want_on
                await self._notifier.async_notify_surplus_load(
                    cfg.name, want_on, true_surplus, soc
                )

        # Phase 3: Reconcile physical switches
        # A switch turns ON if ANY load wants it on, OFF only if ALL loads agree off
        await self._reconcile_switches()

    def get_utilization_factors(self) -> dict[str, float]:
        """Compute average utilization factor per load from history.

        Utilization = avg(runtime_hours / surplus_hours) over historical days.
        Returns a factor between 0.0 and 1.0 per load name.
        Falls back to 1.0 (conservative) when no history exists.
        """
        history = self._coordinator.store.surplus_runtime_history
        if not history:
            return {}

        # Accumulate runtime and surplus hours per load
        load_totals: dict[str, list[float]] = {}
        for entry in history:
            surplus_hours = entry.get("surplus_hours", 0)
            if surplus_hours <= 0:
                continue
            loads = entry.get("loads", {})
            for name, runtime_h in loads.items():
                factor = min(1.0, runtime_h / surplus_hours)
                load_totals.setdefault(name, []).append(factor)

        return {
            name: round(sum(factors) / len(factors), 2)
            for name, factors in load_totals.items()
            if factors
        }

    async def async_on_midnight(self) -> None:
        """Reset daily runtime counters, predictive state, and persist history."""
        runtime_data: dict[str, float] = {}
        energy_data: dict[str, float] = {}
        for cfg in self._configs:
            st = self._states[cfg.id]
            if st.daily_runtime_seconds > 0:
                runtime_data[cfg.name] = round(st.daily_runtime_seconds / 3600, 2)
            if st.daily_energy_kwh > 0:
                energy_data[cfg.name] = round(st.daily_energy_kwh, 2)
            st.daily_runtime_seconds = 0.0
            st.daily_energy_kwh = 0.0
            # Reset predictive state for tomorrow
            st.predictive_approved = None
            st.predictive_aborted = False
            st.predictive_notified = False
            st.last_notified_on = None

        # Use actual tracked surplus hours (not forecast)
        surplus_hours = round(self._daily_surplus_seconds / 3600)
        self._daily_surplus_seconds = 0.0

        if runtime_data:
            grid_export = self._coordinator.grid_export_today
            await self._coordinator.async_record_surplus_runtime(
                runtime_data, surplus_hours=surplus_hours,
                energy_data=energy_data,
                grid_export_kwh=grid_export,
            )

    def get_states_for_storage(self) -> dict[str, Any]:
        """Serialize runtime states for persistence."""
        result: dict[str, Any] = {}
        for entity_id, st in self._states.items():
            result[entity_id] = {
                "last_switch_time": st.last_switch_time,
                "daily_runtime_seconds": st.daily_runtime_seconds,
                "controlled_by_automation": st.controlled_by_automation,
                "daily_energy_kwh": st.daily_energy_kwh,
            }
        return result

    def get_sensor_data(self) -> dict[str, Any]:
        """Compute data for surplus-related sensors."""
        grid_export = self._get_grid_export_power()
        true_surplus = self._compute_true_surplus(grid_export) if grid_export is not None else None
        anticipated = self._compute_anticipated_surplus()

        active_loads = []
        load_details = []
        for cfg in self._configs:
            st = self._states.get(cfg.id, SurplusLoadState())
            device_on = self._is_device_on(cfg.switch_entity)
            if st.is_running:
                active_loads.append(cfg.name)
            real_power = self._read_power_sensor(cfg)
            detail: dict[str, Any] = {
                "name": cfg.name,
                "entity": cfg.switch_entity,
                "power_kw": cfg.power_kw,
                "actual_power_kw": round(real_power, 2) if real_power is not None else None,
                "priority": cfg.priority,
                "is_running": st.is_running,
                "is_device_on": device_on,
                "controlled_by_automation": st.controlled_by_automation,
                "runtime_today_h": round(st.daily_runtime_seconds / 3600, 2),
                "energy_today_kwh": round(st.daily_energy_kwh, 2),
                "mode": cfg.mode,
                "reason": st.last_reason,
            }
            if cfg.mode == SURPLUS_MODE_PREDICTIVE:
                detail["schedule"] = f"{cfg.schedule_start_hour:02d}:00-{cfg.schedule_end_hour:02d}:00"
                detail["approved"] = st.predictive_approved
                detail["aborted"] = st.predictive_aborted
            load_details.append(detail)

        total_surplus_power = sum(
            cfg.power_kw for cfg in self._configs
            if self._states.get(cfg.id, SurplusLoadState()).is_running
        )

        self._last_surplus_energy_today = round(sum(
            self._states.get(cfg.id, SurplusLoadState()).daily_energy_kwh
            for cfg in self._configs
        ), 2)

        # Build today's in-progress entry for the runtime history chart
        today_str = dt_util.now().strftime("%Y-%m-%d")
        today_runtime: dict[str, float] = {}
        today_energy: dict[str, float] = {}
        for cfg in self._configs:
            st = self._states.get(cfg.id, SurplusLoadState())
            if st.daily_runtime_seconds > 0:
                today_runtime[cfg.name] = round(st.daily_runtime_seconds / 3600, 2)
            if st.daily_energy_kwh > 0:
                today_energy[cfg.name] = round(st.daily_energy_kwh, 2)
        today_entry: dict[str, Any] = {
            "date": today_str,
            "loads": today_runtime,
            "surplus_hours": round(self._daily_surplus_seconds / 3600),
            "energy_kwh": today_energy,
            "grid_export_kwh": round(self._coordinator.grid_export_today, 2),
        }
        history = self._coordinator.store.surplus_runtime_history
        # Prepend today's live data (replace if midnight already recorded today)
        if history and history[0].get("date") == today_str:
            runtime_history = [today_entry] + history[1:]
        else:
            runtime_history = [today_entry] + history

        return {
            "surplus_active_loads": len(active_loads),
            "surplus_active_load_names": ", ".join(active_loads) if active_loads else "None",
            "surplus_total_power_kw": round(total_surplus_power, 2),
            "surplus_grid_export_kw": round(grid_export, 2) if grid_export is not None else None,
            "surplus_true_surplus_kw": round(true_surplus, 2) if true_surplus is not None else None,
            "surplus_anticipated_kw": round(anticipated, 2) if anticipated is not None else None,
            "surplus_load_details": load_details,
            "surplus_utilization_factors": self.get_utilization_factors(),
            "surplus_runtime_history": runtime_history,
        }
