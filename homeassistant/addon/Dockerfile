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
  io.hass.version="2.7.2" \
  io.hass.arch="aarch64|amd64|armv7"

# System deps: libxml2/libxslt for lxml, curl for source download
RUN apt-get update && apt-get install -y --no-install-recommends \
        libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CACHEBUST forces Docker to re-download source on every version bump.
# IMPORTANT: ${CACHEBUST} must appear in the RUN command below for Docker
# cache invalidation to actually trigger when the value changes.
ARG CACHEBUST=20260407_10009
ARG REPO_URL=https://github.com/Tiimber/ha-swehockey-api/archive/refs/heads/master.tar.gz
RUN echo "Cache bust: ${CACHEBUST}" \
    && echo "${CACHEBUST}" > /app/build_id \
    && curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors -o /tmp/repo.tar.gz "${REPO_URL}" \
    && tar -xz --strip-components=1 -f /tmp/repo.tar.gz \
    && rm /tmp/repo.tar.gz \
    && pip3 install --no-cache-dir -r requirements.txt \
    && rm -rf homeassistant/ custom_components/ scripts/

# Embed run.sh directly — always current, no cache issue
COPY <<'EOF' /app/run.sh
#!/bin/sh
set -e
echo "[HockeyLive] Build: $(cat /app/build_id 2>/dev/null || echo unknown)"
echo "[HockeyLive] Generating config from add-on options..."
python3 /app/generate_config.py
echo "[HockeyLive] Generating AWTRIX automations..."
python3 /app/generate_awtrix.py
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

# Embed mqtt_publisher.py directly — always current, overwrites the downloaded copy
COPY <<'MQTT_PY_EOF' /app/mqtt_publisher.py
"""
mqtt_publisher.py – MQTT Discovery + state push for HockeyLive add-on.

Publishes one MQTT device per watch entry, with sensors auto-discovered by HA.
All sensors share a single JSON state topic – one message updates everything.

Topics per watch:
  hockeylive/<watch_id>/state            JSON payload (all sensor values)
  hockeylive/<watch_id>/availability     "online" | "offline"

Discovery topics per entity (retained, empty payload = HA removes entity):
  homeassistant/sensor/hockeylive_<slug>_status/config
  homeassistant/binary_sensor/hockeylive_<slug>_live/config
  homeassistant/sensor/hockeylive_<slug>_score/config
  homeassistant/sensor/hockeylive_<slug>_period/config
  homeassistant/sensor/hockeylive_<slug>_next_match/config
  homeassistant/sensor/hockeylive_<slug>_last_result/config

Entity tracking:  /data/mqtt_entities.json
  {watch_id: {slug, discovery_topics: [...]}}

Automation example:
  trigger:
    platform: mqtt
    topic: hockeylive/<id>/state
  condition:
    "{{ trigger.payload_json.is_playing }}"
  action:
    service: mqtt.publish
    data:
      topic: awtrix_XXXXX/notify
      payload: '{"text": "{{ trigger.payload_json.score }}", "duration": 30}'
"""

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime as _datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo as _ZoneInfo

logger = logging.getLogger(__name__)

ENTITIES_FILE = Path("/data/mqtt_entities.json")
GOALS_CACHE_FILE = Path("/data/goals_cache.json")
MQTT_PREFIX = "hockeylive"
DISCOVERY_PREFIX = "homeassistant"


