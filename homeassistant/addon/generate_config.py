"""
generate_config.py – converts /data/options.json (written by HA Supervisor)
into /data/config.yaml that the HockeyLive API reads at startup.

When running outside HA (standalone / Docker), this file is unused;
config.yaml is provided directly via bind-mount or placed in /data/.
"""

import json
import yaml
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")
CONFIG_FILE = Path("/data/config.yaml")


def main() -> None:
    if not OPTIONS_FILE.exists():
        print(
            f"[generate_config] {OPTIONS_FILE} not found – skipping (standalone mode?)"
        )
        return

    options: dict = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))

    team = options.get("team", "")
    season_ids = options.get("season_ids", [])

    if not team:
        raise ValueError("'team' is required in add-on options")
    if not season_ids:
        raise ValueError("'season_ids' must contain at least one ID")

    config = {
        "team": team,
        "season_ids": [int(s) for s in season_ids],
        "port": 8080,
    }

    CONFIG_FILE.write_text(
        yaml.dump(config, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    print(
        f"[generate_config] Written {CONFIG_FILE}: "
        f"team={config['team']!r}, season_ids={config['season_ids']}"
    )


if __name__ == "__main__":
    main()
