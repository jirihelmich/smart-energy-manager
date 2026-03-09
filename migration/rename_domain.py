#!/usr/bin/env python3
"""Migrate HA instance from smart_battery_charging → smart_energy_manager.

Run this ON the HA host (via SSH) while HA is still running, then restart HA.

Usage:
    python3 /config/custom_components/smart_energy_manager/migration/rename_domain.py

What it does:
    1. Backs up all files it touches to /config/backups/domain_rename_<timestamp>/
    2. Renames domain in .storage/core.config_entries
    3. Renames entity IDs in .storage/core.entity_registry
    4. Renames device identifiers in .storage/core.device_registry
    5. Renames integration store: .storage/smart_battery_charging.json → smart_energy_manager.json
    6. Renames entity IDs in dashboard: .storage/lovelace.dashboard_solax
    7. Renames entity IDs in recorder DB: statistics_meta + states_meta tables
    8. Prints summary — does NOT restart HA (do that yourself after verifying)

Prerequisite:
    - New code already deployed to /config/custom_components/smart_energy_manager/
    - Old code still at /config/custom_components/smart_battery_charging/ (will be removed after)
"""

import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

OLD_DOMAIN = "smart_battery_charging"
NEW_DOMAIN = "smart_energy_manager"
OLD_PREFIX = "smart_battery_charging_"
NEW_PREFIX = "smart_energy_manager_"

CONFIG_DIR = Path("/config")
STORAGE_DIR = CONFIG_DIR / ".storage"
DB_PATH = CONFIG_DIR / "home-assistant_v2.db"

BACKUP_BASE = CONFIG_DIR / "backups"


def backup_file(src: Path, backup_dir: Path) -> None:
    """Copy a file to the backup directory, preserving relative path."""
    if not src.exists():
        return
    rel = src.relative_to(CONFIG_DIR)
    dst = backup_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  Backed up: {rel}")


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def migrate_config_entries(backup_dir: Path) -> int:
    """Rename domain in core.config_entries."""
    path = STORAGE_DIR / "core.config_entries"
    if not path.exists():
        print("  SKIP: core.config_entries not found")
        return 0
    backup_file(path, backup_dir)
    data = load_json(path)

    count = 0
    for entry in data.get("data", {}).get("entries", []):
        if entry.get("domain") == OLD_DOMAIN:
            entry["domain"] = NEW_DOMAIN
            count += 1

    save_json(path, data)
    print(f"  Updated {count} config entries")
    return count


def migrate_entity_registry(backup_dir: Path) -> int:
    """Rename entity IDs and platform references in core.entity_registry."""
    path = STORAGE_DIR / "core.entity_registry"
    if not path.exists():
        print("  SKIP: core.entity_registry not found")
        return 0
    backup_file(path, backup_dir)
    data = load_json(path)

    count = 0
    entities = data.get("data", {}).get("entities", [])
    for entity in entities:
        if entity.get("platform") == OLD_DOMAIN:
            entity["platform"] = NEW_DOMAIN
            count += 1

        # Rename entity_id: sensor.smart_battery_charging_foo → sensor.smart_energy_manager_foo
        eid = entity.get("entity_id", "")
        if OLD_PREFIX in eid:
            entity["entity_id"] = eid.replace(OLD_PREFIX, NEW_PREFIX)

        # Rename unique_id if it contains the old domain
        uid = entity.get("unique_id", "")
        if OLD_DOMAIN in str(uid):
            entity["unique_id"] = str(uid).replace(OLD_DOMAIN, NEW_DOMAIN)

    # Also update deleted_entities if present
    for entity in data.get("data", {}).get("deleted_entities", []):
        if entity.get("platform") == OLD_DOMAIN:
            entity["platform"] = NEW_DOMAIN
        eid = entity.get("entity_id", "")
        if OLD_PREFIX in eid:
            entity["entity_id"] = eid.replace(OLD_PREFIX, NEW_PREFIX)
        uid = entity.get("unique_id", "")
        if OLD_DOMAIN in str(uid):
            entity["unique_id"] = str(uid).replace(OLD_DOMAIN, NEW_DOMAIN)

    save_json(path, data)
    print(f"  Updated {count} entity registry entries")
    return count


def migrate_device_registry(backup_dir: Path) -> int:
    """Rename device identifiers in core.device_registry."""
    path = STORAGE_DIR / "core.device_registry"
    if not path.exists():
        print("  SKIP: core.device_registry not found")
        return 0
    backup_file(path, backup_dir)
    data = load_json(path)

    count = 0
    for device in data.get("data", {}).get("devices", []):
        new_ids = []
        changed = False
        for id_pair in device.get("identifiers", []):
            if isinstance(id_pair, list) and len(id_pair) == 2 and id_pair[0] == OLD_DOMAIN:
                new_ids.append([NEW_DOMAIN, id_pair[1]])
                changed = True
            else:
                new_ids.append(id_pair)
        if changed:
            device["identifiers"] = new_ids
            count += 1

    # Also update deleted_devices
    for device in data.get("data", {}).get("deleted_devices", []):
        new_ids = []
        changed = False
        for id_pair in device.get("identifiers", []):
            if isinstance(id_pair, list) and len(id_pair) == 2 and id_pair[0] == OLD_DOMAIN:
                new_ids.append([NEW_DOMAIN, id_pair[1]])
                changed = True
            else:
                new_ids.append(id_pair)
        if changed:
            device["identifiers"] = new_ids

    save_json(path, data)
    print(f"  Updated {count} device registry entries")
    return count