# ---------------------------------------------------------------------------
# Slug / topic helpers
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """'HV 71 SHL' → 'hv71shl'  (lowercase ASCII alphanumeric only)."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def watch_slug(watch: dict) -> str:
    """Return the entity slug for a watch, derived from its display name."""
    display = watch.get("name") or watch["team"]
    return _slugify(display)


def _state_topic(watch_id: str) -> str:
    return f"{MQTT_PREFIX}/{watch_id}/state"


def _attr_topic(watch_id: str) -> str:
    return f"{MQTT_PREFIX}/{watch_id}/attr"


def _avail_topic(watch_id: str) -> str:
    return f"{MQTT_PREFIX}/{watch_id}/availability"


def _discovery_topic(component: str, object_id: str) -> str:
    return f"{DISCOVERY_PREFIX}/{component}/{object_id}/config"


# ---------------------------------------------------------------------------
# Entity-tracking file  (/data/mqtt_entities.json)
# ---------------------------------------------------------------------------


def _load_entities() -> dict:
    if ENTITIES_FILE.exists():
        try:
            return json.loads(ENTITIES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_entities(data: dict) -> None:
    try:
        ENTITIES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Failed to save mqtt_entities.json: %s", exc)


def _load_goals_cache() -> dict:
    if GOALS_CACHE_FILE.exists():
        try:
            return json.loads(GOALS_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_goals_cache(data: dict) -> None:
    try:
        GOALS_CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Failed to save goals_cache.json: %s", exc)


# ---------------------------------------------------------------------------
# State payload builder
# ---------------------------------------------------------------------------


def _build_state(status_payload: dict) -> dict:
    """Flatten a /watch/{id}/status response into a compact MQTT state dict."""
    live = status_payload.get("live", {})
    next_match = status_payload.get("next_match")
    last_match = status_payload.get("last_match")
    is_playing = live.get("is_playing", False)

    # Compute last_match_today early — drives status logic
    last_match_date = last_match.get("date") if last_match else None
    last_match_today = False
    if last_match_date:
        try:
            now_sthlm = _datetime.now(_ZoneInfo("Europe/Stockholm"))
            last_match_today = last_match_date == now_sthlm.strftime("%Y-%m-%d")
        except Exception:
            pass

    # Status and main score
    if is_playing:
        period_raw = live.get("period", "")
        _period_states = {"P1": "live_p1", "P2": "live_p2", "P3": "live_p3", "OT": "live_ot", "SO": "live_so"}
        status = _period_states.get(period_raw, "live")
        score = f"{live.get('home_score', 0)}\u2013{live.get('away_score', 0)}"
    elif last_match_today:
        # Game finished today — keep showing result until midnight
        status = "finished_today"
        score = (
            f"{last_match['home_score']}\u2013{last_match['away_score']}"
            if last_match and last_match.get("home_score") is not None
            else "\u2013"
        )
    elif next_match:
        score = "\u2013"
        # Sub-divide upcoming by time-to-match so automations can trigger on state change
        next_dt_raw = next_match.get("datetime_iso")
        if next_dt_raw:
            try:
                dt = _datetime.fromisoformat(next_dt_raw)
                now_sthlm = _datetime.now(_ZoneInfo("Europe/Stockholm"))
                secs_to_match = (dt - now_sthlm).total_seconds()
                if secs_to_match <= 7200:
                    status = "upcoming_prematch"   # ≤2h: show 0-0 scoreboard
                elif dt.date() == now_sthlm.date():
                    status = "upcoming_countdown"  # match day, >2h: show countdown
                else:
                    status = "upcoming_far"        # not today: show calendar day
            except Exception:
                status = "upcoming_far"
        else:
            status = "upcoming_far"
    else:
        status = "idle"
        score = "\u2013"

    # Goal data (only present during live games)
    goals = live.get("goals", []) if is_playing else []
    last_goal = live.get("last_goal") if is_playing else None

    # Next match helpers
    next_dt = next_match.get("datetime_iso") if next_match else None
    next_today = False
    next_time = None
    if next_dt:
        try:
            dt = _datetime.fromisoformat(next_dt)
            now_sthlm = _datetime.now(_ZoneInfo("Europe/Stockholm"))
            next_today = dt.date() == now_sthlm.date()
            next_time = dt.strftime("%H:%M")
        except Exception:
            pass

    # Last match OT/SO detection: period_scores is '(0-1, 2-0, 0-1, 1-0)' on
    # swehockey.se — comma-separated inside ONE set of parens, NOT separate tokens.
    # >=4 entries means at least one OT period was played.
    last_period_scores = last_match.get("period_scores") if last_match else None
    last_went_ot = False
    if last_period_scores and last_period_scores.startswith("("):
        last_went_ot = last_period_scores.count(",") + 1 >= 4

    return {
        "status": status,
        "is_playing": is_playing,
        "score": score,
        "home_team": live.get("home_team") if is_playing else None,
        "away_team": live.get("away_team") if is_playing else None,
        "home_score": live.get("home_score") if is_playing else None,
        "away_score": live.get("away_score") if is_playing else None,
        "period": live.get("period") if is_playing else None,
        "period_label": live.get("period_label") if is_playing else None,
        "period_clock": live.get("period_clock") if is_playing else None,
        "is_overtime": live.get("is_overtime", False) if is_playing else False,
        "is_shootout": live.get("is_shootout", False) if is_playing else False,
        # Goal data — changes on every new goal → triggers automations
        "goals_count": len(goals),
        "last_goal_scorer": last_goal.get("scorer") if last_goal else None,
        "last_goal_team": last_goal.get("team") if last_goal else None,
        "last_goal_assists": last_goal.get("assists", []) if last_goal else [],
        "last_goal_period": last_goal.get("period") if last_goal else None,
        "last_goal_clock": last_goal.get("period_clock") if last_goal else None,
        "last_goal_situation": last_goal.get("situation") if last_goal else None,
        # Full goals list available as JSON attribute on the status sensor
        "goals": goals,
        # Next match
        "next_datetime": next_dt,
        "next_today": next_today,
        "next_time": next_time,
        "next_opponent": next_match.get("opponent") if next_match else None,
        "next_home_team": next_match.get("home_team") if next_match else None,
        "next_away_team": next_match.get("away_team") if next_match else None,
        # Last match
        "last_score": (
            f"{last_match['home_score']}\u2013{last_match['away_score']}"
            if last_match and last_match.get("home_score") is not None
            else None
        ),
        "last_date": last_match_date,
        "last_opponent": last_match.get("opponent") if last_match else None,
        "last_home_team": last_match.get("home_team") if last_match else None,
        "last_away_team": last_match.get("away_team") if last_match else None,
        "last_home_score": last_match.get("home_score") if last_match else None,
        "last_away_score": last_match.get("away_score") if last_match else None,
        "last_won": last_match.get("won") if last_match else None,
        "last_period_scores": last_period_scores,
        "last_went_ot": last_went_ot,
        "last_match_today": last_match_today,
    }


def _state_hash(state: dict) -> str:
    return hashlib.md5(
        json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# MQTTPublisher
# ---------------------------------------------------------------------------


class MQTTPublisher:
    def __init__(self, host: str, port: int, username: str, password: str):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._client = None
        self._connected = False
        self._state_hashes: dict[str, str] = {}
        self._goals_cache: dict = _load_goals_cache()  # persists goals after game ends

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Connect to the broker and start the network loop in a background thread."""
        print(
            f"[MQTT] Connecting to {self._host}:{self._port} "
            f"(user={self._username or 'anonymous'})...",
            flush=True,
        )
        try:
            import paho.mqtt.client as mqtt  # imported lazily so missing lib is non-fatal
            import paho.mqtt as _paho_root

            _ver = getattr(_paho_root, "__version__", "unknown")
            print(f"[MQTT] paho-mqtt found, version {_ver}", flush=True)

            # Support both paho 1.x (no CallbackAPIVersion) and 2.x
            try:
                client = mqtt.Client(
                    mqtt.CallbackAPIVersion.VERSION1, client_id="hockeylive-api"
                )
            except AttributeError:
                client = mqtt.Client(client_id="hockeylive-api")

            if self._username:
                client.username_pw_set(self._username, self._password)

            def on_connect(client, userdata, flags, rc):
                if rc == 0:
                    self._connected = True
                    print(f"[MQTT] Connected to {self._host}:{self._port}", flush=True)
                    logger.info("MQTT connected to %s:%s", self._host, self._port)
                else:
                    rc_messages = {
                        1: "incorrect protocol version",
                        2: "invalid client ID",
                        3: "broker unavailable",
                        4: "bad username or password",
                        5: "not authorised",
                    }
                    reason = rc_messages.get(rc, f"unknown rc={rc}")
                    print(f"[MQTT] ERROR: Connection refused – {reason}", flush=True)
                    logger.error("MQTT connect failed, rc=%s (%s)", rc, reason)

            def on_disconnect(client, userdata, rc):
                self._connected = False
                if rc != 0:
                    print(f"[MQTT] Disconnected unexpectedly: rc={rc}", flush=True)
                    logger.warning("MQTT disconnected unexpectedly: rc=%s", rc)

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.reconnect_delay_set(min_delay=5, max_delay=60)
            client.connect(self._host, self._port, keepalive=60)
            client.loop_start()
            self._client = client
            return True
        except ImportError:
            print(
                "[MQTT] ERROR: paho-mqtt is not installed – rebuild the add-on",
                flush=True,
            )
            logger.error("paho-mqtt not installed")
            return False
        except Exception as exc:
            print(f"[MQTT] ERROR: {exc}", flush=True)
            logger.error("MQTT connect error: %s", exc)
            return False

    def disconnect(self) -> None:
        """Mark all watches offline and close the connection."""
        if not self._client:
            return
        for watch_id in _load_entities():
            try:
                self._client.publish(_avail_topic(watch_id), "offline", retain=True)
            except Exception:
                pass
        self._client.loop_stop()
        self._client.disconnect()

    # ------------------------------------------------------------------
    # Internal publish helper
    # ------------------------------------------------------------------

    def _pub(self, topic: str, payload: str, retain: bool = True) -> None:
        if not self._client:
            return
        self._client.publish(topic, payload, retain=retain)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def publish_discovery(self, watch: dict) -> None:
        """
        Publish MQTT Discovery configs for all entities of a watch.
        HA will create the entities automatically; no YAML needed.
        """
        if not self._connected or not self._client:
            logger.debug("MQTT not connected – skipping discovery for %s", watch["id"])
            return

        watch_id = watch["id"]
        display_name = watch.get("name") or watch["team"]
        slug = watch_slug(watch)
        state_t = _state_topic(watch_id)
        avail_t = _avail_topic(watch_id)

        # --- Clear legacy / wrongly-named retained discovery topics ---
        # Old code without object_id made HA auto-generate entity_id as
        # "{device_slug}_{device_slug}_{suffix}" or "{slug}_{suffix}".
        # Publishing empty retained payload removes them from HA and the broker.
        # Keep ALL historical suffixes here – never remove, only add new ones.
        _LEGACY_SUFFIXES = [
            ("sensor", "status"),
            ("binary_sensor", "live"),
            ("sensor", "score"),
            ("sensor", "period"),
            ("sensor", "next_match"),
            ("sensor", "last_result"),
            ("sensor", "last_goal_scorer"),
            ("sensor", "goals_count"),
            ("sensor", "goals"),  # old name before → goals_count
            ("sensor", "last_match"),  # old name before → last_result
            ("binary_sensor", "overtime"),
        ]
        cleared = 0
        for comp, sfx in _LEGACY_SUFFIXES:
            for old_id in [
                f"{slug}_{slug}_{sfx}",          # double-slug (HA auto-gen without object_id)
                f"{slug}_{sfx}",                 # single-slug without hockeylive_ prefix
                f"hockeylive_{watch_id}_{sfx}",  # unique_id-based (very old format)
                f"hockeylive_{slug}_{sfx}",      # object_id topic, uid2_ era (v2.6-v2.7)
            ]:
                self._pub(f"{DISCOVERY_PREFIX}/{comp}/{old_id}/config", "")
                cleared += 1
        print(f"[MQTT] Legacy cleanup: sent {cleared} empty retained msgs for {slug}")
        logger.info("MQTT legacy cleanup: %d topics cleared for %s", cleared, slug)
        # -----------------------------------------------------------------

        device = {
            "identifiers": [f"hockeylive_{watch_id}"],
            # Prefix device name with "HockeyLive " so HA slugifies the device name
            # to "hockeylive_{slug}" — entity_ids become hockeylive_{slug}_{suffix}.
            "name": f"HockeyLive {display_name}",
            "manufacturer": "HockeyLive",
            "model": "Ice Hockey Watch",
        }

        # Helper: build the boilerplate fields shared by every entity
        def _base(suffix: str, component: str = "sensor") -> tuple[str, str, dict]:
            object_id = f"hockeylive_{slug}_{suffix}"
            cfg: dict = {
                # uid3_ prefix: forces new entity_id creation (uid2_ entries still exist
                # in HA registry mapped to wrong entity_ids from previous versions)
                "unique_id": f"uid3_{watch_id}_{suffix}",
                "object_id": object_id,
                "state_topic": state_t,
                "availability_topic": avail_t,
                "device": device,
            }
            return component, object_id, cfg

        # Define all entities for this watch
        entity_defs = [
            # Main status sensor (also carries ALL JSON attributes for automations)
            (
                *_base("status"),
                {
                    "name": "Status",
                    "value_template": "{{ value_json.status }}",
                    "icon": "mdi:hockey-sticks",
                    "json_attributes_topic": state_t,
                },
            ),
            # Binary sensor: is a game live right now?
            (
                *_base("live", "binary_sensor"),
                {
                    "name": "Live",
                    "value_template": "{{ value_json.is_playing | lower }}",
                    "payload_on": "true",
                    "payload_off": "false",
                    "device_class": "running",
                    "icon": "mdi:play-circle",
                },
            ),
            # Score  "2–1"
            (
                *_base("score"),
                {
                    "name": "Score",
                    "value_template": "{{ value_json.score }}",
                    "icon": "mdi:scoreboard",
                },
            ),
            # Period label  "Period 2" / "Övertid" / "Straffar"
            (
                *_base("period"),
                {
                    "name": "Period",
                    "value_template": "{{ value_json.period_label or '-' }}",
                    "icon": "mdi:timer",
                },
            ),
            # Next match datetime  "2026-04-15 19:00"
            (
                *_base("next_match"),
                {
                    "name": "Next Match",
                    "value_template": (
                        "{% if value_json.next_datetime %}"
                        "{{ value_json.next_datetime[:16] | replace('T', ' ') }}"
                        "{% else %}-{% endif %}"
                    ),
                    "icon": "mdi:calendar",
                },
            ),
            # Last result  "3–2"
            (
                *_base("last_result"),
                {
                    "name": "Last Result",
                    "value_template": "{{ value_json.last_score or '-' }}",
                    "icon": "mdi:history",
                },
            ),
            # Last goal scorer — changes value on every new goal → ideal automation trigger
            (
                *_base("last_goal_scorer"),
                {
                    "name": "Last Goal Scorer",
                    "value_template": "{{ value_json.last_goal_scorer or '-' }}",
                    "icon": "mdi:hockey-puck",
                },
            ),
            # Total goals in current game — integer that increases with each goal
            (
                *_base("goals_count"),
                {
                    "name": "Goals",
                    "value_template": "{{ value_json.goals_count }}",
                    "icon": "mdi:counter",
                    "state_class": "measurement",
                },
            ),
            # OT/SO binary sensor
            (
                *_base("overtime", "binary_sensor"),
                {
                    "name": "Overtime",
                    "value_template": (
                        "{{ (value_json.is_overtime or value_json.is_shootout) | lower }}"
                    ),
                    "payload_on": "true",
                    "payload_off": "false",
                    "icon": "mdi:clock-alert",
                },
            ),
        ]

        discovery_topics: list[str] = []
        for component, object_id, base_cfg, extra in entity_defs:
            topic = _discovery_topic(component, object_id)
            payload = {**base_cfg, **extra}
            self._pub(topic, json.dumps(payload, ensure_ascii=False))
            discovery_topics.append(topic)

        # Mark watch as offline first so HA entity transitions unavailable→new-state on online
        self._pub(avail_t, "offline")

        # Persist tracked discovery topics so we can clean them up on delete
        tracked = _load_entities()
        tracked[watch_id] = {"slug": slug, "discovery_topics": discovery_topics}
        _save_entities(tracked)

        print(
            f"[MQTT] Discovery published: {display_name} ({watch_id}) "
            f"slug={slug} → {len(discovery_topics)} entities",
            flush=True,
        )
        logger.info(
            "MQTT Discovery published: %s (%s) → %d entities",
            display_name,
            watch_id,
            len(discovery_topics),
        )

    # ------------------------------------------------------------------
    # State push
    # ------------------------------------------------------------------

    def publish_state(self, watch_id: str, status_payload: dict) -> bool:
        """
        Publish state for a watch if it has changed since last publish.
        Returns True if a message was actually sent.
        """
        if not self._connected or not self._client:
            return False

        state = _build_state(status_payload)

        # Cache goals while game is live; restore them for finished_today
        if state.get("status", "").startswith("live") and state.get("goals"):
            self._goals_cache[watch_id] = state["goals"]
            _save_goals_cache(self._goals_cache)
        if state.get("status") == "finished_today":
            cached = self._goals_cache.get(watch_id, [])
            if not cached:
                # Fallback: app.py fetched goals from Game/Events for finished match
                cached = status_payload.get("last_match", {}).get("goals", [])
                if cached:
                    self._goals_cache[watch_id] = cached
                    _save_goals_cache(self._goals_cache)
            state["goals"] = list(reversed(cached))
            state["goals_count"] = len(cached)

        h = _state_hash(state)

        if self._state_hashes.get(watch_id) == h:
            return False  # nothing changed

        self._state_hashes[watch_id] = h
        payload_json = json.dumps(state, ensure_ascii=False)
        self._pub(_state_topic(watch_id), payload_json)
        # Bring entity online AFTER state is in broker — ensures automations fire
        # with fresh attributes already in the retained payload (same topic)
        self._pub(_avail_topic(watch_id), "online")
        logger.info(
            "MQTT state: %s → status=%s score=%s",
            watch_id,
            state["status"],
            state["score"],
        )
        return True

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def remove_watch(self, watch_id: str) -> None:
        """
        Unpublish all Discovery topics for a watch.
        HA removes the entities from its registry automatically.
        """
        tracked = _load_entities()
        entry = tracked.pop(watch_id, None)
        if not entry:
            return

        if self._client and self._connected:
            for topic in entry.get("discovery_topics", []):
                # Empty payload = HA treats this as "remove this entity"
                self._pub(topic, "")
            self._pub(_avail_topic(watch_id), "offline")

        self._state_hashes.pop(watch_id, None)
        _save_entities(tracked)
        logger.info("MQTT entities removed for watch %s", watch_id)

    def publish_global_discovery(self, awtrix_prefix: str) -> None:
        """Publish MQTT discovery for the AWTRIX currentApp sensor.

        Creates sensor.awtrix_current_app in HA that mirrors the AWTRIX
        stats/currentApp MQTT topic.  Used by button automations to
        determine which hockey app is currently on screen.
        """
        if not self._connected or not self._client:
            return
        cfg = json.dumps({
            "name": "AWTRIX Current App",
            "unique_id": "awtrix_current_app",
            "state_topic": f"{awtrix_prefix}/stats/currentApp",
            "icon": "mdi:view-carousel",
            "entity_category": "diagnostic",
        })
        topic = _discovery_topic("sensor", "awtrix_current_app")
        self._pub(topic, cfg)
        logger.info("MQTT global discovery: awtrix_current_app sensor published")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create(cfg: dict) -> Optional[MQTTPublisher]:
    """
    Create an MQTTPublisher from config dict, or return None if mqtt_host
    is empty/absent (MQTT is disabled).
    """
    host = (cfg.get("mqtt_host") or "").strip()
    if not host:
        logger.info("MQTT not configured (mqtt_host is empty) – push disabled")
        return None
    return MQTTPublisher(
        host=host,
        port=int(cfg.get("mqtt_port") or 1883),
        username=cfg.get("mqtt_username") or "",
        password=cfg.get("mqtt_password") or "",
    )
