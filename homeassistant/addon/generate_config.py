"""
generate_config.py – converts /data/options.json (written by HA Supervisor)
into /data/config.yaml and populates /data/watchlist.json at startup.

Options format:
  watches:
    - team: "HV 71"
      season_ids: [18263, 19791]
    - team: "Frölunda"
      season_ids: [18263, 18289]

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


def _generate_id(team: str, season_ids: list) -> str:
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
    # Load existing watchlist (preserves entries added manually via the API)
    existing: dict = {}
    if WATCHLIST_FILE.exists():
        try:
            existing = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    new_entries: dict = {}
    for w in watches:
        team = w["team"]
        season_ids = sorted(int(s) for s in w["season_ids"])
        wid = _generate_id(team, season_ids)
        new_entries[wid] = {"id": wid, "team": team, "season_ids": season_ids}

    merged = {**existing, **new_entries}
    WATCHLIST_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    added = [e for e in new_entries if e not in existing]
    print(
        f"[generate_config] watchlist.json: {len(merged)} entries total"
        + (f" ({len(added)} new: {added})" if added else " (no changes)")
    )

    # ── Write config.yaml (first watch = default team for root endpoints) ──────
    first = watches[0]
    config = {
        "team": first["team"],
        "season_ids": sorted(int(s) for s in first["season_ids"]),
        "port": 8080,
    }
    CONFIG_FILE.write_text(
        yaml.dump(config, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    print(
        f"[generate_config] config.yaml: default team={config['team']!r}, "
        f"season_ids={config['season_ids']}"
    )


if __name__ == "__main__":
    main()