def migrate_integration_store(backup_dir: Path) -> bool:
    """Rename the integration's own storage file."""
    old_path = STORAGE_DIR / f"{OLD_DOMAIN}.json"
    new_path = STORAGE_DIR / f"{NEW_DOMAIN}.json"

    if not old_path.exists():
        print(f"  SKIP: {OLD_DOMAIN}.json not found")
        return False

    backup_file(old_path, backup_dir)
    data = load_json(old_path)

    # Update the key field if present
    if data.get("key") == OLD_DOMAIN:
        data["key"] = NEW_DOMAIN

    save_json(new_path, data)
    old_path.unlink()
    print(f"  Renamed {OLD_DOMAIN}.json → {NEW_DOMAIN}.json")
    return True


def migrate_dashboard(backup_dir: Path) -> int:
    """Rename entity IDs in dashboard storage."""
    path = STORAGE_DIR / "lovelace.dashboard_solax"
    if not path.exists():
        print("  SKIP: lovelace.dashboard_solax not found")
        return 0
    backup_file(path, backup_dir)

    # Read as text for simple find-replace (handles nested YAML/JSON content)
    text = path.read_text()
    count = text.count(OLD_PREFIX)
    text = text.replace(OLD_PREFIX, NEW_PREFIX)
    # Also replace bare domain references (e.g. in entity attributes)
    text = text.replace(OLD_DOMAIN, NEW_DOMAIN)
    path.write_text(text)

    print(f"  Updated {count} entity references in dashboard")
    return count


def migrate_recorder_db(backup_dir: Path) -> tuple[int, int]:
    """Rename entity IDs in the recorder database (statistics + states)."""
    if not DB_PATH.exists():
        print("  SKIP: home-assistant_v2.db not found")
        return 0, 0

    backup_file(DB_PATH, backup_dir)

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    # statistics_meta: statistic_id column
    cursor.execute(
        "SELECT id, statistic_id FROM statistics_meta WHERE statistic_id LIKE ?",
        (f"%{OLD_PREFIX}%",),
    )
    stats_rows = cursor.fetchall()
    for row_id, stat_id in stats_rows:
        new_id = stat_id.replace(OLD_PREFIX, NEW_PREFIX)
        cursor.execute(
            "UPDATE statistics_meta SET statistic_id = ? WHERE id = ?",
            (new_id, row_id),
        )

    # states_meta: entity_id column
    cursor.execute(
        "SELECT metadata_id, entity_id FROM states_meta WHERE entity_id LIKE ?",
        (f"%{OLD_PREFIX}%",),
    )
    states_rows = cursor.fetchall()
    for meta_id, entity_id in states_rows:
        new_id = entity_id.replace(OLD_PREFIX, NEW_PREFIX)
        cursor.execute(
            "UPDATE states_meta SET entity_id = ? WHERE metadata_id = ?",
            (new_id, meta_id),
        )

    conn.commit()
    conn.close()

    print(f"  Updated {len(stats_rows)} statistics_meta rows")
    print(f"  Updated {len(states_rows)} states_meta rows")
    return len(stats_rows), len(states_rows)


def main() -> None:
    print(f"Domain Rename: {OLD_DOMAIN} → {NEW_DOMAIN}")
    print("=" * 60)

    # Verify we're on the HA host
    if not CONFIG_DIR.exists():
        print(f"ERROR: {CONFIG_DIR} not found. Run this on the HA host.")
        sys.exit(1)

    # Check new code is deployed
    new_code = CONFIG_DIR / "custom_components" / NEW_DOMAIN
    if not new_code.exists():
        print(f"ERROR: New code not found at {new_code}")
        print("Deploy the renamed integration first, then run this script.")
        sys.exit(1)

    # Create backup directory
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_BASE / f"domain_rename_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nBackup directory: {backup_dir}\n")

    # Run migrations
    print("[1/6] Config entries...")
    migrate_config_entries(backup_dir)

    print("[2/6] Entity registry...")
    migrate_entity_registry(backup_dir)

    print("[3/6] Device registry...")
    migrate_device_registry(backup_dir)

    print("[4/6] Integration store...")
    migrate_integration_store(backup_dir)

    print("[5/6] Dashboard...")
    migrate_dashboard(backup_dir)

    print("[6/6] Recorder database...")
    stats, states = migrate_recorder_db(backup_dir)

    print("\n" + "=" * 60)
    print("Migration complete!")
    print()
    print("Next steps:")
    print("  1. Restart HA (via API or UI)")
    print("  2. Verify integration loads correctly")
    print("  3. Check entity history is preserved")
    print(f"  4. Remove old code: rm -rf /config/custom_components/{OLD_DOMAIN}")
    print(f"\nBackups at: {backup_dir}")
    print("To rollback, copy backup files back to their original locations.")


if __name__ == "__main__":
    main()