MQTT_PY_EOF

# Embed generate_awtrix.py directly — always current, no cache issue
COPY <<'AWTRIX_PY_EOF' /app/generate_awtrix.py
#!/usr/bin/env python3
"""
generate_awtrix.py – Auto-generates HA automation YAML for AWTRIX Hockey Scoreboard.

Reads  : /data/options.json   (awtrix_prefix, per-watch awtrix / awtrix_icon flags)
         /data/watchlist.json  (watch IDs and names)
Writes : /config/packages/hockeylive_awtrix.yaml  (HA Package format)

Requires in configuration.yaml:
    homeassistant:
      packages: !include_dir_named packages/

Per watch (when awtrix: true) the following automations are created:
  1. No match today      – show team icon + date of next match
  2. Countdown           – match is today but not started, countdown in minutes
  3. Live scoreboard     – H letter | score | A letter + 5 period dots
  4. Goal notification   – 30-second notify with scorer / assists / situation
  5. Match finished      – final score + period dots coloured green/red
  6. Midnight clear      – removes the custom app at 00:00:30

Global (once per prefix):
  7. Track active app    – mirrors AWTRIX currentApp MQTT → input_text
  8. Middle button       – cycles through goal history when on a scoreboard
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Team abbreviations (canonical slug → 2-4 char string)
# ---------------------------------------------------------------------------
_TEAM_ABBR: dict[str, str] = {
    # SHL
    "brynas_if":        "BIF",
    "djurgardens_if":   "DIF",
    "frolunda_hc":      "FHC",
    "farjestad_bk":     "FBK",
    "hv71":             "HV",
    "leksands_if":      "LIF",
    "linkoping_hc":     "LHC",
    "lulea_hf":         "LHF",
    "malmo_redhawks":   "MAL",
    "rogle_bk":         "RBK",
    "skelleftea_aik":   "SAIK",
    "timra_ik":         "TIK",
    "vaxjo_lakers":     "VLH",
    "orebro_hk":        "ÖHK",
    # HockeyAllsvenskan
    "aik":              "AIK",
    "almtuna_is":       "AIS",
    "bik_karlskoga":    "BIK",
    "if_bjorkloven":    "IFB",
    "ik_oskarshamn":    "IKO",
    "modo_hockey":      "MODO",
    "mora_ik":          "MIK",
    "nybro_vikings_if": "NYB",
    "troja_ljungby":    "TRO",
    "sodertalje_sk":    "SSK",
    "vik_vasteras_hk":  "VIK",
    "kalmar_hc":        "KHC",
    "ostersunds_ik":    "ÖIK",
    "vimmerby_hockey":  "VHC",
}

# ---------------------------------------------------------------------------
# 3×5 pixel font (cols × rows). Each char = list of 5 ints, bit 2 = leftmost.
# ---------------------------------------------------------------------------
_FONT3X5: dict[str, list[int]] = {
    "A": [0b010, 0b101, 0b111, 0b101, 0b101],
    "B": [0b110, 0b101, 0b110, 0b101, 0b110],
    "C": [0b011, 0b100, 0b100, 0b100, 0b011],
    "D": [0b110, 0b101, 0b101, 0b101, 0b110],
    "E": [0b111, 0b100, 0b110, 0b100, 0b111],
    "F": [0b111, 0b100, 0b110, 0b100, 0b100],
    "G": [0b011, 0b100, 0b101, 0b101, 0b011],
    "H": [0b101, 0b101, 0b111, 0b101, 0b101],
    "I": [0b111, 0b010, 0b010, 0b010, 0b111],
    "J": [0b111, 0b001, 0b001, 0b101, 0b010],
    "K": [0b101, 0b101, 0b110, 0b101, 0b101],
    "L": [0b100, 0b100, 0b100, 0b100, 0b111],
    "M": [0b101, 0b111, 0b111, 0b101, 0b101],
    "N": [0b101, 0b111, 0b111, 0b101, 0b101],
    "O": [0b010, 0b101, 0b101, 0b101, 0b010],
    "P": [0b110, 0b101, 0b110, 0b100, 0b100],
    "Q": [0b010, 0b101, 0b101, 0b110, 0b011],
    "R": [0b110, 0b101, 0b110, 0b101, 0b101],
    "S": [0b011, 0b100, 0b010, 0b001, 0b110],
    "T": [0b111, 0b010, 0b010, 0b010, 0b010],
    "U": [0b101, 0b101, 0b101, 0b101, 0b011],
    "V": [0b101, 0b101, 0b101, 0b010, 0b010],
    "W": [0b101, 0b101, 0b111, 0b111, 0b101],
    "X": [0b101, 0b101, 0b010, 0b101, 0b101],
    "Y": [0b101, 0b101, 0b010, 0b010, 0b010],
    "Z": [0b111, 0b001, 0b010, 0b100, 0b111],
    "Ö": [0b010, 0b111, 0b101, 0b111, 0b010],
}


def _luminance(hex_color: str) -> float:
    """Relative luminance of a hex color string like '#rrggbb'."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _text_color_for(bg_hex: str) -> str:
    """Return '#000000' or '#ffffff' for best contrast against bg_hex."""
    return "#000000" if _luminance(bg_hex) > 0.179 else "#ffffff"


