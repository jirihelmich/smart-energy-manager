# Domain Rename: smart_battery_charging → smart_energy_manager

## Overview

Full rename of the integration domain, preserving all entity history, config, and dashboard references.

## Prerequisites

- HA running and accessible via SSH
- New codebase with renamed domain already prepared

## Steps

### 1. Prepare the renamed codebase

Rename the directory and do a global find-replace in the codebase:

```bash
# From repo root
mv custom_components/smart_battery_charging custom_components/smart_energy_manager

# Find-replace in all Python/JSON/YAML files:
#   smart_battery_charging → smart_energy_manager
# Key files: const.py (DOMAIN), manifest.json, all imports, dashboard entity refs
```

### 2. Deploy new code to HA

```bash
COPYFILE_DISABLE=1 tar cf - -C custom_components/smart_energy_manager . \
  | ssh hassio@homeassistant 'sudo mkdir -p /config/custom_components/smart_energy_manager && sudo tar xf - -C /config/custom_components/smart_energy_manager'
```

### 3. Upload and run migration script

```bash
cat migration/rename_domain.py | ssh hassio@homeassistant 'sudo tee /config/custom_components/smart_energy_manager/rename_domain.py > /dev/null'

ssh hassio@homeassistant 'sudo python3 /config/custom_components/smart_energy_manager/rename_domain.py'
```

### 4. Restart HA

```bash
TOKEN=$(cat .claude/skills/ha-fetch-states/.token)
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://homeassistant:8123/api/services/homeassistant/restart
```

### 5. Verify

- Check integration loads in Settings → Devices & Services
- Check entity history is preserved (pick a sensor, look at history graph)
- Check energy dashboard still shows data
- Check dashboard Surplus tab works

### 6. Clean up

```bash
ssh hassio@homeassistant 'sudo rm -rf /config/custom_components/smart_battery_charging'
ssh hassio@homeassistant 'sudo rm /config/custom_components/smart_energy_manager/rename_domain.py'
```

## What the script migrates

| File | What changes |
|------|-------------|
| `.storage/core.config_entries` | `domain` field |
| `.storage/core.entity_registry` | `platform`, `entity_id`, `unique_id` |
| `.storage/core.device_registry` | `identifiers` tuples |
| `.storage/smart_battery_charging.json` | Renamed to `smart_energy_manager.json`, `key` field updated |
| `.storage/lovelace.dashboard_solax` | All entity ID references |
| `home-assistant_v2.db` | `statistics_meta.statistic_id`, `states_meta.entity_id` |

## Rollback

Backups are saved to `/config/backups/domain_rename_<timestamp>/`. To rollback:

1. Stop HA
2. Copy all files from the backup back to their original locations
3. Remove new code directory
4. Start HA
