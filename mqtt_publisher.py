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

    if is_playing:
        status = "live"
        score = f"{live.get('home_score', 0)}\u2013{live.get('away_score', 0)}"
    elif next_match:
        status = "upcoming"
        score = "\u2013"
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

    # Last match OT/SO detection from period_scores string e.g. "(2-0)(1-2)(0-1)(1-0)"
    last_period_scores = last_match.get("period_scores") if last_match else None
    last_went_ot = False
    if last_period_scores:
        last_went_ot = len(re.findall(r"\(\d+-\d+\)", last_period_scores)) >= 4

    # Last match today — needed to keep the result on Ulanzi for rest of day
    last_match_date = last_match.get("date") if last_match else None
    last_match_today = False
    if last_match_date:
        try:
            now_sthlm = _datetime.now(_ZoneInfo("Europe/Stockholm"))
            last_match_today = last_match_date == now_sthlm.strftime("%Y-%m-%d")
        except Exception:
            pass

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
                f"{slug}_{slug}_{sfx}",  # double-slug (HA auto-gen without object_id)
                f"{slug}_{sfx}",  # single-slug without hockeylive_ prefix
                f"hockeylive_{watch_id}_{sfx}",  # unique_id-based (very old format)
                f"hockeylive_{slug}_{sfx}",  # object_id topic, uid2_ era (v2.6-v2.7)
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

        # Mark watch as online
        self._pub(avail_t, "online")

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
        self._pub(
            _state_topic(watch_id),
            json.dumps(state, ensure_ascii=False),
        )
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