def _pixel_bg_color(x, y, colors, x_offset):
    """Return the logo color painted at pixel (x, y) for the given team."""
    if not colors:
        p, s, a = "#444444", "#888888", None
    else:
        p, s, a = colors
    lx = x - x_offset  # local coordinate within 0-7
    if a is None:
        return p if lx <= (7 - y) else s
    else:
        if lx <= min(7, 5 - y):
            return p
        elif lx <= min(7, 7 - y):
            return s
        else:
            return a or p


def _abbr_char_positions(abbr, x_off):
    """Return list of (char, px, py) anchor positions for the abbreviation."""
    n = len(abbr)
    if n == 2:
        anchors = [(x_off + 0, 0), (x_off + 5, 3)]
    elif n == 3:
        anchors = [(x_off + 0, 0), (x_off + 5, 0), (x_off + 2, 3)]
    else:  # 4
        anchors = [(x_off + 0, 0), (x_off + 4, 0), (x_off + 0, 3), (x_off + 4, 3)]
    return list(zip(abbr, anchors))


def _abbr_pixel_coords(abbr, x_off):
    """Return the set of (x, y) coordinates occupied by abbreviation pixels."""
    coords = set()
    for ch, (px, py) in _abbr_char_positions(abbr, x_off):
        rows = _FONT3X5.get(ch.upper(), _FONT3X5.get("X", [0]*5))
        for dy, row_bits in enumerate(rows):
            for dx in range(3):
                if row_bits & (0b100 >> dx):
                    coords.add((px + dx, py + dy))
    return coords


def _abbr_pixels(abbr, x_off, colors):
    """Return dp draw commands for abbr overlaid on an 8x8 logo at x_off."""
    parts = []
    for ch, (px, py) in _abbr_char_positions(abbr, x_off):
        rows = _FONT3X5.get(ch.upper(), _FONT3X5.get("X", [0]*5))
        for dy, row_bits in enumerate(rows):
            for dx in range(3):
                if row_bits & (0b100 >> dx):
                    bg = _pixel_bg_color(px + dx, py + dy, colors, x_off)
                    text_col = _text_color_for(bg)
                    parts.append(f'{{"dp":[{px+dx},{py+dy},"{text_col}"]}}')
    return parts


