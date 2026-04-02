# syntax=docker/dockerfile:1
#
# Add-on Dockerfile for HockeyLive API.
#
# The HA Supervisor only includes config.yaml and this Dockerfile in the
# Docker build context. App source (app.py, scraper.py, etc.) is downloaded
# from GitHub at build time. generate_config.py and run.sh are embedded
# directly as heredocs so they are always current regardless of Docker cache.

FROM python:3.12-slim

LABEL \
  io.hass.name="HockeyLive API" \
  io.hass.description="Swedish ice hockey live scores from swehockey.se" \
  io.hass.version="2.5.1" \
  io.hass.arch="aarch64|amd64|armv7"

# System deps: libxml2/libxslt for lxml, curl for source download
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CACHEBUST forces Docker to re-download source on every version bump.
# Change this value whenever new code must be pulled from GitHub.
ARG CACHEBUST=20260402_1800
ARG REPO_URL=https://github.com/Tiimber/ha-swehockey-api/archive/refs/heads/master.tar.gz
RUN curl -fsSL "${REPO_URL}" \
    | tar -xz --strip-components=1 \
    && pip3 install --no-cache-dir -r requirements.txt \
    && rm -rf homeassistant/ custom_components/ scripts/

# Embed run.sh directly — always current, no cache issue
COPY <<'EOF' /app/run.sh
#!/bin/sh
set -e
echo "[HockeyLive] Generating config from add-on options..."
python3 /app/generate_config.py
echo "[HockeyLive] Starting API on port 8080..."
exec uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1
EOF

# Embed generate_config.py directly — always current, no cache issue
COPY <<'EOF' /app/generate_config.py
import hashlib, json, sys, yaml
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")
CONFIG_FILE  = Path("/data/config.yaml")
WATCHLIST_FILE = Path("/data/watchlist.json")

def _generate_id(team, season_ids, name=None):
    if name and name.strip():
        key = name.strip().lower()
    else:
        key = f"{team.lower()}:{sorted(season_ids)}"
    return hashlib.sha1(key.encode()).hexdigest()[:8]

def main():
    if not OPTIONS_FILE.exists():
        print(f"[generate_config] {OPTIONS_FILE} not found – skipping")
        return

    options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
    watches = options.get("watches", [])
    if not watches:
        print("[generate_config] ERROR: 'watches' list is empty", file=sys.stderr)
        sys.exit(1)

    existing = {}
    if WATCHLIST_FILE.exists():
        try:
            raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            existing = raw.get("watches", raw) if isinstance(raw, dict) else {}
        except json.JSONDecodeError:
            existing = {}

    new_entries = {}
    for w in watches:
        name = w.get("name") or None
        team = w["team"]
        raw_ids = w["season_ids"]
        if isinstance(raw_ids, str):
            season_ids = sorted(int(s.strip()) for s in raw_ids.split(",") if s.strip())
        else:
            season_ids = sorted(int(s) for s in raw_ids)
        wid = _generate_id(team, season_ids, name)
        entry = {"id": wid, "team": team, "season_ids": season_ids}
        if name:
            entry["name"] = name
        new_entries[wid] = entry

    merged = {**existing, **new_entries}
    WATCHLIST_FILE.write_text(json.dumps({"watches": merged}, ensure_ascii=False, indent=2), encoding="utf-8")
    added = [e for e in new_entries if e not in existing]
    print(f"[generate_config] watchlist.json: {len(merged)} entries" +
          (f" ({len(added)} new)" if added else " (no changes)"))

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
    CONFIG_FILE.write_text(yaml.dump(config, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    print(f"[generate_config] config.yaml: team={config['team']!r}, season_ids={config['season_ids']}, mqtt_host={config['mqtt_host']!r}")

if __name__ == "__main__":
    main()
EOF

RUN chmod +x /app/run.sh

# /data is mounted by the Supervisor — config.yaml and watchlist.json live there
ENV HOCKEY_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["/app/run.sh"]
