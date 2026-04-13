<img src="custom_components/smart_energy_manager/icon@2x.png" alt="Smart Energy Manager" width="64" height="64" align="left" style="margin-right: 16px;">

# Smart Energy Manager

A Home Assistant custom integration for smart energy management of solar+battery systems. Automated night charging during cheapest electricity hours and surplus load management that activates loads when solar production exceeds consumption.

**Inverter-agnostic** — supports Solax, GoodWe, SolarEdge, Huawei, Wattsonic GEN2, or any inverter with mode select entities in Home Assistant.

---

## Features

### Night Charging
- Dynamic charging window calculation (1-6 hours based on energy deficit)
- SOC trajectory simulation — hour-by-hour forward simulation for precise charge decisions
- Cheapest price window selection within configurable night hours
- Solar forecast integration with error correction (7-day sliding window)
- Baseline consumption tracking — automatically subtracts surplus load energy from average to prevent feedback loops
- Consumption tracking with sliding window average (7-day, 3-period day model)
- Price threshold with emergency low-battery override (SOC < 25%)
- Charging state persists across HA restarts
- Modbus call timeout protection (30s) with stall detection

### Surplus Load Management
- Generic, priority-based controller for loads consuming solar surplus
- Two modes: **reactive** (surplus-triggered) and **predictive** (scheduled with forecast evaluation)
- True surplus calculation: `grid_export + sum(running_load.power_kw)`
- Per-load SOC thresholds, power margins, anti-flap protection
- Outdoor temperature gating — skip loads when it's warm enough outside
- Predictive evaluation checks impact on reactive loads (won't starve lower-priority loads)
- Surplus forecasting (today + tomorrow after sunset) with visual bars on dashboard
- Runtime tracking, energy metering, utilization efficiency

### Dashboard
- Included Lovelace dashboard with 4 views: Scheduled Charging, Data, Analytics, Surplus
- [gauge-card-pro](https://github.com/benjamin-dcs/gauge-card-pro) gauges with rounded ends, gradients, setpoint needles, and min/max indicators
- Dual battery gauge: SOC (outer) + usable charge in kWh (inner)
- Electricity price gauge: current price needle, max charge price setpoint, cheapest/most expensive today markers
- Surplus forecast: visual bars showing solar forecast, baseline consumption, and expected surplus
- ApexCharts for price history, power draw, and energy consumption

### General
- Multi-step config flow with inverter templates for easy setup
- All settings exposed as number entities (controllable from dashboard/automations)
- Configurable notifications for all events
- JSON-based persistence
- 285 unit tests, pure logic modules with zero HA dependencies

## Installation

### HACS (Recommended)

1. Add this repository to HACS as a custom repository
2. Search for "Smart Energy Manager" and install
3. Restart Home Assistant
4. Go to Settings > Devices & Services > Add Integration > Smart Energy Manager

### Manual

1. Copy `custom_components/smart_energy_manager/` to your HA `custom_components/` directory
2. Restart Home Assistant
3. Add the integration via Settings > Devices & Services

## Configuration

The integration uses a multi-step config flow:

1. **Name** — Instance name
2. **Inverter Template** — Pick your inverter (Solax, GoodWe, SolarEdge, Huawei, Wattsonic, or Custom)
3. **Inverter Entities** — SOC sensor, capacity sensor, mode/charge entities
4. **Inverter Values** — Mode option strings (pre-filled from template)
5. **Price Sensor** — Spot electricity price sensor with hourly attributes
6. **Solar Forecast** — Today/tomorrow forecast sensors (multiple orientations supported)
7. **Consumption** — Daily consumption sensor (resets at midnight)
8. **Analytics** — Optional grid import/export and solar production sensors
9. **Settings** — Battery capacity, SOC limits, charge power, price threshold, etc.

### Surplus Load Management

Configure via Settings > Devices > Smart Energy Manager > Configure > Surplus Load Management:

- **Add/Edit/Remove loads** through the UI (no YAML editing)
- **Reactive mode**: turns on when grid export exceeds margin, SOC above threshold
- **Predictive mode**: runs on daily schedule, pre-evaluated against solar forecast
- **Per-load settings**: power draw (kW the load consumes — used for surplus allocation, not a cutoff limit), priority, SOC thresholds, margins, switch interval, max outdoor temp

### Dashboard

A template dashboard is included in `dashboards/dashboard_template.yaml` with 3 views: Scheduled Charging, Analytics, and Surplus.

**Required custom cards** (install via HACS):
- [apexcharts-card](https://github.com/RomRider/apexcharts-card) — all charts and graphs
- [gauge-card-pro](https://github.com/benjamin-dcs/gauge-card-pro) — battery and price gauges

**Setup:**

1. Install the required custom cards via HACS
2. Copy `dashboards/dashboard_template.yaml` content
3. In HA, go to Settings > Dashboards > Add Dashboard > select "sections" view
4. Open the dashboard, click the three-dot menu > Raw Configuration Editor
5. Paste the template YAML
6. Search and replace these placeholders with your entity IDs:

| Placeholder | Description | Example |
|-------------|-------------|---------|
| `YOUR_BATTERY_SOC_SENSOR` | Battery state of charge (%) | `sensor.solax_inverter_battery_capacity` |
| `YOUR_PRICE_SENSOR` | Spot electricity price with hourly attributes | `sensor.current_spot_electricity_price` |
| `YOUR_ACTUAL_SOLAR_SENSOR` | Today's actual solar production (resets at midnight) | `sensor.solax_inverter_today_s_solar_energy` |
| `YOUR_CONSUMPTION_SENSOR` | Daily home consumption (resets at midnight) | `sensor.home_consumption_energy` |
| `YOUR_GRID_EXPORT_POWER_SENSOR` | Real-time grid export power (W) | `sensor.solax_inverter_grid_export` |
| `YOUR_PV_POWER_SENSOR` | Real-time PV production power (W) | `sensor.solax_inverter_pv_power_total` |

**Optional placeholders** (remove the card if you don't have these):

| Placeholder | Description |
|-------------|-------------|
| `YOUR_CHEAPEST_PRICE_SENSOR` | Today's cheapest electricity price (gauge min indicator) |
| `YOUR_EXPENSIVE_PRICE_SENSOR` | Today's most expensive electricity price (gauge max indicator) |

**Adjustments you may want to make:**
- PV/Export gauge: adjust outer `max: 10000` to your peak PV power (W) and inner `max: 4500` to your grid export limit (W)
- Battery gauge inner max: change `15` to your battery capacity in kWh
- Price gauge range: adjust `min: -1` / `max: 8` for your electricity pricing
- Price threshold annotation: adjust `y: 4` to your max charge price
- Min SOC annotation: adjust `y: 20` to your configured min SOC
- Live Power Draw chart: uncomment and add one series per surplus load with its power sensor
- Solar vs Export chart: uncomment and add your daily solar/export energy sensors

## Entities Created

### Sensors
| Entity | Description |
|--------|-------------|
| Average Daily Consumption | 7-day sliding window average |
| Today/Tomorrow Solar Forecast | Combined forecast (all orientations) |
| Solar Forecast Error Average | 7-day error tracking (%) |
| Today Solar Forecast Error | Live forecast vs actual |
| Tomorrow Energy Forecast | Adjusted solar minus consumption |
| Battery Charge kWh | Current charge in kWh |
| Battery Usable Charge | Charge above minimum SOC |
| Battery Capacity to Max | Remaining to configured max |
| Night Charging Status | Idle/Scheduled/Charging/Complete/Disabled |
| Last Night Charge kWh | SOC delta converted to kWh |
| Last Charge Battery/Time Range | Start→End SOC and time |
| Last Charge Total Cost | kWh × avg price |
| Electricity Price Status | Very Cheap/Cheap/Normal/Expensive |
| Today/Tomorrow Cheapest Hours | Top 3 cheapest hours |
| Self Consumption / Grid Dependency | Daily ratios |
| Morning SOC | Battery level at sunrise |
| Surplus Forecast | Today/tomorrow surplus kWh with hourly breakdown |
| Surplus Load Status | Active loads, power, runtime, utilization |
| BMS Battery Capacity | Tracked from inverter |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Charging Active | Currently force-charging |
| Charging Recommended | Price below threshold and SOC below max |

### Number Entities
| Entity | Description |
|--------|-------------|
| Max Charge Level | % |
| Min SOC | % |
| Max Charge Power | kW |
| Max Charge Price | Currency/kWh |
| Fallback Consumption | kWh (used when no history) |

### Switch
| Entity | Description |
|--------|-------------|
| Enabled | Master on/off |

## Example Use Cases

Real-world examples from a Czech household with a Solax G4 inverter, 15 kWh battery, and 8 kWp dual-orientation solar panels.

### Water Heater (Reactive)

A dual-tank water heater (2.2 kW) controlled by a Shelly smart relay. Runs only when the battery is nearly full and solar is being exported to the grid.

**Setup:**
- **Switch entity**: `switch.water_heater` (Shelly relay)
- **Power sensor**: `sensor.water_heater_power` (Shelly energy meter)
- **Mode**: Reactive
- **Power**: 2.2 kW
- **Priority**: 1 (highest — gets surplus first)
- **SOC ON threshold**: 90% (battery must be nearly full)
- **SOC OFF threshold**: 85% (hysteresis — don't flap on small SOC drops)
- **Margin ON**: 0.3 kW (turn on when grid export exceeds 0.3 kW)
- **Margin OFF**: -0.2 kW (turn off when importing more than 0.2 kW)
- **Min switch interval**: 1800s (avoid relay wear on cloudy days)

**Why it works:** The water heater is a perfect surplus load — it's a thermal battery. The Shelly relay has a built-in power meter, so the integration tracks actual energy consumed. With a 90% SOC threshold, it only runs when the battery is basically full and surplus would otherwise be exported.

### Floor Heating Upstairs (Predictive)

Electric underfloor heating (0.5 kW) via a Devireg thermostat. Runs on a morning schedule — the integration checks the solar forecast to ensure the battery will recover.

**Setup:**
- **Switch entity**: `switch.floor_heating_surplus` (HA template switch, see below)
- **Power sensor**: `sensor.power_meter_floor_heating_upstairs_power_2`
- **Mode**: Predictive
- **Power**: 0.5 kW
- **Priority**: 3
- **Schedule**: 05:00–08:00
- **Evaluation lead**: 30 minutes (checks forecast at 04:30)
- **Max outdoor temp**: 24°C (skip in summer)

**Template switch** (needed because the thermostat doesn't have a simple on/off — we set the target temperature):

```yaml
# packages/floor_heating_surplus.yaml
template:
  - switch:
      - name: "Floor Heating (Surplus)"
        state: "{{ states('sensor.power_meter_floor_heating_upstairs_power_2') | float(0) > 10 }}"
        turn_on:
          - action: script.turn_on
            target:
              entity_id: script.heat_upstairs_floor
        turn_off:
          - action: script.turn_on
            target:
              entity_id: script.heat_upstairs_floor_off
```

**Scripts:**
```yaml
heat_upstairs_floor:
  sequence:
    - action: climate.set_temperature
      target:
        entity_id: climate.devireg_upstairs_thermostat
      data:
        temperature: 28
        hvac_mode: heat
  mode: restart

heat_upstairs_floor_off:
  sequence:
    - action: climate.set_temperature
      target:
        entity_id: climate.devireg_upstairs_thermostat
      data:
        temperature: 15
        hvac_mode: "off"
  mode: restart
```

**Why predictive:** Floor heating draws from the battery during early morning (no solar). The integration simulates the day ahead — if the solar forecast shows enough production to recharge the battery AND still leave surplus for the water heater (reactive loads), it approves the schedule. If the forecast is poor, it skips the day.

### Floor Heating Downstairs (Reactive)

A second floor heating zone (0.63 kW) via another Devireg thermostat, monitored by a Shelly 3EM energy meter.

**Setup:**
- **Switch entity**: `switch.floor_heating_downstairs_surplus` (template switch, same pattern)
- **Power sensor**: `sensor.shelly3em63g3_b08184e0d6c0_energy_meter_2_power`
- **Mode**: Reactive
- **Power**: 0.63 kW
- **Priority**: 5 (runs after water heater gets surplus)
- **SOC ON threshold**: 90%
- **SOC OFF threshold**: 85%
- **Max outdoor temp**: 24°C (skip in summer)

**Why reactive (not predictive):** This zone heats during the day when there's solar surplus available. Lower priority than the water heater ensures hot water comes first.

### Tips for Adding Your Own Loads

1. **Simple on/off loads** (smart plug, relay): use the switch entity directly
2. **Thermostat-controlled loads**: create a template switch that sets temperature on turn_on and turns off on turn_off (see floor heating example)
3. **Mode-switching loads** (e.g. boiler eco → boost): create a template switch where `turn_on` activates the desired mode and `turn_off` returns to default. **Important**: the switch's `state` must report `on` when the load is actively consuming surplus, and `off` otherwise. The integration monitors the switch state every 2 minutes — if the state doesn't match what the integration expects, it will cycle on/off. Do NOT use an inverted switch
4. **Power draw (kW)**: this tells the integration how much power the load consumes. It is NOT a cutoff limit — it's used to calculate how much surplus remains for lower-priority loads and to determine when to turn off (if remaining surplus drops below the load's consumption minus the off margin)
5. **Set priorities carefully**: lower number = higher priority. Water heater at 1 means it always gets surplus first
6. **SOC thresholds**: use 90%+ for reactive loads (ensures battery is full before diverting). Solax inverters start exporting around 97-98% SOC
7. **Anti-flap interval**: increase to 1800s+ on cloudy days to reduce relay switching. The default 300s can cause 10+ cycles/day with variable clouds
8. **Outdoor temp gating**: set `max_outdoor_temp` for seasonal loads (heating) to avoid wasting energy in summer
9. **Power sensors**: always configure if available — the integration uses actual power for energy tracking instead of the configured maximum. Power sensor is optional — you can leave it blank or clear it when editing a load

### Troubleshooting: Why Isn't My Load Turning On?

Each surplus load reports a **reason** explaining why it is currently on or off. To check:

1. Go to **Developer Tools → States**
2. Search for `sensor.smart_energy_manager_surplus_load_status`
3. Expand **Attributes** → find `load_details`
4. Find your load by name — the `reason` field explains the current state

Common reasons:

| Reason | Meaning |
|--------|---------|
| `Waiting: SOC 85% < 90%` | Battery not full enough — lower the SOC ON threshold or wait |
| `Waiting: surplus 0.15 kW < 0.30 kW margin` | Not enough grid export yet — lower margin_on or wait for more solar |
| `Blocked: outdoor temp too high` | Outdoor temperature exceeds the load's max_outdoor_temp setting |
| `ON: surplus 2.50 kW, SOC 100%` | Load is running — conditions are met |
| `OFF: Surplus 0.10 kW too low` | Surplus dropped, load was turned off |
| `Running — surplus OK` | Load is running and conditions are still good |
| `Negative price — forced ON` | Spot price is ≤ 0, load forced on to absorb energy |

**Still not working?** Check:
- Is the **switch entity** correct? The integration calls `switch.turn_on` / `switch.turn_off` on the configured entity
- Does the switch **report state correctly**? After `turn_on`, the switch must show `on` in HA. If it stays `off`, the integration thinks it failed and won't track it
- Is the load's **auto mode** enabled? Check `switch.<load>_automatic_watering` or equivalent
- Check HA logs: **Settings → System → Logs**, filter for `smart_energy_manager`

## Architecture

```
coordinator.py            ← DataUpdateCoordinator (30s refresh)
├── planner.py            ← SOC trajectory simulation, charging decisions
├── charging_controller.py ← Inverter control state machine
├── surplus_controller.py  ← Multi-load surplus management
├── price_analyzer.py     ← Price extraction, cheapest window
├── forecast_corrector.py ← 7-day forecast error tracking
├── consumption_tracker.py ← 7-day consumption average
├── inverters/            ← Inverter abstraction (select, EMS)
├── notifier.py           ← HA notification service
├── storage.py            ← JSON persistence via HA Store
└── config_flow.py        ← Multi-step setup + surplus load menu
```

## Development

```bash
# Run tests (no HA installation needed)
cd smart-energy-manager
python3 -m pytest tests/ -v
```

## License

MIT