# Team colors – drawn directly via AWTRIX draw commands, no icon files needed.
# cols 0-2: primary | cols 3-4: secondary | cols 5-7: accent (or primary)
# ---------------------------------------------------------------------------
_TEAM_COLORS = {
    # SHL
    "brynas_if": ("#1e1e1e", "#ffffff", "#fecc03"),
    "brynas": ("#1e1e1e", "#ffffff", "#fecc03"),
    "djurgardens_if": ("#fbea05", "#db0d1a", "#1261ab"),
    "djurgardens": ("#fbea05", "#db0d1a", "#1261ab"),
    "farjestad_bk": ("#ffffff", "#008e4f", "#e5b843"),
    "farjestad": ("#ffffff", "#008e4f", "#e5b843"),
    "frolunda_hc": ("#ffffff", "#0e5840", None),
    "frolunda": ("#ffffff", "#0e5840", None),
    "hc_frolunda": ("#ffffff", "#0e5840", None),
    "hv71": ("#052e59", "#fdd410", None),
    "linkoping_hc": ("#052d5c", "#ffffff", "#b10c21"),
    "linkoping": ("#052d5c", "#ffffff", "#b10c21"),
    "lhc": ("#052d5c", "#ffffff", "#b10c21"),
    "lulea_hf": ("#1e1e1e", "#d10c12", "#fdcd01"),
    "lulea": ("#1e1e1e", "#d10c12", "#fdcd01"),
    "malmo_redhawks": ("#ffffff", "#bb0b2e", "#1e1e1e"),
    "malmo": ("#ffffff", "#bb0b2e", "#1e1e1e"),
    "modo_hockey": ("#cf1f2d", "#ffffff", "#2c5235"),
    "modo": ("#cf1f2d", "#ffffff", "#2c5235"),
    "rogle_bk": ("#ffffff", "#067b35", "#a4272a"),
    "rogle": ("#ffffff", "#067b35", "#a4272a"),
    "skelleftea_aik": ("#fbba14", "#1e1e1e", None),
    "skelleftea": ("#fbba14", "#1e1e1e", None),
    "saik": ("#fbba14", "#1e1e1e", None),
    "timra_ik": ("#e32f25", "#ffffff", "#104999"),
    "timra": ("#e32f25", "#ffffff", "#104999"),
    "orebro_hk": ("#e3032a", "#ffffff", None),
    "orebro": ("#e3032a", "#ffffff", None),
    "vaxjo_lakers": ("#052f5d", "#eb7229", "#ffffff"),
    "vaxjo": ("#052f5d", "#eb7229", "#ffffff"),
    # HockeyAllsvenskan
    "aik": ("#1e1e1e", "#FFD700", None),
    "almtuna_is": ("#CC0000", "#FFD700", "#ffffff"),
    "almtuna": ("#CC0000", "#FFD700", "#ffffff"),
    "bik_karlskoga": ("#0b2d74", "#ffffff", None),
    "karlskoga": ("#0b2d74", "#ffffff", None),
    "if_bjorkloven": ("#0b5640", "#ffffff", "#fdd003"),
    "bjorkloven": ("#0b5640", "#ffffff", "#fdd003"),
    "ik_oskarshamn": ("#0b2d74", "#c10230", "#ffffff"),
    "oskarshamn": ("#0b2d74", "#c10230", "#ffffff"),
    "karlskrona_hk": ("#003F7F", "#FFFFFF", None),
    "karlskrona": ("#003F7F", "#FFFFFF", None),
    "kristianstad_ik": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "kristianstad": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "leksands_if": ("#0d3579", "#ffffff", None),
    "leksand": ("#0d3579", "#ffffff", None),
    "lif": ("#0d3579", "#ffffff", None),
    "mora_ik": ("#e42313", "#fcda00", "#007d32"),
    "mora": ("#e42313", "#fcda00", "#007d32"),
    "nybro_vikings_if": ("#e73137", "#1e1e1e", "#fed68f"),
    "nybro": ("#e73137", "#1e1e1e", "#fed68f"),
    "tingsryd_aif": ("#CC0000", "#FFFFFF", None),
    "tingsryd": ("#CC0000", "#FFFFFF", None),
    "vik_vasteras_hk": ("#fdd200", "#1e1e1e", None),
    "vasteras": ("#fdd200", "#1e1e1e", None),
    "vastervik_ik": ("#007755", "#FFFFFF", None),
    "vastervik": ("#007755", "#FFFFFF", None),
    "sodertalje_sk": ("#1264b0", "#fddf00", "#d4b882"),
    "sodertalje": ("#1264b0", "#fddf00", "#d4b882"),
    "huddinge_ik": ("#CC0000", "#FFFFFF", None),
    "huddinge": ("#CC0000", "#FFFFFF", None),
    "kalmar_hc": ("#ac0e09", "#ffffff", "#f1da9e"),
    "kalmar": ("#ac0e09", "#ffffff", "#f1da9e"),
    "troja_ljungby": ("#dc2f34", "#ffffff", None),
    "troja": ("#dc2f34", "#ffffff", None),
    "vimmerby_hockey": ("#fddd01", "#1e1e1e", "#ffffff"),
    "vimmerby": ("#fddd01", "#1e1e1e", "#ffffff"),
    "ostersunds_ik": ("#fded00", "#006633", None),
    "ostersund": ("#fded00", "#006633", None),
}

_TRANSLIT = str.maketrans("åäöÅÄÖéüÜ", "aaoAAOeuu")


def _tslug(name):
    s = name.translate(_TRANSLIT).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _draw_from_colors(colors, x_offset=0, slug=""):
    if colors:
        p, s, a = colors
    else:
        p, s, a = "#444444", "#888888", None

    parts = []

    def seg(x0, y, x1, color):
        x0 += x_offset
        x1 += x_offset
        if x0 > x1:
            return
        if x0 == x1:
            parts.append(f'{{"dp":[{x0},{y},"{color}"]}}')
        else:
            parts.append(f'{{"dl":[{x0},{y},{x1},{y},"{color}"]}}')

    for r in range(8):
        if a is None:
            seg(0, r, min(7, 6 - r), p)
            seg(max(0, 7 - r), r, 7, s)
        else:
            seg(0, r, min(7, 5 - r), p)
            seg(max(0, 6 - r), r, min(7, 8 - r), s)
            seg(max(0, 9 - r), r, 7, a)

    return ",".join(parts)


def _team_draw_fragment(team_name):
    slug = _tslug(team_name)
    colors = _TEAM_COLORS.get(slug)
    if not colors:
        for k, v in _TEAM_COLORS.items():
            if slug.startswith(k) or k.startswith(slug):
                colors = v
                slug = k
                break
    return _draw_from_colors(colors, 0, slug)


def _jinja_awtrix_draw_header():
    l_entries = []
    r_entries = []
    for slug, colors in _TEAM_COLORS.items():
        l = _draw_from_colors(colors, 0, slug)
        r = _draw_from_colors(colors, 24, slug)
        l_entries.append("'" + slug + "':'" + l + "'")
        r_entries.append("'" + slug + "':'" + r + "'")
    fallback_l = _draw_from_colors(None, 0)
    fallback_r = _draw_from_colors(None, 24)
    ld = "{%- set _ld={" + ",".join(l_entries) + "} -%}"
    rd = "{%- set _rd={" + ",".join(r_entries) + "} -%}"
    ts = (
        "{%- macro _ts(n) -%}"
        "{{- n|lower"
        "|replace('å','a')|replace('ä','a')|replace('ö','o')"
        "|replace('é','e')|replace('ü','u')"
        "|replace(' ','_')|replace('-','_')|replace('.','') -}}"
        "{%- endmacro -%}"
    )
    fl = "{%- set _fl='" + fallback_l + "' -%}"
    fr = "{%- set _fr='" + fallback_r + "' -%}"
    return ld + rd + ts + fl + fr


OPTIONS = Path("/data/options.json")
WATCHLIST = Path("/data/watchlist.json")
OUT_FILE = Path("/config/packages/hockeylive_awtrix.yaml")


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def _sub(tmpl: str, **kw) -> str:
    """Replace __KEY__ placeholders; HA Jinja2 {{ }} are left untouched."""
    for k, v in kw.items():
        tmpl = tmpl.replace(f"__{k}__", str(v))
    return tmpl


