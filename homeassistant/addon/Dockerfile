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
  io.hass.version="2.3.0" \
  io.hass.arch="aarch64|amd64|armv7"

# System deps: libxml2/libxslt for lxml, curl for source download
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Download app source from GitHub (app.py, scraper.py, config.py, watchlist.py, requirements.txt)
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

def _generate_id(team, season_ids):
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
            existing = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    new_entries = {}
    for w in watches:
        team = w["team"]
        raw = w["season_ids"]
        if isinstance(raw, str):
            season_ids = sorted(int(s.strip()) for s in raw.split(",") if s.strip())
        else:
            season_ids = sorted(int(s) for s in raw)
        wid = _generate_id(team, season_ids)
        new_entries[wid] = {"id": wid, "team": team, "season_ids": season_ids}

    merged = {**existing, **new_entries}
    WATCHLIST_FILE.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    added = [e for e in new_entries if e not in existing]
    print(f"[generate_config] watchlist.json: {len(merged)} entries" +
          (f" ({len(added)} new)" if added else " (no changes)"))

    first = watches[0]
    raw_first = first["season_ids"]
    if isinstance(raw_first, str):
        first_ids = sorted(int(s.strip()) for s in raw_first.split(",") if s.strip())
    else:
        first_ids = sorted(int(s) for s in raw_first)
    config = {"team": first["team"], "season_ids": first_ids, "port": 8080}
    CONFIG_FILE.write_text(yaml.dump(config, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    print(f"[generate_config] config.yaml: team={config['team']!r}, season_ids={config['season_ids']}")

if __name__ == "__main__":
    main()
EOF

RUN chmod +x /app/run.sh

# /data is mounted by the Supervisor — config.yaml and watchlist.json live there
ENV HOCKEY_CONFIG=/data/config.yaml

EXPOSE 8080

CMD ["/app/run.sh"]
