"""Charging notification gateway — the ONLY class that calls notify services.

Sends rich notifications for planning, charging lifecycle, and morning safety events.
Each notification type has an independent toggle in the options flow.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_NOTIFICATION_SERVICE,
    CONF_NOTIFY_BATTERY_FULL,
    CONF_NOTIFY_BATTERY_LOW,
    CONF_NOTIFY_CHARGING_COMPLETE,
    CONF_NOTIFY_CHARGING_STALLED,
    CONF_NOTIFY_CHARGING_START,
    CONF_NOTIFY_MORNING_SAFETY,
    CONF_NOTIFY_PLANNING,
    CONF_NOTIFY_SENSOR_UNAVAILABLE,
    CONF_NOTIFY_SURPLUS_LOAD,
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
)
from .models import ChargingSchedule, ChargingSession, EnergyDeficit, OvernightNeed, PredictiveEvaluation

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import SmartBatteryCoordinator

_LOGGER = logging.getLogger(__name__)


class ChargingNotifier:
    """Single gateway for all charging notifications."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SmartBatteryCoordinator,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._last_plan_hash: str | None = None
        self._last_plan_date: date | None = None

    @property
    def _service_name(self) -> str:
        return str(
            self._coordinator._opt(
                CONF_NOTIFICATION_SERVICE, DEFAULT_NOTIFICATION_SERVICE
            )
        )

    def _is_enabled(self, toggle_key: str, default: bool) -> bool:
        return bool(self._coordinator._opt(toggle_key, default))

    async def _async_send(self, title: str, message: str) -> None:
        """Send a notification via the configured service. Safe to call even if unconfigured."""
        service = self._service_name
        if not service:
            return
        try:
            await self._hass.services.async_call(
                "notify",
                service,
                {"title": title, "message": message},
            )
        except Exception:
            _LOGGER.exception("Failed to send notification via notify.%s", service)

    def _compute_plan_hash(
        self,
        schedule: ChargingSchedule | None,
        deficit: EnergyDeficit,
        overnight: OvernightNeed | None = None,
    ) -> str:
        """Compute a hash of the plan for deduplication.

        Uses only stable values (charge_needed, schedule params) — NOT raw
        deficit which fluctuates as solar tracking updates.
        """
        overnight_key = f":{overnight.charge_needed:.0f}" if overnight else ""
        if schedule is None:
            key = f"no_schedule:{deficit.charge_needed:.0f}{overnight_key}"
        else:
            key = (
                f"{schedule.start_hour}:{schedule.end_hour}:"
                f"{schedule.required_kwh:.1f}:{schedule.target_soc:.0f}:"
                f"{schedule.avg_price:.2f}{overnight_key}"
            )
        return hashlib.md5(key.encode()).hexdigest()[:12]

    def _is_duplicate_plan(
        self,
        schedule: ChargingSchedule | None,
        deficit: EnergyDeficit,
        overnight: OvernightNeed | None = None,
    ) -> bool:
        """Check if this plan is the same as the last one sent today."""
        today = dt_util.now().date()
        plan_hash = self._compute_plan_hash(schedule, deficit, overnight)

        if self._last_plan_date == today and self._last_plan_hash == plan_hash:
            return True

        self._last_plan_hash = plan_hash
        self._last_plan_date = today
        return False

    async def async_notify_plan(
        self,
        schedule: ChargingSchedule | None,
        deficit: EnergyDeficit,
        overnight: OvernightNeed | None = None,
    ) -> None:
        """Send planning notification (3 variants: scheduled, not scheduled, not needed)."""
        if not self._is_enabled(CONF_NOTIFY_PLANNING, DEFAULT_NOTIFY_PLANNING):
            return

        # Suppress plan notifications during overnight hours (22:00-06:00).
        # The plan is decided in the evening; re-notifying every hour overnight
        # when the price sensor updates is spam.
        hour = dt_util.now().hour
        if hour >= 22 or hour < 6:
            _LOGGER.debug("Skipping plan notification during overnight hours (%02d:00)", hour)
            return

        if self._is_duplicate_plan(schedule, deficit, overnight):
            _LOGGER.debug("Skipping duplicate plan notification")
            return

        currency = self._coordinator.currency
        soc = self._coordinator.current_soc

        # Overnight context line (appended when overnight triggers charging)
        overnight_info = ""
        if overnight and overnight.charge_needed > deficit.charge_needed:
            overnight_info = (
                f"\n\n🌙 Overnight survival: {overnight.charge_needed:.1f} kWh needed\n"
                f"Dark hours: {overnight.dark_hours:.0f}h, "
                f"Battery at 22:00: ~{overnight.battery_at_window_start:.1f} kWh"
            )

        if deficit.charge_needed <= 0 and (overnight is None or overnight.charge_needed <= 0):
            # No charging needed — either solar covers it or battery + solar do
            title = "☀️ No Charging Needed"
            if deficit.solar_adjusted >= deficit.consumption:
                reason = "Solar forecast covers tomorrow's consumption."
            else:
                reason = "Battery charge + solar cover tomorrow's consumption."
            message = (
                f"{reason}\n\n"
                f"SOC: {soc:.0f}%\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
            )
        elif schedule is not None:
            # Charging scheduled
            title = "🔋 Charging Scheduled"
            message = (
                f"Window: {schedule.start_hour:02d}:00–{schedule.end_hour:02d}:00 "
                f"({schedule.window_hours}h)\n"
                f"Charge: {schedule.required_kwh:.1f} kWh → {schedule.target_soc:.0f}%\n"
                f"Avg price: {schedule.avg_price:.2f} {currency}\n\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
                f"{overnight_info}"
            )
        else:
            # Deficit exists but no schedule (price too high or no prices)
            effective = max(deficit.charge_needed, overnight.charge_needed) if overnight else deficit.charge_needed
            title = "⏸️ Charging Not Scheduled"
            max_price = self._coordinator.max_charge_price
            message = (
                f"Charging needed ({effective:.1f} kWh) but not scheduled.\n"
                f"SOC: {soc:.0f}%\n"
                f"Price threshold: {max_price:.2f} {currency}\n\n"
                f"Solar (raw): {deficit.solar_raw:.1f} kWh\n"
                f"Solar (adjusted): {deficit.solar_adjusted:.1f} kWh\n"
                f"Consumption: {deficit.consumption:.1f} kWh"
                f"{overnight_info}"
            )

        await self._async_send(title, message)

    async def async_notify_charging_started(
        self,
        current_soc: float,
        target_soc: float,
        required_kwh: float,
    ) -> None:
        """Send notification when charging starts."""
        if not self._is_enabled(
            CONF_NOTIFY_CHARGING_START, DEFAULT_NOTIFY_CHARGING_START
        ):
            return

        now = dt_util.now().strftime("%H:%M")
        title = "🔋 Charging Started"
        message = (
            f"Time: {now}\n"
            f"SOC: {current_soc:.0f}% → {target_soc:.0f}%\n"
            f"Charge needed: {required_kwh:.1f} kWh"
        )
        await self._async_send(title, message)

    async def async_notify_charging_complete(
        self,
        session: ChargingSession,
        target_soc: float,
    ) -> None:
        """Send notification when charging completes."""
        if not self._is_enabled(
            CONF_NOTIFY_CHARGING_COMPLETE, DEFAULT_NOTIFY_CHARGING_COMPLETE
        ):
            return

        title = "✅ Charging Complete"

        # Calculate duration
        duration_str = ""
        if session.start_time and session.end_time:
            start_display = (
                session.start_time[11:16]
                if len(session.start_time) > 15
                else session.start_time
            )
            end_display = (
                session.end_time[11:16]
                if len(session.end_time) > 15
                else session.end_time
            )
            duration_str = f"Duration: {start_display}–{end_display}\n"

        message = (
            f"Reason: {session.result}\n"
            f"SOC: {session.start_soc:.0f}% → {session.end_soc:.0f}%\n"
            f"{duration_str}"
            f"Target was: {target_soc:.0f}%"
        )
        await self._async_send(title, message)

    async def async_notify_morning_safety(self, soc: float) -> None:
        """Send notification when morning safety stops charging."""
        if not self._is_enabled(
            CONF_NOTIFY_MORNING_SAFETY, DEFAULT_NOTIFY_MORNING_SAFETY
        ):
            return

        title = "🌅 Morning: Charging Stopped"
        message = (
            f"Morning safety triggered.\n"
            f"SOC: {soc:.0f}%\n"
            f"Mode restored to Self Use."
        )
        await self._async_send(title, message)

    async def async_notify_charging_stalled(
        self,
        soc: float,
        target_soc: float,
        minutes_stalled: int,
    ) -> None:
        """Send notification when charging appears stalled."""
        if not self._is_enabled(
            CONF_NOTIFY_CHARGING_STALLED, DEFAULT_NOTIFY_CHARGING_STALLED
        ):
            return

        title = "⚠️ Charging Stalled"
        message = (
            f"Charging stalled for {minutes_stalled} minutes.\n"
            f"SOC: {soc:.0f}% (target: {target_soc:.0f}%)\n"
            f"Charging aborted. Check inverter."
        )
        await self._async_send(title, message)

    async def async_notify_sensor_unavailable(
        self,
        sensor_name: str,
        entity_id: str,
    ) -> None:
        """Send notification when a critical sensor is unavailable (H1)."""
        if not self._is_enabled(
            CONF_NOTIFY_SENSOR_UNAVAILABLE, DEFAULT_NOTIFY_SENSOR_UNAVAILABLE
        ):
            return

        title = "⚠️ Sensor Unavailable"
        message = (
            f"{sensor_name} sensor is unavailable.\n"
            f"Entity: {entity_id}\n"
            f"Charging decisions may be incorrect until sensor recovers."
        )
        await self._async_send(title, message)

    async def async_notify_battery_full(
        self, soc: float, grid_export: float
    ) -> None:
        """Send notification when battery is full and exporting to grid."""
        if not self._is_enabled(
            CONF_NOTIFY_BATTERY_FULL, DEFAULT_NOTIFY_BATTERY_FULL
        ):
            return

        title = "🔋 Battery Full — Exporting to Grid"
        message = (
            f"Battery SOC: {soc:.0f}%\n"
            f"Grid export: {grid_export:.1f} kW"
        )
        await self._async_send(title, message)

    async def async_notify_battery_low(self, soc: float, min_soc: float) -> None:
        """Send notification when battery reaches min SOC."""
        if not self._is_enabled(
            CONF_NOTIFY_BATTERY_LOW, DEFAULT_NOTIFY_BATTERY_LOW
        ):
            return

        title = "🪫 Battery at Minimum SOC"
        message = (
            f"Battery SOC: {soc:.0f}%\n"
            f"Min SOC threshold: {min_soc:.0f}%"
        )
        await self._async_send(title, message)

    async def async_notify_surplus_load(
        self,
        load_name: str,
        turned_on: bool,
        surplus_kw: float,
        soc: float,
    ) -> None:
        """Send notification when a surplus load is switched."""
        if not self._is_enabled(
            CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
        ):
            return

        action = "ON" if turned_on else "OFF"
        emoji = "☀️" if turned_on else "🔌"
        title = f"{emoji} Surplus: {load_name} {action}"
        message = (
            f"Surplus: {surplus_kw:.1f} kW\n"
            f"Battery SOC: {soc:.0f}%"
        )
        await self._async_send(title, message)

    async def async_notify_predictive_evaluation(
        self,
        load_name: str,
        evaluation: PredictiveEvaluation,
    ) -> None:
        """Send notification when a predictive load is evaluated."""
        if not self._is_enabled(
            CONF_NOTIFY_SURPLUS_LOAD, DEFAULT_NOTIFY_SURPLUS_LOAD
        ):
            return

        emoji = "✅" if evaluation.approved else "❌"
        status = "Approved" if evaluation.approved else "Denied"
        title = f"{emoji} Predictive: {load_name} {status}"
        message = (
            f"Load needs: {evaluation.load_needs_kwh:.1f} kWh\n"
            f"Surplus budget: {evaluation.surplus_budget_kwh:.1f} kWh\n"
            f"Reactive claim: {evaluation.reactive_claim_kwh:.1f} kWh\n"
            f"Min SOC after: {evaluation.min_soc_after:.0f}%\n"
            f"{evaluation.reason}"
        )
        await self._async_send(title, message)