# ---------------------------------------------------------------------------
# Automation YAML template – one block per watch.
# Uses __PLACEHOLDER__ style substitution (not Python f-strings) so that
# HA Jinja2 {{ }} expressions are left as literal strings.
# ---------------------------------------------------------------------------
_T = """\
# --- __NAME__ (watch_id: __WID__) ---

- alias: "AWTRIX __NAME__ - Ingen match idag"
  id: "awtrix___WID___no_match"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to:
        - "upcoming_far"
        - "idle"
    - platform: time_pattern
      minutes: "/5"
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: sensor.hockeylive___SLUG___status
      state:
        - "upcoming_far"
        - "idle"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {%- set day = (ndt[8:10]|int(0))|string if ndt else '' -%}
          {%- set dx = 26 if day|length == 1 else 24 -%}
          {%- if day -%}{"draw":[__TEAMDRAW__,{"dl":[13,1,16,1,"#FFFFFF"]},{"dp":[12,2,"#FFFFFF"]},{"dp":[17,2,"#FFFFFF"]},{"dp":[12,3,"#FFFFFF"]},{"dl":[14,3,15,3,"#FFFFFF"]},{"dp":[17,3,"#FFFFFF"]},{"dp":[12,4,"#FFFFFF"]},{"dp":[14,4,"#FFFFFF"]},{"dp":[17,4,"#FFFFFF"]},{"dp":[12,5,"#FFFFFF"]},{"dl":[14,5,17,5,"#FFFFFF"]},{"dp":[12,6,"#FFFFFF"]},{"dl":[13,7,16,7,"#FFFFFF"]},{"df":[23,0,9,2,"#CC0000"]},{"df":[23,2,9,6,"#FFFFFF"]},{"dt":[{{ dx }},2,"{{ day }}","#000000"]}],"noScroll":true,"duration":10,"lifetime":600}{%- else -%}{%- endif -%}

- alias: "AWTRIX __NAME__ - Nedrakning till match"
  id: "awtrix___WID___countdown"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to: "upcoming_countdown"
    - platform: time_pattern
      minutes: "/1"
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: sensor.hockeylive___SLUG___status
      state: "upcoming_countdown"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {%- set mins = [((as_timestamp(ndt) - now().timestamp()) / 60)|int, 0]|max if ndt else 0 -%}
          {%- set hrs = mins // 60 -%}
          {%- set tx = 9 if hrs >= 10 else 14 -%}
          {%- set cdtext = '%d:%02d'|format(hrs, mins % 60) -%}
          {"draw":[__TEAMDRAW__,{"dt":[{{ tx }},1,"{{ cdtext }}","#FFFFFF"]}],"noScroll":true,"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Prematch scoreboard"
  id: "awtrix___WID___prematch"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to: "upcoming_prematch"
    - platform: time_pattern
      minutes: "/1"
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: sensor.hockeylive___SLUG___status
      state: "upcoming_prematch"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set _ht = state_attr('sensor.hockeylive___SLUG___status','next_home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','next_away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"0-0","#FFFFFF"]},{"dp":[9,7,"#404040"]},{"dp":[12,7,"#404040"]},{"dp":[15,7,"#404040"]},{"dp":[18,7,"#404040"]},{"dp":[21,7,"#404040"]},{{ _ad }}],"noScroll":true,"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Live scoreboard"
  id: "awtrix___WID___live"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to:
        - "live_p1"
        - "live_p2"
        - "live_p3"
        - "live_ot"
        - "live_so"
        - "live"
    - platform: time_pattern
      minutes: "/1"
    - platform: homeassistant
      event: start
  condition:
    - condition: template
      value_template: >-
        {{ states('sensor.hockeylive___SLUG___status') in ['live_p1','live_p2','live_p3','live_ot','live_so','live'] }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set p = state_attr('sensor.hockeylive___SLUG___status','period') -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','home_score')|default(0)|int -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','away_score')|default(0)|int -%}
          {%- set _ht = state_attr('sensor.hockeylive___SLUG___status','home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {%- set p1c = '#1E3A5F' if p in ['P2','P3','OT','SO'] else '#FFD700' if p == 'P1' else '#404040' -%}
          {%- set p2c = '#1E3A5F' if p in ['P3','OT','SO'] else '#FFD700' if p == 'P2' else '#404040' -%}
          {%- set p3c = '#1E3A5F' if p in ['OT','SO'] else '#FFD700' if p == 'P3' else '#404040' -%}
          {%- set otc = '#1E3A5F' if p == 'SO' else '#FFD700' if p == 'OT' else '#404040' -%}
          {%- set soc = '#FFD700' if p == 'SO' else '#404040' -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"{{ hs }}-{{ as_ }}","#FFD700"]},{"dp":[9,7,"{{ p1c }}"]},{"dp":[12,7,"{{ p2c }}"]},{"dp":[15,7,"{{ p3c }}"]},{"dp":[18,7,"{{ otc }}"]},{"dp":[21,7,"{{ soc }}"]},{{ _ad }}],"noScroll":true,"lifetime":120}

- alias: "AWTRIX __NAME__ - Mal!"
  id: "awtrix___WID___goal"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___last_goal_scorer
  condition:
    - condition: template
      value_template: >-
        {{ states('sensor.hockeylive___SLUG___status') in ['live_p1','live_p2','live_p3','live_ot','live_so','live']
           and trigger.to_state.state not in ['unknown','unavailable','-'] }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/notify"
        payload: >-
          {%- set sc = trigger.to_state.state -%}
          {%- set team = state_attr('sensor.hockeylive___SLUG___status','last_goal_team') or '?' -%}
          {%- set ass = state_attr('sensor.hockeylive___SLUG___status','last_goal_assists') or [] -%}
          {%- set sit = state_attr('sensor.hockeylive___SLUG___status','last_goal_situation') or 'ES' -%}
          {%- set score = state_attr('sensor.hockeylive___SLUG___status','score') -%}
          {%- set sitc = 'FFD700' if sit == 'PP' else '00AAFF' if sit == 'SH' else 'FFFFFF' -%}
          {%- set own_goal = (team|lower|replace('å','a')|replace('ä','a')|replace('ö','o')|replace('é','e')|replace('ü','u')|replace(' ','_')|replace('-','_')|replace('.','')) == '__SLUG__' -%}
          {%- set rtttl = 'goal:d=8,o=5,b=200:c6,e6,g6,p,c6,e6,g6,c7,g6,c7' if own_goal else 'opp:d=8,o=5,b=140:c7,g6,e6,c6' -%}
          {"draw":[__TEAMDRAW__],"text":[{"t":"     MAL! ","c":"FFD700"},{"t":"{{ sc }}","c":"FFFFFF"},{"t":" {{ score }}","c":"FFD700"}{% if ass %},{"t":" Ass: {{ ass|join(', ') }}","c":"888888"}{% endif %}{% if sit not in ['ES',''] %},{"t":" ({{ sit }})","c":"{{ sitc }}"}{% endif %}],"rtttl":"{{ rtttl }}","duration":30,"stack":false,"wakeup":true}

- alias: "AWTRIX __NAME__ - Match slut"
  id: "awtrix___WID___finished"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to: "finished_today"
  condition:
    - condition: template
      value_template: >-
        {{ trigger.from_state is not none
           and trigger.from_state.state in ['live_p1','live_p2','live_p3','live_ot','live_so','live'] }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set won = state_attr('sensor.hockeylive___SLUG___status','last_won') -%}
          {%- set ls = state_attr('sensor.hockeylive___SLUG___status','last_score') or '-' -%}
          {%- set ot = state_attr('sensor.hockeylive___SLUG___status','last_went_ot') -%}
          {%- set dc = '#00C800' if won else '#C80000' -%}
          {%- set oc = dc if ot else '#404040' -%}
          {"draw":[__TEAMDRAW__,{"dp":[8,7,"{{ dc }}"]},{"dp":[13,7,"{{ dc }}"]},{"dp":[18,7,"{{ dc }}"]},{"dp":[23,7,"{{ oc }}"]},{"dp":[28,7,"#404040"]}],"text":[{"t":"{{ ls }} {{ '+' if won else 'x' }}","c":"{{ dc }}"}],"duration":30,"lifetime":3600}

- alias: "AWTRIX __NAME__ - Matchresultat idag"
  id: "awtrix___WID___result_today"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
      to: "finished_today"
    - platform: time_pattern
      minutes: "/5"
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: sensor.hockeylive___SLUG___status
      state: "finished_today"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set _ht = state_attr('sensor.hockeylive___SLUG___status','last_home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','last_away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','last_home_score')|int(0) -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','last_away_score')|int(0) -%}
          {%- set won = state_attr('sensor.hockeylive___SLUG___status','last_won') -%}
          {%- set ot = state_attr('sensor.hockeylive___SLUG___status','last_went_ot') -%}
          {%- set dc = '#00C800' if won else '#C80000' -%}
          {%- set oc = dc if ot else '#404040' -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"{{ hs }}-{{ as_ }}","{{ dc }}"]},{"dp":[9,7,"{{ dc }}"]},{"dp":[12,7,"{{ dc }}"]},{"dp":[15,7,"{{ dc }}"]},{"dp":[18,7,"{{ oc }}"]},{"dp":[21,7,"#404040"]},{{ _ad }}],"noScroll":true,"duration":10,"lifetime":1800}

- alias: "AWTRIX __NAME__ - Rensa vid midnatt"
  id: "awtrix___WID___midnight"
  trigger:
    - platform: time
      at: "00:00:30"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: ""
"""


# ---------------------------------------------------------------------------
# Global helpers – input_number / input_text written once per generated file.
# ---------------------------------------------------------------------------
_HELPERS_BLOCK = """\
input_number:
  awtrix_goal_idx:
    name: "AWTRIX Mål-index"
    min: 0
    max: 99
    step: 1
    initial: 0

input_text:
  awtrix_goal_app:
    name: "AWTRIX Mål app"
    max: 64
    initial: ""

"""

