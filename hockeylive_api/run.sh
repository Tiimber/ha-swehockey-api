#!/bin/sh
# run.sh – Add-on entry point.
# Reads /data/options.json (written by HA Supervisor from add-on config),
# generates /data/config.yaml for the app, then starts uvicorn.
set -e

echo "[HockeyLive] Generating config from add-on options..."
python3 /app/generate_config.py

echo "[HockeyLive] Starting API on port 8080..."
exec uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1
