"""Constants for the Smart Battery Charging integration."""

DOMAIN = "smart_energy_manager"

# Config flow steps
CONF_INVERTER_TEMPLATE = "inverter_template"
CONF_INVERTER_SOC_SENSOR = "inverter_soc_sensor"
CONF_INVERTER_CAPACITY_SENSOR = "inverter_capacity_sensor"
CONF_INVERTER_ACTUAL_SOLAR_SENSOR = "inverter_actual_solar_sensor"
CONF_INVERTER_MODE_SELECT = "inverter_mode_select"
CONF_INVERTER_CHARGE_COMMAND_SELECT = "inverter_charge_command_select"
CONF_INVERTER_CHARGE_SOC_LIMIT = "inverter_charge_soc_limit"
CONF_INVERTER_DISCHARGE_MIN_SOC = "inverter_discharge_min_soc"

# Inverter option strings
CONF_MODE_SELF_USE = "mode_self_use"
CONF_MODE_MANUAL = "mode_manual"
CONF_CHARGE_FORCE = "charge_force"
CONF_CHARGE_STOP = "charge_stop"

# Price sensor
CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_ATTRIBUTE_FORMAT = "price_attribute_format"

# Solar forecast
CONF_SOLAR_FORECAST_TODAY = "solar_forecast_today"
CONF_SOLAR_FORECAST_TOMORROW = "solar_forecast_tomorrow"

# Consumption
CONF_CONSUMPTION_SENSOR = "consumption_sensor"

# Analytics sensors (optional)
CONF_GRID_IMPORT_SENSOR = "grid_import_sensor"
CONF_GRID_EXPORT_SENSOR = "grid_export_sensor"
CONF_DAILY_SOLAR_SENSOR = "daily_solar_sensor"

# Settings (also exposed as number entities)
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_MAX_CHARGE_LEVEL = "max_charge_level"
CONF_MIN_SOC = "min_soc"
CONF_MAX_CHARGE_POWER = "max_charge_power"
CONF_MAX_CHARGE_PRICE = "max_charge_price"
CONF_FALLBACK_CONSUMPTION = "fallback_consumption"
CONF_WINDOW_START_HOUR = "window_start_hour"
CONF_WINDOW_END_HOUR = "window_end_hour"
CONF_CURRENCY = "currency"

# Charging efficiency (Fix 1)
CONF_CHARGING_EFFICIENCY = "charging_efficiency"
DEFAULT_CHARGING_EFFICIENCY = 0.90

# Consumption profiles (Fix 5/6)
CONF_EVENING_CONSUMPTION_MULTIPLIER = "evening_consumption_multiplier"
CONF_NIGHT_CONSUMPTION_MULTIPLIER = "night_consumption_multiplier"
CONF_WEEKEND_CONSUMPTION_MULTIPLIER = "weekend_consumption_multiplier"
DEFAULT_EVENING_CONSUMPTION_MULTIPLIER = 1.5
DEFAULT_NIGHT_CONSUMPTION_MULTIPLIER = 0.5
DEFAULT_WEEKEND_CONSUMPTION_MULTIPLIER = 1.0

# Defaults
DEFAULT_INVERTER_TEMPLATE = "custom"
DEFAULT_BATTERY_CAPACITY = 10.0
DEFAULT_MAX_CHARGE_LEVEL = 90.0
DEFAULT_MIN_SOC = 20.0
DEFAULT_MAX_CHARGE_POWER = 5.0
DEFAULT_MAX_CHARGE_PRICE = 0.10
DEFAULT_FALLBACK_CONSUMPTION = 20.0
DEFAULT_WINDOW_START_HOUR = 22
DEFAULT_WINDOW_END_HOUR = 6
DEFAULT_CURRENCY = "EUR/kWh"
DEFAULT_PRICE_ATTRIBUTE_FORMAT = "iso_datetime"

# Price attribute formats
PRICE_FORMAT_ISO_DATETIME = "iso_datetime"
PRICE_FORMAT_HOUR_INT = "hour_int"

# Coordinator
UPDATE_INTERVAL_SECONDS = 30

# Consumption tracker
CONSUMPTION_WINDOW_DAYS = 7

# Forecast corrector
FORECAST_ERROR_WINDOW_DAYS = 7

# Charge history
CHARGE_HISTORY_DAYS = 7

# Analytics history retention
MORNING_SOC_HISTORY_DAYS = 30
SESSION_COST_HISTORY_DAYS = 90
BMS_CAPACITY_HISTORY_DAYS = 365