# ---------------------------------------------------------------------------
# Global automations – track current app + middle-button goal history browser.
# These are appended once (not per watch) and use __PREFIX__ substitution.
# ---------------------------------------------------------------------------
_BUTTON_AUTOMATIONS = """\
- alias: "AWTRIX - Blockera inbyggd knappnavigering"
  id: "awtrix_block_nav_keys"
  trigger:
    - platform: homeassistant
      event: start
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/settings"
        payload: '{"BLOCKN":true}'

- alias: "AWTRIX - Knapp: visa detaljer"
  id: "awtrix_button_details"
  mode: restart
  trigger:
    - platform: mqtt
      topic: "__PREFIX__/stats/buttonSelect"
      payload: "0"
  variables:
    current_app: "{{ states('sensor.awtrix_current_app') }}"
    last_app: "{{ states('input_text.awtrix_goal_app') }}"
    slug: "{{ (current_app[7:] if current_app.startswith('hockey_') else (last_app[7:] if last_app.startswith('hockey_') else '')) | trim }}"
    is_hockey: "{{ slug != '' }}"
    status: "{{ states('sensor.hockeylive_' ~ slug ~ '_status') if slug != '' else '' }}"
    is_upcoming: "{{ status in ['upcoming_far', 'upcoming_countdown', 'upcoming_prematch'] }}"
    goals: "{{ state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'goals') if slug != '' else [] }}"
    n: "{{ (goals or []) | length }}"
  condition:
    - "{{ is_hockey }}"
  action:
    - choose:
        - conditions:
            - "{{ is_upcoming }}"
          sequence:
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: >-
                  {%- set _nht = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_home_team') | default('') | string -%}
                  {%- set _nat = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_away_team') | default('') | string -%}
                  {%- set _ndt = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_datetime') | default('') | string -%}
                  {%- set _nt = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_time') | default('?') | string -%}
                  {%- set _months = ['Jan','Feb','Mar','Apr','Maj','Jun','Jul','Aug','Sep','Okt','Nov','Dec'] -%}
                  {%- if _ndt -%}
                  {%- set _d = _ndt[8:10] | int -%}
                  {%- set _m = _ndt[5:7] | int -%}
                  {%- set _suf = 'st' if _d in [1,21,31] else 'nd' if _d in [2,22] else 'rd' if _d in [3,23] else 'th' -%}
                  {%- set _date_str = (_d | string) ~ _suf ~ ' ' ~ _months[_m - 1] -%}
                  {%- else -%}
                  {%- set _date_str = '' -%}
                  {%- endif -%}
                  {"text":"{{ _nht }} - {{ _nat }}{% if _date_str %}, {{ _date_str }}{% endif %} {{ _nt }}","repeat":1,"stack":false}
      default:
        - service: input_text.set_value
          target:
            entity_id: input_text.awtrix_goal_app
          data:
            value: "{{ 'hockey_' ~ slug }}"
        - choose:
            - conditions:
                - "{{ (n | int) == 0 }}"
              sequence:
                - service: mqtt.publish
                  data:
                    topic: "__PREFIX__/notify"
                    payload: '{"text":"Inga m\u00e5l","duration":5,"stack":false}'
          default:
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: >-
                  {%- set goal_iter = goals | sort(attribute='game_time_secs') if status == 'finished_today' else goals | sort(attribute='game_time_secs', reverse=true) -%}
                  {%- set home_t = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'home_team') | default(state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'last_home_team')) | default('') | string -%}
                  {%- set ns = namespace(segs=[]) -%}
                  {%- for g in goal_iter -%}
                  {%- if not loop.first -%}{%- set ns.segs = ns.segs + ['{"t":"   ","c":"FFFFFF"}'] -%}{%- endif -%}
                  {%- set sc = g.scorer | default('?') -%}
                  {%- set hs = g.home_score_after | default(0) | int -%}
                  {%- set as_ = g.away_score_after | default(0) | int -%}
                  {%- set per = g.period | default('') -%}
                  {%- set clk = g.period_clock | default('') -%}
                  {%- set sit = g.situation | default('') -%}
                  {%- set sit_str = ' ' ~ sit if sit not in ['EQ','ES',''] else '' -%}
                  {%- set gt = g.team | default('') -%}
                  {%- if gt == home_t -%}
                  {%- set ns.segs = ns.segs + ['{"t":"' ~ hs ~ '-","c":"FFD700"}', '{"t":"' ~ as_ ~ ' ","c":"FFFFFF"}', '{"t":"' ~ sc ~ ' ' ~ per ~ ' ' ~ clk ~ sit_str ~ '","c":"FFFFFF"}'] -%}
                  {%- else -%}
                  {%- set ns.segs = ns.segs + ['{"t":"' ~ hs ~ '-","c":"FFFFFF"}', '{"t":"' ~ as_ ~ ' ","c":"FFD700"}', '{"t":"' ~ sc ~ ' ' ~ per ~ ' ' ~ clk ~ sit_str ~ '","c":"FFFFFF"}'] -%}
                  {%- endif -%}
                  {%- endfor -%}
                  {"text":[{{ ns.segs | join(',') }}],"duration":20,"stack":false}
"""


