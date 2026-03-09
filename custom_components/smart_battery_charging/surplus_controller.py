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

from .const import (
    CONF_GRID_EXPORT_POWER_SENSOR,
    CONF_NOTIFY_SURPLUS_LOAD,
    CONF_SURPLUS_LOADS,
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
                name=item["name"],
                switch_entity=item["switch_entity"],
                power_kw=float(item["power_kw"]),
                priority=int(item.get("priority", 1)),
                battery_on_threshold=float(item.get("battery_on_threshold", DEFAULT_SURPLUS_BATTERY_ON)),
                battery_off_threshold=float(item.get("battery_off_threshold", DEFAULT_SURPLUS_BATTERY_OFF)),
                margin_on_kw=float(item.get("margin_on_kw", DEFAULT_SURPLUS_MARGIN_ON)),
                margin_off_kw=float(item.get("margin_off_kw", DEFAULT_SURPLUS_MARGIN_OFF)),
                min_switch_interval=int(item.get("min_switch_interval", DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL)),
                mode=str(item.get("mode", "reactive")),
                schedule_start_hour=int(item.get("schedule_start_hour", DEFAULT_PREDICTIVE_SCHEDULE_START)),
                schedule_end_hour=int(item.get("schedule_end_hour", DEFAULT_PREDICTIVE_SCHEDULE_END)),
                evaluation_lead_minutes=int(item.get("evaluation_lead_minutes", DEFAULT_PREDICTIVE_LEAD_MINUTES)),
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

    def load_configs(self) -> None:
        """Reload configs from options and sync states."""
        self._configs = _load_configs_from_options(self._coordinator)
        # Initialize state for new loads, keep existing state for known loads
        new_states: dict[str, SurplusLoadState] = {}
        for cfg in self._configs:
            if cfg.switch_entity in self._states:
                new_states[cfg.switch_entity] = self._states[cfg.switch_entity]
            else:
                new_states[cfg.switch_entity] = SurplusLoadState()
        self._states = new_states

    def restore_states(self, stored: dict[str, Any]) -> None:
        """Restore runtime states from storage."""
        for entity_id, data in stored.items():
            if entity_id in self._states:
                self._states[entity_id].last_switch_time = data.get("last_switch_time", 0.0)
                self._states[entity_id].daily_runtime_seconds = data.get("daily_runtime_seconds", 0.0)

    def _sync_actual_switch_states(self) -> None:
        """Read actual switch states from HA (handles restart recovery)."""
        for cfg in self._configs:
            state = self._hass.states.get(cfg.switch_entity)
            if state is not None:
                actual_on = state.state == "on"
                self._states[cfg.switch_entity].is_running = actual_on

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

    def _compute_true_surplus(self, grid_export_kw: float) -> float:
        """Compute true surplus by adding back running loads' consumption.

        When a load is running, it reduces grid_export by its power_kw.
        True surplus = grid_export + sum(running_load.power_kw).
        """
        running_power = sum(
            cfg.power_kw
            for cfg in self._configs
            if self._states.get(cfg.switch_entity, SurplusLoadState()).is_running
        )
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
            st = self._states[cfg.switch_entity]

            # Skip if already evaluated today or aborted
            if st.predictive_approved is not None or st.predictive_aborted:
                continue

            # Check if we're within the evaluation window
            eval_hour = cfg.schedule_start_hour
            eval_minute = 60 - cfg.evaluation_lead_minutes
            if eval_minute < 0:
                eval_hour -= 1
                eval_minute += 60

            # Are we at or past evaluation time but before schedule start?
            current_minutes = now.hour * 60 + now.minute
            eval_minutes = eval_hour * 60 + eval_minute
            start_minutes = cfg.schedule_start_hour * 60

            if not (eval_minutes <= current_minutes < start_minutes):
                continue

            _LOGGER.info(
                "Evaluating predictive load '%s' (schedule %02d:00-%02d:00)",
                cfg.name, cfg.schedule_start_hour, cfg.schedule_end_hour,
            )

            try:
                evaluation = planner.evaluate_predictive_load(
                    cfg, reactive, now=now
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
            st = self._states[cfg.switch_entity]
            current_hour = now.hour

            in_schedule = cfg.schedule_start_hour <= current_hour < cfg.schedule_end_hour

            if in_schedule and st.predictive_approved and not st.predictive_aborted:
                if not st.is_running:
                    # Turn on at schedule start
                    try:
                        domain = cfg.switch_entity.split(".")[0]
                        await self._hass.services.async_call(
                            domain, "turn_on", {"entity_id": cfg.switch_entity}
                        )
                        st.is_running = True
                        st.last_switch_time = monotonic_now
                        _LOGGER.info(
                            "Predictive: %s turn on (schedule %02d:00-%02d:00, SOC=%.0f%%)",
                            cfg.name, cfg.schedule_start_hour, cfg.schedule_end_hour, soc,
                        )
                        if self._notifier and self._coordinator._opt(
                            CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                        ):
                            await self._notifier.async_notify_surplus_load(
                                cfg.name, True, 0.0, soc
                            )
                    except Exception:
                        _LOGGER.exception("Failed to turn on predictive load %s", cfg.name)
                else:
                    # Mid-run re-evaluation: abort if SOC trajectory goes below min_soc
                    if planner is not None:
                        try:
                            reactive = self._reactive_configs()
                            evaluation = planner.evaluate_predictive_load(
                                cfg, reactive, now=now
                            )
                            if not evaluation.approved:
                                _LOGGER.warning(
                                    "Predictive '%s': aborting mid-run — %s",
                                    cfg.name, evaluation.reason,
                                )
                                st.predictive_aborted = True
                                try:
                                    domain = cfg.switch_entity.split(".")[0]
                                    await self._hass.services.async_call(
                                        domain, "turn_off", {"entity_id": cfg.switch_entity}
                                    )
                                    st.is_running = False
                                    st.last_switch_time = monotonic_now
                                    if self._notifier and self._coordinator._opt(
                                        CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                                    ):
                                        await self._notifier.async_notify_surplus_load(
                                            cfg.name, False, 0.0, soc
                                        )
                                except Exception:
                                    _LOGGER.exception("Failed to abort predictive load %s", cfg.name)
                        except Exception:
                            _LOGGER.debug("Mid-run evaluation failed for %s", cfg.name)

            elif not in_schedule and st.is_running and cfg.mode == SURPLUS_MODE_PREDICTIVE:
                # Schedule ended — turn off
                try:
                    domain = cfg.switch_entity.split(".")[0]
                    await self._hass.services.async_call(
                        domain, "turn_off", {"entity_id": cfg.switch_entity}
                    )
                    st.is_running = False
                    st.last_switch_time = monotonic_now
                    _LOGGER.info(
                        "Predictive: %s turn off (schedule ended, SOC=%.0f%%)",
                        cfg.name, soc,
                    )
                    if self._notifier and self._coordinator._opt(
                        CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                    ):
                        await self._notifier.async_notify_surplus_load(
                            cfg.name, False, 0.0, soc
                        )
                except Exception:
                    _LOGGER.exception("Failed to turn off predictive load %s", cfg.name)

    async def async_on_tick(self) -> None:
        """Main tick — evaluate all loads and switch as needed.

        Called from the 2-minute tick in __init__.py.
        """
        if not self._configs:
            return

        monotonic_now = time.monotonic()
        now = self._get_now()

        # Sync with actual HA switch states
        self._sync_actual_switch_states()

        # Accumulate runtime for running loads
        for cfg in self._configs:
            st = self._states[cfg.switch_entity]
            if st.is_running and st.last_tick_time > 0:
                elapsed = monotonic_now - st.last_tick_time
                if 0 < elapsed < 600:  # Sanity: max 10 min per tick
                    st.daily_runtime_seconds += elapsed
            st.last_tick_time = monotonic_now

        # --- Predictive loads: evaluate and manage schedule ---
        await self._evaluate_predictive_loads(now)
        await self._tick_predictive_loads(now, monotonic_now)

        # --- Reactive loads: surplus-based switching ---
        reactive_configs = self._reactive_configs()
        if not reactive_configs:
            return

        # Get grid export power
        grid_export = self._get_grid_export_power()
        if grid_export is None:
            _LOGGER.debug("Grid export power sensor unavailable, skipping surplus tick")
            return

        soc = self._coordinator.current_soc

        true_surplus = self._compute_true_surplus(grid_export)

        _LOGGER.debug(
            "Surplus tick: grid_export=%.2f kW, true_surplus=%.2f kW, SOC=%.0f%%",
            grid_export, true_surplus, soc,
        )

        # Remaining surplus available for allocation
        available_surplus = true_surplus

        # Phase 1: Decide what should be ON/OFF (reactive loads only)
        desired: dict[str, bool] = {}
        for cfg in reactive_configs:  # sorted by priority (low first)
            st = self._states[cfg.switch_entity]

            if st.is_running:
                # Already running — should we turn OFF?
                should_off = (
                    soc < cfg.battery_off_threshold
                    or available_surplus < cfg.power_kw - cfg.margin_off_kw
                )
                if should_off:
                    desired[cfg.switch_entity] = False
                else:
                    desired[cfg.switch_entity] = True
                    available_surplus -= cfg.power_kw
            else:
                # Not running — should we turn ON?
                should_on = (
                    soc >= cfg.battery_on_threshold
                    and available_surplus >= cfg.power_kw + cfg.margin_on_kw
                )
                if should_on:
                    desired[cfg.switch_entity] = True
                    available_surplus -= cfg.power_kw
                else:
                    desired[cfg.switch_entity] = False

        # Phase 2: Execute switches (with anti-flap)
        for cfg in reactive_configs:
            st = self._states[cfg.switch_entity]
            want_on = desired.get(cfg.switch_entity, False)

            if want_on == st.is_running:
                continue  # No change needed

            # Anti-flap: check minimum switch interval
            if st.last_switch_time > 0:
                elapsed = monotonic_now - st.last_switch_time
                if elapsed < cfg.min_switch_interval:
                    _LOGGER.debug(
                        "Anti-flap: %s switch blocked (%.0fs < %ds)",
                        cfg.name, elapsed, cfg.min_switch_interval,
                    )
                    continue

            # Execute switch
            action = "turn_on" if want_on else "turn_off"
            try:
                domain = cfg.switch_entity.split(".")[0]
                await self._hass.services.async_call(
                    domain, action, {"entity_id": cfg.switch_entity}
                )
                st.is_running = want_on
                st.last_switch_time = monotonic_now
                _LOGGER.info(
                    "Surplus: %s %s (surplus=%.2f kW, SOC=%.0f%%)",
                    cfg.name, action.replace("_", " "), true_surplus, soc,
                )

                # Notify
                if self._notifier and self._coordinator._opt(
                    CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
                ):
                    await self._notifier.async_notify_surplus_load(
                        cfg.name, want_on, true_surplus, soc
                    )
            except Exception:
                _LOGGER.exception("Failed to %s %s", action, cfg.switch_entity)

    async def async_on_midnight(self) -> None:
        """Reset daily runtime counters, predictive state, and persist history."""
        runtime_data: dict[str, float] = {}
        for cfg in self._configs:
            st = self._states[cfg.switch_entity]
            if st.daily_runtime_seconds > 0:
                runtime_data[cfg.name] = round(st.daily_runtime_seconds / 3600, 2)
            st.daily_runtime_seconds = 0.0
            # Reset predictive state for tomorrow
            st.predictive_approved = None
            st.predictive_aborted = False

        if runtime_data:
            await self._coordinator.async_record_surplus_runtime(runtime_data)

    def get_states_for_storage(self) -> dict[str, Any]:
        """Serialize runtime states for persistence."""
        result: dict[str, Any] = {}
        for entity_id, st in self._states.items():
            result[entity_id] = {
                "last_switch_time": st.last_switch_time,
                "daily_runtime_seconds": st.daily_runtime_seconds,
            }
        return result

    def get_sensor_data(self) -> dict[str, Any]:
        """Compute data for surplus-related sensors."""
        grid_export = self._get_grid_export_power()
        true_surplus = self._compute_true_surplus(grid_export) if grid_export is not None else None

        active_loads = []
        load_details = []
        for cfg in self._configs:
            st = self._states.get(cfg.switch_entity, SurplusLoadState())
            if st.is_running:
                active_loads.append(cfg.name)
            detail: dict[str, Any] = {
                "name": cfg.name,
                "entity": cfg.switch_entity,
                "power_kw": cfg.power_kw,
                "priority": cfg.priority,
                "is_running": st.is_running,
                "runtime_today_h": round(st.daily_runtime_seconds / 3600, 2),
                "mode": cfg.mode,
            }
            if cfg.mode == SURPLUS_MODE_PREDICTIVE:
                detail["schedule"] = f"{cfg.schedule_start_hour:02d}:00-{cfg.schedule_end_hour:02d}:00"
                detail["approved"] = st.predictive_approved
                detail["aborted"] = st.predictive_aborted
            load_details.append(detail)

        total_surplus_power = sum(
            cfg.power_kw for cfg in self._configs
            if self._states.get(cfg.switch_entity, SurplusLoadState()).is_running
        )

        return {
            "surplus_active_loads": len(active_loads),
            "surplus_active_load_names": ", ".join(active_loads) if active_loads else "None",
            "surplus_total_power_kw": round(total_surplus_power, 2),
            "surplus_grid_export_kw": round(grid_export, 2) if grid_export is not None else None,
            "surplus_true_surplus_kw": round(true_surplus, 2) if true_surplus is not None else None,
            "surplus_load_details": load_details,
            "surplus_runtime_history": self._coordinator.store.surplus_runtime_history,
        }
