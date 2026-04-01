#!/usr/bin/env bash
# scripts/build_addon.sh
#
# Assembles a self-contained HA local add-on directory by merging the
# app source files with the add-on specific files.
#
# Usage (run from repo root):
#   ./scripts/build_addon.sh                    # → /tmp/hockeylive-addon/
#   ./scripts/build_addon.sh /addons/hockeylive  # write directly to HA (via Samba/SSH)
#
# After running, copy the resulting directory to /addons/ on your HA instance.
# Then reload the Add-on Store: Settings → Add-ons → ⋮ → Check for updates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TARGET="${1:-/tmp/hockeylive-addon}"

echo "╔══════════════════════════════════════╗"
echo "║  HockeyLive Add-on Builder           ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Repo   : $REPO_ROOT"
echo "Target : $TARGET"
echo ""

mkdir -p "$TARGET"

# ── Add-on specific files ─────────────────────────────────────────────────
echo "Copying add-on files..."
cp "$REPO_ROOT/homeassistant/addon/config.yaml"       "$TARGET/"
cp "$REPO_ROOT/homeassistant/addon/Dockerfile"         "$TARGET/"
cp "$REPO_ROOT/homeassistant/addon/run.sh"             "$TARGET/"
cp "$REPO_ROOT/homeassistant/addon/generate_config.py" "$TARGET/"

# ── App source files ──────────────────────────────────────────────────────
echo "Copying app source files..."
cp "$REPO_ROOT/app.py"           "$TARGET/"
cp "$REPO_ROOT/scraper.py"       "$TARGET/"
cp "$REPO_ROOT/config.py"        "$TARGET/"
cp "$REPO_ROOT/watchlist.py"     "$TARGET/"
cp "$REPO_ROOT/requirements.txt" "$TARGET/"

echo ""
echo "✓ Add-on ready at: $TARGET"
echo ""
echo "Next steps for Home Assistant Green:"
echo ""
echo "  Option A – Samba (easiest):"
echo "    1. Install 'Samba share' add-on in HA"
echo "    2. Map \\\\<ha-ip>\\addons on your PC"
echo "    3. Copy the '$TARGET' folder to \\\\<ha-ip>\\addons\\hockeylive"
echo ""
echo "  Option B – SCP from this machine:"
echo "    scp -r '$TARGET' root@<ha-ip>:/addons/hockeylive"
echo "    (Requires SSH add-on with 'Allow network connections' enabled)"
echo ""
echo "  Then in HA:"
echo "    Settings → Add-ons → ⋮ → Check for updates"
echo "    'HockeyLive API' appears under Local add-ons → Install → Configure → Start"
