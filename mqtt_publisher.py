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
from pathlib import Path
from typing import Optional

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
        score = f"{live.get('home_score', 0)}\u20131{live.get('away_score', 0)}"
        # Use proper en-dash
        score = f"{live.get('home_score', 0)}–{live.get('away_score', 0)}"
    elif next_match:
        status = "upcoming"
        score = "–"
    else:
        status = "idle"
        score = "–"

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
        "next_datetime": next_match["datetime_iso"] if next_match else None,
        "next_opponent": next_match["opponent"] if next_match else None,
        "next_home_team": next_match["home_team"] if next_match else None,
        "next_away_team": next_match["away_team"] if next_match else None,
        "last_score": (
            f"{last_match['home_score']}–{last_match['away_score']}"
            if last_match and last_match.get("home_score") is not None
            else None
        ),
        "last_opponent": last_match.get("opponent") if last_match else None,
        "last_won": last_match.get("won") if last_match else None,
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
        try:
            import paho.mqtt.client as mqtt  # imported lazily so missing lib is non-fatal

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
                    logger.info("MQTT connected to %s:%s", self._host, self._port)
                else:
                    logger.error("MQTT connect failed, rc=%s", rc)

            def on_disconnect(client, userdata, rc):
                self._connected = False
                if rc != 0:
                    logger.warning("MQTT disconnected unexpectedly: rc=%s", rc)

            client.on_connect = on_connect
            client.on_disconnect = on_disconnect
            client.reconnect_delay_set(min_delay=5, max_delay=60)
            client.connect(self._host, self._port, keepalive=60)
            client.loop_start()
            self._client = client
            return True
        except Exception as exc:
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

        device = {
            "identifiers": [f"hockeylive_{watch_id}"],
            "name": display_name,
            "manufacturer": "HockeyLive",
            "model": "Ice Hockey Watch",
        }

        # Helper: build the boilerplate fields shared by every entity
        def _base(suffix: str, component: str = "sensor") -> tuple[str, str, dict]:
            object_id = f"hockeylive_{slug}_{suffix}"
            cfg: dict = {
                "unique_id": f"hockeylive_{watch_id}_{suffix}",
                "object_id": object_id,
                "state_topic": state_t,
                "availability_topic": avail_t,
                "device": device,
            }
            return component, object_id, cfg

        # Define all entities for this watch
        entity_defs = [
            # Main status sensor (also carries JSON attributes for automations)
            (
                *_base("status"),
                {
                    "name": f"{display_name} Status",
                    "value_template": "{{ value_json.status }}",
                    "icon": "mdi:hockey-sticks",
                    "json_attributes_topic": state_t,
                },
            ),
            # Binary sensor: is a game live right now?
            (
                *_base("live", "binary_sensor"),
                {
                    "name": f"{display_name} Live",
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
                    "name": f"{display_name} Score",
                    "value_template": "{{ value_json.score }}",
                    "icon": "mdi:scoreboard",
                },
            ),
            # Period label  "Period 2"
            (
                *_base("period"),
                {
                    "name": f"{display_name} Period",
                    "value_template": "{{ value_json.period_label or '\u2013' }}",
                    "icon": "mdi:timer",
                },
            ),
            # Next match datetime  "2026-04-15 19:00"
            (
                *_base("next_match"),
                {
                    "name": f"{display_name} Next Match",
                    "value_template": (
                        "{% if value_json.next_datetime %}"
                        "{{ value_json.next_datetime[:16] | replace('T', ' ') }}"
                        "{% else %}\u2013{% endif %}"
                    ),
                    "icon": "mdi:calendar",
                },
            ),
            # Last result  "3–2"
            (
                *_base("last_result"),
                {
                    "name": f"{display_name} Last Result",
                    "value_template": "{{ value_json.last_score or '\u2013' }}",
                    "icon": "mdi:history",
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
