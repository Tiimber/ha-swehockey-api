"""image.py – 32x32 PNG scoreboard image entity for HockeyLive."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HockeyLiveCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HockeyLiveCoordinator = hass.data[DOMAIN][entry.entry_id]
    team = entry.data["team"]
    slug = team.lower().replace(" ", "_")
    api_url = entry.data["api_url"]
    async_add_entities([HockeyScoreboardImage(coordinator, hass, team, slug, api_url)])


class HockeyScoreboardImage(CoordinatorEntity, ImageEntity):
    """32x32 PNG scoreboard – URL-based, refreshed on coordinator update."""

    _attr_content_type = "image/png"
    _attr_has_entity_name = True
    _attr_name = "Scoreboard 32x32"
    _attr_icon = "mdi:hockey-sticks"

    def __init__(
        self,
        coordinator: HockeyLiveCoordinator,
        hass: HomeAssistant,
        team: str,
        slug: str,
        api_url: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, hass)
        self._team = team
        self._slug = slug
        self._attr_unique_id = f"{slug}_scoreboard_png"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, slug)},
            name=team,
            manufacturer="swehockey.se",
            model="HockeyLive Team",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._attr_image_url = (
            f"{api_url.rstrip('/')}/team/{quote(team, safe='')}/png"
        )
        self._last_updated: datetime = datetime.now(timezone.utc)

    @property
    def image_last_updated(self) -> datetime:
        """Return last updated time – overrides cached_property to stay dynamic."""
        return self._last_updated

    def _handle_coordinator_update(self) -> None:
        """Bump timestamp on coordinator update so HA re-fetches the image."""
        self._last_updated = datetime.now(timezone.utc)
        self._cached_image = None
        self.async_write_ha_state()