def main() -> None:
    if not OPTIONS.exists():
        print("[generate_awtrix] /data/options.json not found – skipping")
        return

    options = json.loads(OPTIONS.read_text(encoding="utf-8"))
    prefix = (options.get("awtrix_prefix") or "").strip()
    if not prefix:
        print("[generate_awtrix] awtrix_prefix not configured – skipping")
        return

    if not WATCHLIST.exists():
        print(
            "[generate_awtrix] watchlist.json not found – run generate_config.py first"
        )
        return

    raw = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    watches = (raw.get("watches") or raw) if isinstance(raw, dict) else {}

    # Map display name → options entry (for awtrix flag + icon)
    opt_map = {
        (w.get("name") or w.get("team", "")): w for w in options.get("watches", [])
    }

    enabled = []
    disabled = []  # watches with awtrix: false – need their app cleared
    for wid, w in watches.items():
        name = w.get("name") or w.get("team", "")
        ow = opt_map.get(name, {})
        if ow.get("awtrix"):
            enabled.append({**w, "awtrix_icon": (ow.get("awtrix_icon") or "").strip()})
        else:
            disabled.append({**w})

    if not enabled and not disabled:
        print("[generate_awtrix] No watches configured – skipping")
        return

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    awtrix_draw_hdr = _jinja_awtrix_draw_header()
    automation_blocks = []
    for w in enabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        teamdraw = _team_draw_fragment(name)
        automation_blocks.append(
            _sub(
                _T,
                NAME=name,
                WID=w["id"],
                SLUG=slug,
                TEAMDRAW=teamdraw,
                AWTRIX_DRAW_HDR=awtrix_draw_hdr,
                APP=f"hockey_{slug}",
                PREFIX=prefix,
            )
        )

    # Clear automations for disabled (awtrix: false) watches
    _T_CLEAR = """\
- alias: "AWTRIX __NAME__ - Rensa (inaktiverad)"
  id: "awtrix___WID___disabled_clear"
  trigger:
    - platform: homeassistant
      event: start
    - platform: time_pattern
      hours: "/1"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: ""
"""
    for w in disabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        automation_blocks.append(
            _sub(
                _T_CLEAR,
                NAME=name,
                WID=w["id"],
                APP=f"hockey_{slug}",
                PREFIX=prefix,
            )
        )

    # Append global button automations (once per prefix, not per watch)
    automation_blocks.append(_sub(_BUTTON_AUTOMATIONS, PREFIX=prefix, AWTRIX_DRAW_HDR=awtrix_draw_hdr))

    # Write as HA Package (works alongside any existing automation: !include setup)
    preamble = "\n".join(
        [
            "# Auto-generated by HockeyLive add-on – do not edit manually",
            "# Re-generated on every add-on restart",
            "#",
            "# Add to configuration.yaml:",
            "#   homeassistant:",
            "#     packages: !include_dir_named packages/",
            "",
        ]
    )
    # Indent each automation block so it becomes a YAML list under "automation:"
    indented = []
    for block in automation_blocks:
        for i, line in enumerate(block.splitlines()):
            if line.startswith("- alias:"):
                indented.append("  " + line)
            elif line.strip() == "":
                indented.append("")
            else:
                indented.append("  " + line)
        indented.append("")

    OUT_FILE.write_text(
        preamble + _HELPERS_BLOCK + "automation:\n" + "\n".join(indented),
        encoding="utf-8",
    )
    print(f"[generate_awtrix] {len(enabled)} enabled + {len(disabled)} disabled → {OUT_FILE}")
    print(
        f"[generate_awtrix] Prefix: {prefix}, Watches: {[w.get('name') or w['team'] for w in enabled]}"
    )
    # Auto-reload automations via Supervisor API (only available inside the add-on)
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if supervisor_token:
        import urllib.request, urllib.error
        req = urllib.request.Request(
            "http://supervisor/core/api/services/automation/reload",
            data=b"{}",
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {supervisor_token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[generate_awtrix] HA automations reloaded (HTTP {resp.status})")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"[generate_awtrix] Could not reload automations: {exc} — {body[:300]}")
        except Exception as exc:
            print(f"[generate_awtrix] Could not reload automations: {exc}")
    else:
        print(
            "[generate_awtrix] Reload automations in HA: Developer Tools → YAML → Reload Automations"
        )


if __name__ == "__main__":
    main()
AWTRIX_PY_EOF

# Embed generate_icons.py directly — always current, no cache issue
COPY <<'ICONS_PY_EOF' /app/generate_icons.py
#!/usr/bin/env python3
"""
generate_icons.py — Generate and upload 8×8 team icons to AWTRIX3.

Design: Jersey chest-stripe pattern (8px wide x 8px tall)
  rows 0-2 : primary color
  rows 3-4 : secondary color (chest stripe)
  row  5   : accent color if 3 colors, else primary
  rows 6-7 : primary color

Upload: POST multipart to http://{awtrix_host}/edit (AWTRIX3 LittleFS editor)
Icons saved to /data/icons/ regardless of upload.
"""
import io, json, re, sys, unicodedata
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    print("[generate_icons] Pillow not installed - skipping")
    sys.exit(0)

TEAM_COLORS = {
    # SHL
    "brynas_if":            ("#006633", "#FFD700", None),
    "brynas":               ("#006633", "#FFD700", None),
    "djurgardens_if":       ("#00297A", "#CE2029", "#FFD700"),
    "djurgardens":          ("#00297A", "#CE2029", "#FFD700"),
    "farjestad_bk":         ("#00542A", "#FFD700", None),
    "farjestad":            ("#00542A", "#FFD700", None),
    "frolunda_hc":          ("#6B1D2A", "#FFD700", None),
    "frolunda":             ("#6B1D2A", "#FFD700", None),
    "hc_frolunda":          ("#6B1D2A", "#FFD700", None),
    "hv71":                 ("#000000", "#FFD700", None),
    "linkoping_hc":         ("#003F6D", "#FFFFFF", None),
    "linkoping":            ("#003F6D", "#FFFFFF", None),
    "lulea_hf":             ("#CC0000", "#FFFFFF", "#004080"),
    "lulea":                ("#CC0000", "#FFFFFF", "#004080"),
    "malmo_redhawks":       ("#CC0000", "#000000", "#FFFFFF"),
    "malmo":                ("#CC0000", "#000000", "#FFFFFF"),
    "modo_hockey":          ("#CC0000", "#FFFFFF", None),
    "modo":                 ("#CC0000", "#FFFFFF", None),
    "rogle_bk":             ("#CC0000", "#FFFFFF", "#003F7F"),
    "rogle":                ("#CC0000", "#FFFFFF", "#003F7F"),
    "skelleftea_aik":       ("#FFD700", "#000000", None),
    "skelleftea":           ("#FFD700", "#000000", None),
    "timra_ik":             ("#CC0000", "#FFFFFF", "#000000"),
    "timra":                ("#CC0000", "#FFFFFF", "#000000"),
    "orebro_hk":            ("#001B50", "#FFFFFF", "#FFD700"),
    "orebro":               ("#001B50", "#FFFFFF", "#FFD700"),
    "vaxjo_lakers":         ("#1E5E3D", "#FFFFFF", "#CC0000"),
    "vaxjo":                ("#1E5E3D", "#FFFFFF", "#CC0000"),
    # HockeyAllsvenskan
    "aik":                  ("#000000", "#FFD700", None),
    "almtuna_is":           ("#CC0000", "#FFFFFF", None),
    "almtuna":              ("#CC0000", "#FFFFFF", None),
    "bik_karlskoga":        ("#003F7F", "#FFFFFF", None),
    "karlskoga":            ("#003F7F", "#FFFFFF", None),
    "if_bjorkloven":        ("#006633", "#FFFFFF", None),
    "bjorkloven":           ("#006633", "#FFFFFF", None),
    "ik_oskarshamn":        ("#003F7F", "#FFFFFF", "#CC0000"),
    "oskarshamn":           ("#003F7F", "#FFFFFF", "#CC0000"),
    "karlskrona_hk":        ("#003F7F", "#FFFFFF", None),
    "karlskrona":           ("#003F7F", "#FFFFFF", None),
    "kristianstad_ik":      ("#CC0000", "#FFFFFF", "#000000"),
    "kristianstad":         ("#CC0000", "#FFFFFF", "#000000"),
    "mora_ik":              ("#003F7F", "#FFD700", None),
    "mora":                 ("#003F7F", "#FFD700", None),
    "nybro_vikings_if":     ("#006633", "#CC0000", "#FFFFFF"),
    "nybro":                ("#006633", "#CC0000", "#FFFFFF"),
    "tingsryd_aif":         ("#CC0000", "#FFFFFF", None),
    "tingsryd":             ("#CC0000", "#FFFFFF", None),
    "vik_vasteras_hk":      ("#00388A", "#FFFFFF", "#FFD700"),
    "vasteras":             ("#00388A", "#FFFFFF", "#FFD700"),
    "vastervik_ik":         ("#007755", "#FFFFFF", None),
    "vastervik":            ("#007755", "#FFFFFF", None),
    "sodertalje_sk":        ("#003F7F", "#FFFFFF", "#CC0000"),
    "sodertalje":           ("#003F7F", "#FFFFFF", "#CC0000"),
}

_TRANSLIT = str.maketrans("aaoAAOeuu", "\xe5\xe4\xf6\xc5\xc4\xd6\xe9\xfc\xdc")

def team_slug(name):
    s = name.lower()
    for src, dst in [("\xe5","a"),("\xe4","a"),("\xf6","o"),("\u00e9","e"),("\xfc","u")]:
        s = s.replace(src, dst)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")

def make_icon_jpeg(primary, secondary, accent=None):
    p = tuple(int(primary.lstrip("#")[i:i+2], 16) for i in (0,2,4))
    s = tuple(int(secondary.lstrip("#")[i:i+2], 16) for i in (0,2,4))
    a = tuple(int(accent.lstrip("#")[i:i+2], 16) for i in (0,2,4)) if accent else None
    palette_colors = [p, s, a if a else p]
    flat = [c for rgb in palette_colors for c in rgb]
    flat += [0] * (768 - len(flat))
    img = Image.new("P", (8, 8))
    img.putpalette(flat)
    px = img.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 0
        px[3, y] = 1
        px[4, y] = 1
        if a:
            px[5, y] = 2
            px[6, y] = 2
            px[7, y] = 2
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()

def upload_icon(host, name, jpeg_bytes):
    import urllib.request
    boundary = "----HockeyLiveBoundary"
    remote_path = f"/ICONS/{name}.gif"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="data"; filename="{remote_path}"\r\n'
        f"Content-Type: image/gif\r\n\r\n"
    ).encode() + jpeg_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"http://{host}/edit", data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status < 300
    except Exception as exc:
        print(f"[generate_icons] Upload {name} failed: {exc}")
        return False

def main():
    opts_file = Path("/data/options.json")
    if not opts_file.exists():
        print("[generate_icons] /data/options.json not found - skipping")
        return
    with open(opts_file) as f:
        opts = json.load(f)
    awtrix_host = (opts.get("awtrix_host") or "").strip()
    out_dir = Path("/data/icons")
    out_dir.mkdir(parents=True, exist_ok=True)
    uploaded = skipped = saved = 0
    for slug, colors in TEAM_COLORS.items():
        try:
            gif = make_icon_jpeg(*colors)
            (out_dir / f"{slug}.gif").write_bytes(gif)
            saved += 1
            if awtrix_host:
                ok = upload_icon(awtrix_host, slug, gif)
                if ok:
                    uploaded += 1
                    print(f"[generate_icons] OK: {slug}")
                else:
                    skipped += 1
        except Exception as exc:
            print(f"[generate_icons] Error {slug}: {exc}")
            skipped += 1
    if awtrix_host:
        print(f"[generate_icons] {uploaded} uploaded, {skipped} failed, icons in {out_dir}")
    else:
        print(f"[generate_icons] {saved} icons saved to {out_dir} (set awtrix_host to upload)")

if __name__ == "__main__":
    main()
ICONS_PY_EOF

RUN chmod +x /app/run.sh

# /data is mounted by the Supervisor — config.yaml and watchlist.json live there
ENV HOCKEY_CONFIG=/data/config.yaml
ENV BUILD_ID=20260403_6200

EXPOSE 8080

CMD ["/app/run.sh"]
