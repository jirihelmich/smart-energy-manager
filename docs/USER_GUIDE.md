# Smart Battery Charging — User Guide

A Home Assistant integration that automatically charges your battery during the cheapest overnight electricity hours, based on tomorrow's solar forecast and your actual consumption patterns.

**Works with**: Solax, GoodWe, SolarEdge, Huawei, and any inverter with mode select + charge command entities in HA.

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Installation](#installation)
3. [Setup Wizard](#setup-wizard)
4. [Daily Operation](#daily-operation)
5. [Settings Reference](#settings-reference)
6. [Entities Reference](#entities-reference)
7. [Notifications](#notifications)
8. [Dashboard](#dashboard)
9. [How the Algorithms Work](#how-the-algorithms-work)
10. [Troubleshooting](#troubleshooting)
11. [FAQ](#faq)

---

## How It Works

Every day the integration runs through this cycle:

1. **Afternoon** — Tomorrow's electricity prices arrive from your spot price sensor (typically 13:00–14:00).
2. **Planning** — The planner calculates how much energy you need overnight:
   - How much will the house consume tomorrow? (7-day average of real data)
   - How much solar will you produce? (forecast, corrected for historical error)
   - Can the battery survive tonight until solar kicks in? (hour-by-hour overnight simulation)
3. **Price check** — Finds the cheapest contiguous window within your night hours (default 22:00–06:00).
4. **Charging** — At the scheduled hour, the inverter switches to Manual Mode → Force Charge. Every 2 minutes, SOC is checked against the target.
5. **Morning** — At sunrise minus 15 minutes, the inverter is restored to Self Use Mode regardless of what happened overnight.

You get a notification at each step. If solar covers tomorrow's consumption AND the battery can bridge the night, no charging is scheduled.

---

## Installation

### Via HACS (Recommended)

1. In HACS, go to **Integrations** → three-dot menu → **Custom repositories**
2. Add the repository URL and select category **Integration**
3. Search for "Smart Battery Charging" and install
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration → Smart Battery Charging**

### Manual

1. Copy the `custom_components/smart_energy_manager/` folder into your HA `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration via **Settings → Devices & Services**

---

## Setup Wizard

The integration guides you through an 8-step config flow. Each step collects different information.

### Step 1: Name

Pick a name for this integration instance (e.g., "Smart Battery Charging"). You can run multiple instances if you have multiple inverters.

### Step 2: Inverter Template

Select your inverter brand to pre-fill mode strings and get entity hints:

| Template | Integration | Notes |
|----------|-------------|-------|
| **Solax Modbus** | wills106/homeassistant-solax-modbus | Self Use / Manual Mode / Force Charge |
| **GoodWe** | Core or mletenay | Not yet available as template — use Custom and configure entities manually |
| **SolarEdge Modbus** | binsentsu Modbus | Remote Control mode for force charge |
| **Huawei Solar** | wlcrs | Time Of Use workaround |
| **Custom / Other** | Any | Fill in all values manually |

### Step 3: Inverter Entities

Select the HA entities that correspond to your inverter's sensors and controls:

| Field | What to select | Example (Solax) |
|-------|---------------|-----------------|
| **SOC sensor** | Battery state of charge (%) | `sensor.solax_inverter_battery_capacity` |
| **Capacity sensor** | Battery capacity from BMS (Wh) | `sensor.solax_inverter_battery_capacity_charge` |
| **Actual solar sensor** | Today's actual solar production (kWh, resets daily) | `sensor.solax_inverter_today_s_solar_energy` |
| **Mode select** | Inverter operating mode | `select.solax_inverter_charger_use_mode` |
| **Charge command select** | Force charge / stop command | `select.solax_inverter_charge_discharge_setting` |
| **Charge SOC limit** | Target SOC for charging | `number.solax_inverter_charge_soc_limit` |
| **Discharge min SOC** *(optional)* | Minimum discharge level in Self Use | `number.solax_inverter_selfuse_discharge_min_soc` |

### Step 4: Inverter Mode Strings

The exact option strings your inverter uses. Pre-filled from the template, but verify they match your setup:

| Field | Solax default | What it does |
|-------|--------------|--------------|
| Self Use mode | `Self Use Mode` | Normal operation — battery charges from solar, discharges to house |
| Manual mode | `Manual Mode` | Allows force charge commands |
| Force charge | `Force Charge` | Command to start charging from grid |
| Stop charge | `Stop Charge and Discharge` | Command to stop forced charge |

### Step 5: Price Sensor

Select your electricity spot price sensor. It must have **tomorrow's hourly prices as attributes** (this is how most spot price integrations work).

Attribute format should be ISO datetime keys like:
```
2026-02-25T00:00:00+01:00: 1.23
2026-02-25T01:00:00+01:00: 0.98
```

### Step 6: Solar Forecast

Select your solar forecast sensors for today and tomorrow. Supports **multiple sensors** for dual-orientation systems (e.g., east/west roof):

- **Today** — `sensor.energy_production_today`, `sensor.energy_production_today_2`
- **Tomorrow** — `sensor.energy_production_tomorrow`, `sensor.energy_production_tomorrow_2`

The integration sums all selected sensors automatically.

### Step 7: Consumption Sensor

Select your daily house consumption sensor. This must be a sensor that **resets at midnight** and shows cumulative daily consumption in kWh.

Example: `sensor.solax_energy_dashboard_solax_home_consumption_energy`

### Step 8: Settings

| Setting | Default | Description |
|---------|---------|-------------|
| Battery capacity | 15.0 kWh | Fallback value — only used if BMS sensor is unavailable (capacity is normally read from your inverter's BMS automatically) |
| Max charge level | 90% | Don't charge above this SOC |
| Min SOC | 20% | Don't discharge below this SOC |
| Max charge power | 10.0 kW | Maximum grid charge rate |
| Max charge price | 4.0 | Don't charge if cheapest price exceeds this |
| Fallback consumption | 20.0 kWh | Used until real consumption history is available |
| Window start hour | 22 | Earliest charging can begin |
| Window end hour | 6 | Latest charging can end |
| Currency | Kč/kWh | Display unit for prices |

All of these can be changed later via the options flow or directly from the dashboard using the number entities.

---

## Daily Operation

### What Happens Automatically

Once set up, everything runs automatically:

| Time | Event | What happens |
|------|-------|-------------|
| ~13:00–14:00 | Price sensor updates | Planner runs, calculates deficit, finds cheapest window |
| 20:00 | Fallback trigger | Planner runs again (in case price update was missed) |
| 22:00–06:00 | Charging window | If scheduled, inverter switches to Force Charge at the planned hour |
| Every 2 min | SOC check | During charging, checks if target SOC is reached |
| Sunrise - 15 min | Morning safety | Inverter restored to Self Use Mode, schedule cleared |
| 23:55 | Daily recording | Today's consumption and solar forecast error saved to history |

### The Master Switch

The **Enabled** switch (`switch.smart_energy_manager_enabled`) is the master on/off. When turned off:

- Any active charging is stopped immediately
- The inverter is restored to Self Use Mode
- No planning or charging occurs until re-enabled

The switch persists across HA restarts.

### What "No Charging Needed" Means

The planner returns "no charging needed" only when **both** conditions are met:
1. Tomorrow's solar forecast (after error correction) covers daily consumption
2. The battery has enough charge to survive overnight until solar kicks in

If solar covers the day but the battery is too low to make it through the night, the **overnight survival check** kicks in and schedules just enough charging to bridge the gap.

---

## Settings Reference

All settings are exposed as **number entities** that you can control from the dashboard, automations, or scripts.

### Max Charge Level (%)

**Default: 90%** — The SOC the integration will charge up to. Set lower if you want to reserve headroom for solar charging. Set to 100% if you want maximum grid charge.

### Min SOC (%)

**Default: 20%** — The SOC below which the battery should not discharge. This is used for:
- Calculating usable capacity (max − min)
- Setting the inverter's discharge minimum after charging stops
- Estimating overnight battery availability

### Max Charge Power (kW)

**Default: 10.0 kW** — Used to calculate how many hours of charging are needed. Set this to your actual inverter charge rate. A 5 kWh deficit at 10 kW = 1 hour; at 5 kW = 1 hour (rounded up).

### Max Charge Price

**Default: 4.0** — If the cheapest available window's average price exceeds this, charging is skipped entirely. You'll get a "Charging Not Scheduled" notification explaining why.

### Fallback Consumption (kWh)

**Default: 20.0 kWh** — Used as the daily consumption estimate until enough real data has been collected (7 days). After that, the 7-day sliding window average of real consumption data is used instead.

### Window Start / End Hour

**Default: 22:00–06:00** — The hours during which charging is allowed. The integration finds the cheapest contiguous block within this window. Supports midnight crossing (e.g., 22 to 6 means 22:00, 23:00, 00:00, 01:00, 02:00, 03:00, 04:00, 05:00).

---

## Entities Reference

The integration creates one device with the following entities.

### Sensors

| Sensor | Unit | Description |
|--------|------|-------------|
| **Average Daily Consumption** | kWh | 7-day sliding window of real consumption. Attributes: `days_tracked`, `source` (sliding_window or fallback) |
| **Today Solar Forecast** | kWh | Combined forecast for today (all orientations) |
| **Tomorrow Solar Forecast** | kWh | Combined forecast for tomorrow |
| **Solar Forecast Error Average** | % | 7-day average overestimate. Attributes: `days_tracked`, `raw_factor` |
| **Today Solar Forecast Error** | % | Live: `(forecast − actual) / forecast × 100`. Attributes: `forecast`, `actual` |
| **Tomorrow Energy Forecast** | kWh | `adjusted_solar − consumption`. Positive = surplus, negative = deficit. Attributes: `solar_raw`, `solar_adjusted`, `forecast_error_pct`, `consumption_estimate` |
| **Battery Charge kWh** | kWh | Current battery energy: `capacity × SOC / 100` |
| **Battery Usable Charge** | kWh | Energy above min SOC. Attributes: `total_usable_capacity` |
| **Battery Capacity to Max** | kWh | Room to charge: `max_level − current`. Attributes: `usable_capacity_total` |
| **Night Charging Status** | text | One of: **Idle**, **Scheduled**, **Charging**, **Complete**, **Disabled**. Attributes: `schedule_start`, `schedule_end`, `charge_needed`, `battery_soc`, `overnight_dark_hours`, `overnight_consumption`, `overnight_charge_needed` |
| **Last Night Charge kWh** | kWh | Energy charged last session (from SOC delta). Attributes: `start_soc`, `end_soc`, `result`, `history` |
| **Last Charge Battery Range** | text | e.g., "35% → 72%" |
| **Last Charge Time Range** | text | e.g., "01:00–03:15" |
| **Last Charge Total Cost** | number | `kWh × avg_price`. Attributes: `currency` |
| **Electricity Price Status** | text | Very Cheap / Cheap / Normal / Expensive (relative to max charge price) |
| **Today Cheapest Hours** | text | Top 3 cheapest hours today. Attributes: `cheapest_price` |
| **Tomorrow Cheapest Hours** | text | Top 3 cheapest hours tomorrow. Attributes: `cheapest_price` |

### Binary Sensors

| Sensor | Description |
|--------|-------------|
| **Charging Active** | ON when the inverter is actively force-charging from grid |
| **Charging Recommended** | ON when current price is below threshold AND SOC is below max |

### Number Entities

| Entity | Range | Description |
|--------|-------|-------------|
| **Max Charge Level** | 50–100% | Maximum SOC to charge to |
| **Min SOC** | 0–50% | Minimum discharge level |
| **Max Charge Power** | 1–20 kW | Used for hours calculation |
| **Max Charge Price** | 0–20 | Price threshold |
| **Fallback Consumption** | 5–50 kWh | Used when no history available |

### Switch

| Entity | Description |
|--------|-------------|
| **Enabled** | Master on/off for all charging automation |

---

## Notifications

Notifications are sent via any HA notification service (e.g., `mobile_app_phone`, `telegram`). Configure the service name in the options flow.

Each notification type can be independently toggled on/off.

### Planning Notification

Sent when the planner runs (after price update or at 20:00).

Three variants:

**Charging Scheduled** — Shows window time, charge amount, SOC target, average price, solar info. When overnight survival triggers the charge, includes overnight context (dark hours, battery estimate at 22:00).

**Charging Not Scheduled** — Charging is needed but skipped because prices are too high or unavailable.

**No Charging Needed** — Solar covers consumption and battery covers overnight.

Notifications are **deduplicated** — the same plan won't trigger multiple notifications on the same day.

### Charging Started

Sent when the inverter enters Force Charge mode. Shows current SOC, target SOC, and charge needed.

### Charging Complete

Sent when charging stops. Shows reason (Target reached / Window ended / Morning safety stop), SOC range, and duration.

### Morning Safety

Sent if the morning safety trigger had to forcibly stop charging (meaning it was still running at sunrise).

---

## Dashboard

The `dashboards/` directory includes ready-to-use YAML:

- **`dashboard_full.yaml`** — Two views: charging status overview and detailed energy analysis
- **`dashboard_charging_status.yaml`** — Standalone charging status card

To use them, create a new dashboard in HA and paste the YAML into the raw editor.

---

## How the Algorithms Work

### Energy Deficit Calculation

```
consumption = 7-day average of daily house consumption
solar_adjusted = tomorrow_solar_forecast × (1 − forecast_error)
deficit = consumption − solar_adjusted
charge_needed = clamp(deficit, 0, usable_capacity)
```

**Usable capacity** = `battery_capacity × (max_charge_level − min_soc) / 100`

### Solar Forecast Error Correction

The integration tracks how accurate the Forecast.Solar predictions are by comparing forecast vs actual production every day at 23:55.

- **Error metric**: `(forecast − actual) / forecast` — a ratio, not absolute kWh
- **Window**: 7-day sliding average
- **Direction**: Bidirectional. Overestimates (common in winter) reduce the forecast so more grid charging is scheduled. Underestimates (common in summer) increase the forecast so less grid charging is scheduled, avoiding wasted money on unnecessary charges.

Example: If the 7-day average error is 40% (forecasts overestimate by 40%), a 10 kWh forecast becomes `10 × (1 − 0.4) = 6 kWh` for planning purposes.

The correction is **gradual** — with no history (day 1), no correction is applied. As data accumulates over 7 days, the correction reaches its full effect.

### Overnight Survival Check

Even when solar covers tomorrow's total consumption, the battery must survive ~10 hours of darkness from evening to morning. The integration simulates this hour by hour:

1. **Estimate battery at 22:00**: Current usable charge minus consumption between now and 22:00, plus any remaining solar production today.
2. **Simulate overnight drain**: Hour by hour from 22:00, subtract `hourly_consumption − hourly_solar_production` (solar is zero at night, ramps up after sunrise).
3. **Find when solar covers consumption**: Uses hourly data from the `forecast_solar` integration if available, otherwise `sunrise + 2 hours` as a conservative estimate.
4. **Compare**: If cumulative overnight drain exceeds estimated battery at 22:00, the shortfall becomes the overnight charge needed.

The planner uses `max(daily_deficit, overnight_need)` — whichever is larger determines the actual charge.

**Data sources** (in priority order):
- `forecast_solar` integration — provides Wh per hour, most accurate
- `sun.sun` entity — sunrise time + 2h buffer for PV ramp-up
- Fallback — window end hour + 3 hours (e.g., 09:00)

### Cheapest Window Selection

Within the configured night window (default 22:00–06:00):

1. Extract hourly prices from the price sensor's attributes
2. Calculate hours needed: `ceil(charge_kWh / charge_power_kW)`
3. Slide a window of that length across available hours
4. Pick the contiguous block with the lowest average price
5. Reject if average price exceeds the configured threshold

### Consumption Tracking

At 23:55 each day, the integration reads your daily consumption sensor (which resets at midnight) and stores the value. The 7-day sliding window average is used for all planning.

Until 7 days of data are collected, the fallback value (default 20 kWh) is used instead.

---

## Troubleshooting

### Battery Depleted Overnight

**Symptoms**: Battery reached shutdown SOC (e.g., 11%) before sunrise despite "No Charging Needed" the previous evening.

**Possible causes**:
- Solar forecast was overly optimistic → the forecast error correction will learn and adjust over the following days
- Battery was already low when the planner ran → the overnight survival check should prevent this (check if `overnight_charge_needed` attribute was > 0)
- Check the `night_charging_status` sensor attributes for `overnight_dark_hours` and `overnight_charge_needed`

### Charging Didn't Happen

1. Check the **Enabled** switch — must be ON
2. Check the **Night Charging Status** sensor — is it "Scheduled"?
3. Check if tomorrow's prices are available in your price sensor's attributes
4. Check HA logs for `smart_energy_manager` entries
5. Verify the inverter entities respond to service calls (try manually selecting Manual Mode)

### Prices "Not Available"

The planner only runs when it can find tomorrow's date in the price sensor's attributes. Most spot price integrations publish tomorrow's prices between 13:00 and 14:00 CET. The 20:00 fallback trigger catches late arrivals.

### Charging Stopped Mid-Session

Possible reasons (check the **Last Charge Result** attribute):
- **Target reached** — Normal, SOC hit the target
- **Window ended** — Charging window expired before reaching target (increase window size or charge power)
- **Morning safety stop** — Sunrise arrived while still charging
- **Disabled** — Someone turned off the master switch

### Inverter Not Responding

If the inverter doesn't switch modes:
1. Verify entity IDs in the integration config match your actual entities
2. Test manually: go to Developer Tools → Services → `select.select_option` and try switching modes
3. Check for Modbus communication errors in the inverter integration's logs
4. Some inverters need a delay between commands — the integration includes a 5-second settle delay

### State Stuck on "Charging"

The morning safety trigger (sunrise − 15 min) always resets the state. If it seems stuck:
1. Wait for the next sunrise trigger
2. Or toggle the master switch off and on
3. Check HA logs for errors in the morning safety handler

---

## FAQ

**Q: Does it work without solar panels?**
A: Yes. Set the solar forecast sensors to any sensor that returns 0, or use a template sensor. The deficit will equal your full daily consumption, and the integration will charge accordingly.

**Q: Can I change settings without restarting HA?**
A: Yes. All number entities (max charge level, min SOC, price threshold, etc.) take effect immediately. For deeper changes (inverter entities, price sensor), use the options flow and HA will reload the integration.

**Q: What happens during a HA restart?**
A: The master switch state is restored automatically. Consumption history, forecast error history, and last session data are stored in JSON and survive restarts. Any in-progress charging session will be caught by the morning safety trigger at the next sunrise.

**Q: Why does it sometimes charge even when solar is enough?**
A: The overnight survival check ensures the battery can bridge the gap from evening to morning. Even with 20 kWh of solar tomorrow, if your battery is at 30% at 22:00, it may not last until solar ramps up at 9:00.

**Q: How long until the consumption and forecast tracking is accurate?**
A: The integration starts using real data from day 1 but reaches full accuracy after 7 days (the sliding window size). The fallback consumption value is used until then.

**Q: Can I run it alongside other charging automations?**
A: Not recommended. The integration controls the inverter mode directly. Running other automations that also change inverter modes will cause conflicts. Use the master switch to disable this integration if you need manual control.

**Q: Does it support time-of-use (TOU) tariffs instead of spot prices?**
A: Currently it's designed for spot/hourly pricing with prices as sensor attributes. For fixed TOU tariffs, you could create a template sensor that publishes the TOU rates in the expected attribute format.

**Q: What if my inverter isn't in the template list?**
A: Choose "Custom / Other" and fill in all entity IDs and mode strings manually. Any inverter that exposes mode selection and charge commands as HA select entities will work.
