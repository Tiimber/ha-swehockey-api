"""coordinator.py – HockeyLive DataUpdateCoordinator (API-backed)."""
from __future__ import annotations
import logging
from datetime import timedelta
from urllib.parse import quote
import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL_LIVE, UPDATE_INTERVAL_GAME_DAY, UPDATE_INTERVAL_IDLE

_LOGGER = logging.getLogger(__name__)


class HockeyLiveCoordinator(DataUpdateCoordinator):
    """One coordinator per configured team entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._api_url: str = entry.data["api_url"]
        self._team: str = entry.data["team"]
        super().__init__(
            hass,
            _LOGGER,
            name=f"HockeyLive – {self._team}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_IDLE),
        )

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        url = f"{self._api_url}/team/{quote(self._team, safe='')}/now"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"API returned HTTP {resp.status} for {url}")
                data = await resp.json()
        except UpdateFailed:
            raise
        except Exception as exc:
            raise UpdateFailed(f"Failed to reach API at {url}: {exc}") from exc

        current = data.get("current") or {}
        if current.get("is_live"):
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_LIVE)
        elif current.get("datetime"):
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_GAME_DAY)
        else:
            self.update_interval = timedelta(seconds=UPDATE_INTERVAL_IDLE)

        return data
