"""
generate_config.py – converts /data/options.json (written by HA Supervisor)
into /data/config.yaml and populates /data/watchlist.json at startup.

Options format:
  mqtt_host: "core-mosquitto"
  mqtt_port: 1883
  mqtt_username: ""
  mqtt_password: ""
  watches:
    - name: "HV71 SHL"      # optional stable alias – ID is based on this
      team: "HV 71"
      season_ids: "18263, 19791"
    - name: "HV71 U20"
      team: "HV 71"
      season_ids: "22500"

The 'name' field is optional for backward compatibility.
If omitted, the ID falls back to sha1(team+sorted(season_ids)) as before.

When running outside HA (standalone / Docker), this file is unused;
config.yaml is provided directly via bind-mount or placed in /data/.
"""

import hashlib
import json
import sys
import yaml
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")
CONFIG_FILE = Path("/data/config.yaml")
WATCHLIST_FILE = Path("/data/watchlist.json")


def _generate_id(team: str, season_ids: list, name: str = None) -> str:
    """
    If name is provided, ID is stable across season_id changes.
    Otherwise falls back to the original team+season_ids hash.
    """
    if name and name.strip():
        key = name.strip().lower()
    else:
        key = f"{team.lower()}:{sorted(season_ids)}"
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def main() -> None:
    if not OPTIONS_FILE.exists():
        print(
            f"[generate_config] {OPTIONS_FILE} not found – skipping (standalone mode?)"
        )
        return

    options: dict = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))

    watches = options.get("watches", [])
    if not watches:
        print("[generate_config] ERROR: 'watches' list is empty", file=sys.stderr)
        sys.exit(1)

    # ── Populate watchlist.json ───────────────────────────────────────────────
    existing: dict = {}
    if WATCHLIST_FILE.exists():
        try:
            raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            existing = raw.get("watches", raw) if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            existing = {}

    new_entries: dict = {}
    for w in watches:
        name = w.get("name") or None
        team = w["team"]
        raw_ids = w["season_ids"]
        if isinstance(raw_ids, str):
            season_ids = sorted(int(s.strip()) for s in raw_ids.split(",") if s.strip())
        else:
            season_ids = sorted(int(s) for s in raw_ids)
        wid = _generate_id(team, season_ids, name)
        entry: dict = {"id": wid, "team": team, "season_ids": season_ids}
        if name:
            entry["name"] = name
        new_entries[wid] = entry

    merged = {**existing, **new_entries}
    WATCHLIST_FILE.write_text(
        json.dumps({"watches": merged}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    added = [e for e in new_entries if e not in existing]
    print(
        f"[generate_config] watchlist.json: {len(merged)} entries total"
        + (f" ({len(added)} new: {added})" if added else " (no changes)")
    )

    # ── Write config.yaml (first watch = default team for root endpoints) ──────
    first = watches[0]
    raw_first = first["season_ids"]
    if isinstance(raw_first, str):
        first_ids = sorted(int(s.strip()) for s in raw_first.split(",") if s.strip())
    else:
        first_ids = sorted(int(s) for s in raw_first)

    config = {
        "team": first["team"],
        "season_ids": first_ids,
        "port": 8080,
        "mqtt_host": options.get("mqtt_host", ""),
        "mqtt_port": int(options.get("mqtt_port") or 1883),
        "mqtt_username": options.get("mqtt_username", ""),
        "mqtt_password": options.get("mqtt_password", ""),
    }
    CONFIG_FILE.write_text(
        yaml.dump(config, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    print(
        f"[generate_config] config.yaml: default team={config['team']!r}, "
        f"season_ids={config['season_ids']}, "
        f"mqtt_host={config['mqtt_host']!r}"
    )


if __name__ == "__main__":
    main()
