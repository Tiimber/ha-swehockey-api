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
ARG CACHEBUST=20260403_6100
ARG REPO_URL=https://github.com/Tiimber/ha-swehockey-api/archive/refs/heads/master.tar.gz
RUN echo "Cache bust: ${CACHEBUST}" \
    && echo "${CACHEBUST}" > /app/build_id \
    && curl -fsSL "${REPO_URL}" \
    | tar -xz --strip-components=1 \
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
                elif secs_to_match <= 86400:
                    status = "upcoming_countdown"  # 2h–24h: show countdown
                else:
                    status = "upcoming_far"        # >24h: show calendar day
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
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

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
          {%- set dx = 12 if day|length == 1 else 10 -%}
          {%- if day -%}{"icon":"__ICON__","draw":[{"df":[9,0,9,2,"#FF0000"]},{"df":[9,2,9,6,"#FFFFFF"]},{"dt":[{{ dx }},2,"{{ day }}","#000000"]}],"noScroll":true,"duration":10,"lifetime":120}{%- else -%}{"icon":"__ICON__","duration":10,"lifetime":120}{%- endif -%}

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
    - variables:
        mins_left: >
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {{ [((as_timestamp(ndt) - now().timestamp()) / 60)|int, 0]|max if ndt else 0 }}
        cdtext: >
          {%- set m = mins_left|int -%}
          {{ '%d:%02d'|format(m // 60, m % 60) }}
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {"icon":"__ICON__","text":[{"t":"{{ cdtext }}","c":"FFFFFF"}],"duration":10,"lifetime":120}

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
          {%- set hl = (state_attr('sensor.hockeylive___SLUG___status','next_home_team') or '?')[0]|upper -%}
          {%- set al = (state_attr('sensor.hockeylive___SLUG___status','next_away_team') or '?')[0]|upper -%}
          {"draw":[{"dt":[0,1,"{{ hl }}","#FFFFFF"]},{"dt":[9,1,"0-0","#FFD700"]},{"dt":[25,1,"{{ al }}","#FFFFFF"]},{"dp":[4,7,"#404040"]},{"dp":[10,7,"#404040"]},{"dp":[16,7,"#404040"]},{"dp":[22,7,"#404040"]},{"dp":[28,7,"#404040"]}],"noScroll":true,"duration":10,"lifetime":120}

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
          {%- set p = state_attr('sensor.hockeylive___SLUG___status','period') -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','home_score')|default(0)|int -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','away_score')|default(0)|int -%}
          {%- set hl = (state_attr('sensor.hockeylive___SLUG___status','home_team') or '?')[0]|upper -%}
          {%- set al = (state_attr('sensor.hockeylive___SLUG___status','away_team') or '?')[0]|upper -%}
          {%- set p1c = '#1E3A5F' if p in ['P2','P3','OT','SO'] else '#FFD700' if p == 'P1' else '#404040' -%}
          {%- set p2c = '#1E3A5F' if p in ['P3','OT','SO'] else '#FFD700' if p == 'P2' else '#404040' -%}
          {%- set p3c = '#1E3A5F' if p in ['OT','SO'] else '#FFD700' if p == 'P3' else '#404040' -%}
          {%- set otc = '#1E3A5F' if p == 'SO' else '#FFD700' if p == 'OT' else '#404040' -%}
          {%- set soc = '#FFD700' if p == 'SO' else '#404040' -%}
          {"draw":[{"dt":[0,1,"{{ hl }}","#FFFFFF"]},{"dt":[9,1,"{{ hs }}-{{ as_ }}","#FFD700"]},{"dt":[25,1,"{{ al }}","#FFFFFF"]},{"dp":[4,7,"{{ p1c }}"]},{"dp":[10,7,"{{ p2c }}"]},{"dp":[16,7,"{{ p3c }}"]},{"dp":[22,7,"{{ otc }}"]},{"dp":[28,7,"{{ soc }}"]}],"noScroll":true,"lifetime":120}

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
          {"icon":"{{ team[0]|upper }}","text":[{"t":"MAL! ","c":"FFD700"},{"t":"{{ sc }}","c":"FFFFFF"},{"t":" {{ score }}","c":"FFD700"}{% if ass %},{"t":" Ass: {{ ass|join(', ') }}","c":"888888"}{% endif %}{% if sit not in ['ES',''] %},{"t":" ({{ sit }})","c":"{{ sitc }}"}{% endif %}],"duration":30,"stack":false,"wakeup":true}

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
          {"icon":"__ICON__","text":[{"t":"{{ ls }}","c":"{{ dc }}"},{"t":" {{ '+' if won else 'x' }}","c":"{{ dc }}"}],"draw":[{"dp":[8,7,"{{ dc }}"]},{"dp":[13,7,"{{ dc }}"]},{"dp":[18,7,"{{ dc }}"]},{"dp":[23,7,"{{ oc }}"]},{"dp":[28,7,"#404040"]}],"duration":30,"lifetime":3600}

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
          {%- set hl = (state_attr('sensor.hockeylive___SLUG___status','last_home_team') or '?')[0]|upper -%}
          {%- set al = (state_attr('sensor.hockeylive___SLUG___status','last_away_team') or '?')[0]|upper -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','last_home_score')|int(0) -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','last_away_score')|int(0) -%}
          {%- set won = state_attr('sensor.hockeylive___SLUG___status','last_won') -%}
          {%- set ot = state_attr('sensor.hockeylive___SLUG___status','last_went_ot') -%}
          {%- set dc = '#00C800' if won else '#C80000' -%}
          {%- set oc = dc if ot else '#404040' -%}
          {"draw":[{"dt":[0,1,"{{ hl }}","#FFFFFF"]},{"dt":[9,1,"{{ hs }}-{{ as_ }}","{{ dc }}"]},{"dt":[25,1,"{{ al }}","#FFFFFF"]},{"dp":[4,7,"{{ dc }}"]},{"dp":[10,7,"{{ dc }}"]},{"dp":[16,7,"{{ dc }}"]},{"dp":[22,7,"{{ oc }}"]},{"dp":[28,7,"#404040"]}],"noScroll":true,"duration":10,"lifetime":1800}

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

    automation_blocks = []
    for w in enabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        icon = w.get("awtrix_icon") or name[0].upper()
        automation_blocks.append(
            _sub(
                _T,
                NAME=name,
                WID=w["id"],
                SLUG=slug,
                ICON=icon,
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

    # Write as HA Package (works alongside any existing automation: !include setup)
    header = "\n".join(
        [
            "# Auto-generated by HockeyLive add-on – do not edit manually",
            "# Re-generated on every add-on restart",
            "#",
            "# Add to configuration.yaml:",
            "#   homeassistant:",
            "#     packages: !include_dir_named packages/",
            "",
            "automation:",
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

    OUT_FILE.write_text(header + "\n" + "\n".join(indented), encoding="utf-8")
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

RUN chmod +x /app/run.sh

# /data is mounted by the Supervisor — config.yaml and watchlist.json live there
ENV HOCKEY_CONFIG=/data/config.yaml
ENV BUILD_ID=20260403_6100

EXPOSE 8080

CMD ["/app/run.sh"]
