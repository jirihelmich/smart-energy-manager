"""Data models for the Smart Battery Charging integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class InverterTemplate:
    """Template for a known inverter integration."""

    id: str
    label: str
    description: str
    control_type: str = "select"  # "select" or "ems_power"
    mode_self_use: str = ""
    mode_manual: str = ""
    charge_force: str = ""
    charge_stop: str = ""
    battery_capacity: float = 15.0
    entity_hints: dict[str, str] = field(default_factory=dict)
    # EMS power control fields (for control_type="ems_power")
    ems_charge_mode_value: int = 0
    ems_normal_mode_value: int = 0


class ChargingState(Enum):
    """State machine states for the charging controller."""

    IDLE = "idle"
    SCHEDULED = "scheduled"
    CHARGING = "charging"
    COMPLETE = "complete"
    DISABLED = "disabled"


@dataclass
class ChargingSchedule:
    """A planned charging session."""

    start_hour: int
    end_hour: int
    window_hours: int
    avg_price: float
    required_kwh: float
    target_soc: float
    created_at: datetime | None = None


@dataclass
class ChargingSession:
    """Record of a completed (or in-progress) charging session."""

    start_soc: float = 0.0
    end_soc: float = 0.0
    start_time: str = ""
    end_time: str = ""
    avg_price: float = 0.0
    result: str = ""

    def kwh_charged(self, battery_capacity_kwh: float) -> float:
        """Calculate kWh charged from SOC delta."""
        if self.end_soc > self.start_soc:
            return round((self.end_soc - self.start_soc) / 100 * battery_capacity_kwh, 2)
        return 0.0

    def total_cost(self, battery_capacity_kwh: float) -> float:
        """Calculate total cost of the charging session."""
        return round(self.kwh_charged(battery_capacity_kwh) * self.avg_price, 1)


@dataclass
class EnergyDeficit:
    """Result of the energy deficit calculation."""

    consumption: float
    solar_raw: float
    solar_adjusted: float
    forecast_error_pct: float
    deficit: float
    charge_needed: float
    usable_capacity: float


@dataclass
class OvernightNeed:
    """Result of the overnight survival calculation.

    Determines whether the battery can bridge the gap from window_start
    (e.g. 22:00) until solar production meaningfully covers consumption.
    """

    dark_hours: float  # Hours from window_start to solar coverage
    overnight_consumption: float  # kWh consumed during dark hours
    battery_at_window_start: float  # Estimated usable kWh at window start
    charge_needed: float  # max(0, overnight_consumption - battery), clamped
    solar_start_hour: float  # Hour when PV covers consumption
    source: str  # "forecast_solar" or "sun_entity" or "fallback"


@dataclass
class TrajectoryResult:
    """Result of the hour-by-hour SOC trajectory simulation."""

    charge_needed_kwh: float  # net deficit below min_soc (after efficiency)
    min_soc_kwh: float  # deepest SOC reached in simulation
    min_soc_hour: int  # clock hour when deepest point occurs

    # Backward-compat: daily energy balance (for EnergyDeficit sensor)
    daily_deficit_kwh: float  # consumption_tomorrow - solar_adjusted_tomorrow (>= 0)
    daily_charge_kwh: float  # daily_deficit clamped to usable capacity / efficiency

    # Backward-compat: overnight data (for OvernightNeed / sensor attributes)
    battery_at_window_start_kwh: float  # projected SOC at window_start
    dark_hours: float  # hours from window_start to solar coverage
    overnight_consumption_kwh: float  # drain from window_start to solar start
    solar_start_hour: float
    solar_source: str

    # Summary data (for sensor display)
    tomorrow_consumption: float
    tomorrow_solar_raw: float
    tomorrow_solar_adjusted: float
    forecast_error_pct: float
    usable_capacity_kwh: float


@dataclass(frozen=True)
class SurplusForecast:
    """Predicted solar surplus for today."""

    total_kwh: float  # Total surplus kWh expected today
    hourly_kwh: dict[int, float]  # hour -> surplus kWh (only hours with surplus)
    battery_full_hour: int | None  # First hour battery hits max (None if never)
    peak_surplus_kw: float  # Maximum hourly surplus
    surplus_hours: int  # Number of hours with surplus > 0


@dataclass(frozen=True)
class SurplusLoadConfig:
    """Configuration for a single surplus load."""

    name: str
    switch_entity: str
    power_kw: float
    priority: int = 1  # Lower = higher priority (turned on first, off last)
    battery_on_threshold: float = 98.0  # SOC % to enable
    battery_off_threshold: float = 95.0  # SOC % to disable
    margin_on_kw: float = 0.3  # Extra surplus above power_kw to turn ON
    margin_off_kw: float = 0.5  # Deficit below 0 to turn OFF
    min_switch_interval: int = 300  # Seconds between switches (anti-flap)
    # Predictive mode fields
    mode: str = "reactive"  # "reactive" or "predictive"
    schedule_start_hour: int = 5  # Hour to start the load (predictive only)
    schedule_end_hour: int = 8  # Hour to stop the load (predictive only)
    evaluation_lead_minutes: int = 30  # Minutes before schedule to evaluate


@dataclass
class SurplusLoadState:
    """Mutable runtime state for a single surplus load."""

    is_running: bool = False
    last_switch_time: float = 0.0  # timestamp
    daily_runtime_seconds: float = 0.0  # today's accumulated runtime
    last_tick_time: float = 0.0  # for runtime accumulation
    # Predictive mode state
    predictive_approved: bool | None = None  # None=not evaluated, True/False
    predictive_aborted: bool = False  # True if mid-run abort happened today


@dataclass(frozen=True)
class PredictiveEvaluation:
    """Result of evaluating whether a predictive load should run."""

    approved: bool
    reason: str
    surplus_budget_kwh: float  # Total surplus available for this load today
    load_needs_kwh: float  # How much the load would consume
    min_soc_after: float  # Projected min SOC % if load runs
    reactive_claim_kwh: float  # Surplus claimed by higher-priority reactive loads