# Stall detection (Fix 3)
STALL_RETRY_TICKS = 8   # 16 min at 2-min ticks — retry charge command
STALL_ABORT_TICKS = 16  # 32 min — abort charging and alert

# Planner magic numbers (Fix 12)
PV_FALLBACK_BUFFER_HOURS = 3.0   # Hours after window_end when no sun data available
MORNING_SAFETY_OFFSET_MINUTES = 15  # Minutes before sunrise for morning safety

# Wattsonic / EMS power control entities
CONF_INVERTER_WORKING_MODE_NUMBER = "inverter_working_mode_number"
CONF_INVERTER_BATTERY_POWER_NUMBER = "inverter_battery_power_number"
CONF_INVERTER_AC_LOWER_LIMIT_NUMBER = "inverter_ac_lower_limit_number"
CONF_INVERTER_BATTERY_DOD_NUMBER = "inverter_battery_dod_number"

# EMS mode values (written as raw integers to working mode register)
CONF_EMS_CHARGE_MODE_VALUE = "ems_charge_mode_value"
CONF_EMS_NORMAL_MODE_VALUE = "ems_normal_mode_value"

# Control type
CONF_CONTROL_TYPE = "control_type"
CONTROL_TYPE_SELECT = "select"
CONTROL_TYPE_EMS_POWER = "ems_power"

# Emergency SOC threshold — below this, price threshold is bypassed (M2)
EMERGENCY_SOC_THRESHOLD = 25.0

# Modbus call timeout in seconds (C2)
MODBUS_CALL_TIMEOUT = 30

# Start failure retry limit (C3)
START_FAILURE_MAX_RETRIES = 3

# Sensor health monitoring (H1)
CONF_NOTIFY_SENSOR_UNAVAILABLE = "notify_sensor_unavailable"
DEFAULT_NOTIFY_SENSOR_UNAVAILABLE = True
SENSOR_UNAVAILABLE_TICKS = 5  # ~2.5 min at 30s update interval

# Notifications
CONF_NOTIFICATION_SERVICE = "notification_service"
CONF_NOTIFY_PLANNING = "notify_planning"
CONF_NOTIFY_CHARGING_START = "notify_charging_start"
CONF_NOTIFY_CHARGING_COMPLETE = "notify_charging_complete"
CONF_NOTIFY_MORNING_SAFETY = "notify_morning_safety"
CONF_NOTIFY_CHARGING_STALLED = "notify_charging_stalled"
CONF_NOTIFY_BATTERY_FULL = "notify_battery_full"
CONF_NOTIFY_BATTERY_LOW = "notify_battery_low"

DEFAULT_NOTIFICATION_SERVICE = ""
DEFAULT_NOTIFY_PLANNING = True
DEFAULT_NOTIFY_CHARGING_START = True
DEFAULT_NOTIFY_CHARGING_COMPLETE = True
DEFAULT_NOTIFY_MORNING_SAFETY = True
DEFAULT_NOTIFY_CHARGING_STALLED = True
DEFAULT_NOTIFY_BATTERY_FULL = True
DEFAULT_NOTIFY_BATTERY_LOW = True

# Surplus load controller
CONF_GRID_EXPORT_POWER_SENSOR = "grid_export_power_sensor"
CONF_SURPLUS_LOADS = "surplus_loads"
CONF_NOTIFY_SURPLUS_LOAD = "notify_surplus_load"
DEFAULT_NOTIFY_SURPLUS_LOAD = True

DEFAULT_SURPLUS_BATTERY_ON = 98.0
DEFAULT_SURPLUS_BATTERY_OFF = 95.0
DEFAULT_SURPLUS_MARGIN_ON = 0.3
DEFAULT_SURPLUS_MARGIN_OFF = 0.5
DEFAULT_SURPLUS_MIN_SWITCH_INTERVAL = 300

SURPLUS_RUNTIME_HISTORY_DAYS = 30

# Predictive surplus load defaults
SURPLUS_MODE_REACTIVE = "reactive"
SURPLUS_MODE_PREDICTIVE = "predictive"
DEFAULT_PREDICTIVE_SCHEDULE_START = 5
DEFAULT_PREDICTIVE_SCHEDULE_END = 8
DEFAULT_PREDICTIVE_LEAD_MINUTES = 30
CONF_OUTDOOR_TEMP_SENSOR = "outdoor_temp_sensor"
DEFAULT_MAX_OUTDOOR_TEMP = 0.0  # 0 = disabled

# Platforms
PLATFORMS = ["sensor", "binary_sensor", "number", "switch"]
