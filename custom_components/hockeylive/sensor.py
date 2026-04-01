"""
sensor.py – Sensors for HockeyLive.

Sensors per configured team
----------------------------
  hockeylive.<team>_next_match       state = date-time string (ISO) | "–"
  hockeylive.<team>_last_result      state = "3–1" | "–"
  hockeylive.<team>_live_score       state = "2–1" | "–"
  hockeylive.<team>_period           state = "Period 2" | "Övertid" | "–"
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HockeyLiveCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HockeyLiveCoordinator = hass.data[DOMAIN][entry.entry_id]
    team = entry.data["team"]
    slug = team.lower().replace(" ", "_")

    async_add_entities(
        [
            HockeyNextMatchSensor(coordinator, team, slug),
            HockeyLastResultSensor(coordinator, team, slug),
            HockeyLiveScoreSensor(coordinator, team, slug),
            HockeyPeriodSensor(coordinator, team, slug),
        ]
    )


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class _HockeySensor(CoordinatorEntity, SensorEntity):
    def __init__(
        self, coordinator: HockeyLiveCoordinator, team: str, slug: str, key: str
    ) -> None:
        super().__init__(coordinator)
        self._team = team
        self._slug = slug
        self._attr_unique_id = f"{slug}_{key}"
        self._attr_has_entity_name = True


# ---------------------------------------------------------------------------
# Next match
# ---------------------------------------------------------------------------

class HockeyNextMatchSensor(_HockeySensor):
    def __init__(self, coordinator, team, slug):
        super().__init__(coordinator, team, slug, "next_match")
        self._attr_name = f"{team} – Nästa match"
        self._attr_icon = "mdi:hockey-sticks"

    @property
    def native_value(self) -> str:
        game = self.coordinator.data.get("next_match") if self.coordinator.data else None
        if not game:
            return "–"
        return game.get("datetime_iso") or "–"

    @property
    def extra_state_attributes(self) -> dict:
        game = self.coordinator.data.get("next_match") if self.coordinator.data else None
        if not game:
            return {}
        return {
            "opponent":    game.get("opponent"),
            "venue":       game.get("venue"),
            "is_home":     game.get("is_home_game"),
            "home_team":   game.get("home_team"),
            "away_team":   game.get("away_team"),
            "round":       game.get("round"),
        }


# ---------------------------------------------------------------------------
# Last result
# ---------------------------------------------------------------------------

class HockeyLastResultSensor(_HockeySensor):
    def __init__(self, coordinator, team, slug):
        super().__init__(coordinator, team, slug, "last_result")
        self._attr_name = f"{team} – Senaste resultat"
        self._attr_icon = "mdi:scoreboard"

    @property
    def native_value(self) -> str:
        game = self.coordinator.data.get("last_match") if self.coordinator.data else None
        if not game:
            return "–"
        sf = game.get("score_for")
        sa = game.get("score_against")
        if sf is None or sa is None:
            return "–"
        return f"{sf}–{sa}"

    @property
    def extra_state_attributes(self) -> dict:
        game = self.coordinator.data.get("last_match") if self.coordinator.data else None
        if not game:
            return {}
        return {
            "datetime":      game.get("datetime_iso"),
            "opponent":      game.get("opponent"),
            "venue":         game.get("venue"),
            "is_home":       game.get("is_home_game"),
            "home_team":     game.get("home_team"),
            "away_team":     game.get("away_team"),
            "home_score":    game.get("home_score"),
            "away_score":    game.get("away_score"),
            "score_for":     game.get("score_for"),
            "score_against": game.get("score_against"),
            "won":           game.get("won"),
            "overtime":      game.get("overtime"),
            "shootout":      game.get("shootout"),
            "round":         game.get("round"),
        }


# ---------------------------------------------------------------------------
# Live score
# ---------------------------------------------------------------------------

class HockeyLiveScoreSensor(_HockeySensor):
    def __init__(self, coordinator, team, slug):
        super().__init__(coordinator, team, slug, "live_score")
        self._attr_name = f"{team} – Live score"
        self._attr_icon = "mdi:hockey-puck"

    @property
    def native_value(self) -> str:
        live = self.coordinator.data.get("live") if self.coordinator.data else None
        if not live or not live.get("is_playing"):
            return "–"
        h = live.get("home_score", 0)
        a = live.get("away_score", 0)
        return f"{h}–{a}"

    @property
    def extra_state_attributes(self) -> dict:
        live = self.coordinator.data.get("live") if self.coordinator.data else None
        if not live or not live.get("is_playing"):
            return {}
        return {
            "home_team":    live.get("home_team"),
            "away_team":    live.get("away_team"),
            "home_score":   live.get("home_score"),
            "away_score":   live.get("away_score"),
            "period":       live.get("period"),
            "period_label": live.get("period_label"),
            "period_clock": live.get("period_clock"),
            "is_overtime":  live.get("is_overtime"),
            "is_shootout":  live.get("is_shootout"),
            "venue":        live.get("venue"),
        }


# ---------------------------------------------------------------------------
# Period
# ---------------------------------------------------------------------------

class HockeyPeriodSensor(_HockeySensor):
    def __init__(self, coordinator, team, slug):
        super().__init__(coordinator, team, slug, "period")
        self._attr_name = f"{team} – Period"
        self._attr_icon = "mdi:timer-outline"

    @property
    def native_value(self) -> str:
        live = self.coordinator.data.get("live") if self.coordinator.data else None
        if not live or not live.get("is_playing"):
            return "–"
        return live.get("period_label") or "–"
