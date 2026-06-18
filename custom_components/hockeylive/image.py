"""image.py – 32x32 PNG scoreboard image entity for HockeyLive."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    async_add_entities([HockeyScoreboardImage(coordinator, team, slug, api_url)])


class HockeyScoreboardImage(CoordinatorEntity, ImageEntity):
    """32x32 PNG scoreboard image, fetched from the API on every coordinator update."""

    _attr_content_type = "image/png"

    def __init__(
        self,
        coordinator: HockeyLiveCoordinator,
        team: str,
        slug: str,
        api_url: str,
    ) -> None:
        CoordinatorEntity.__init__(self, coordinator)
        ImageEntity.__init__(self, coordinator.hass)
        self._team = team
        self._slug = slug
        self._api_url = api_url.rstrip("/")
        self._attr_unique_id = f"{slug}_scoreboard_png"
        self._attr_name = "Scoreboard 32x32"
        self._attr_has_entity_name = True
        self._attr_icon = "mdi:hockey-sticks"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, slug)},
            name=team,
            manufacturer="swehockey.se",
            model="HockeyLive Team",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._cached_image: bytes | None = None
        self._image_last_updated: datetime = datetime.now(timezone.utc)

    @property
    def image_last_updated(self) -> datetime:
        return self._image_last_updated

    async def async_image(self) -> bytes | None:
        """Fetch the PNG from the API and return raw bytes."""
        url = f"{self._api_url}/team/{quote(self._team, safe='')}/png"
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    self._cached_image = data
                    self._image_last_updated = datetime.now(timezone.utc)
                    return data
                _LOGGER.warning("PNG endpoint returned HTTP %s for %s", resp.status, url)
        except Exception as exc:
            _LOGGER.warning("Failed to fetch scoreboard PNG from %s: %s", url, exc)
        return self._cached_image

    def _handle_coordinator_update(self) -> None:
        """Mark image as stale on every coordinator update so HA re-fetches it."""
        self._image_last_updated = datetime.now(timezone.utc)
        self.async_write_ha_state()
