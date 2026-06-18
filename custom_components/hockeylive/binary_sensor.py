"""
binary_sensor.py – Binary sensors for HockeyLive.

  hockeylive.<team>_is_live   True when team is currently playing
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
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
    async_add_entities([HockeyIsLiveSensor(coordinator, team, slug)])


class HockeyIsLiveSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HockeyLiveCoordinator,
        team: str,
        slug: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{slug}_is_live"
        self._attr_name = f"{team} – Spelar nu"
        self._attr_icon = "mdi:broadcast"

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return bool(self.coordinator.data.get("current", {}).get("is_live", False))

    @property
    def extra_state_attributes(self) -> dict:
        current = (self.coordinator.data or {}).get("current") or {}
        return {
            "home_team":    current.get("home_team"),
            "away_team":    current.get("away_team"),
            "period":       current.get("period"),
            "period_label": current.get("period_label"),
        }
