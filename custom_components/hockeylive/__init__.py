"""__init__.py – HockeyLive custom integration entry point."""
from __future__ import annotations
import logging
import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .coordinator import HockeyLiveCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["sensor", "binary_sensor", "image"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}

    coordinator = HockeyLiveCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        raise ConfigEntryNotReady(f"Could not load data for {entry.data['team']}: {exc}") from exc

    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        watch_id = entry.data.get("watch_id")
        api_url = entry.data.get("api_url")
        if watch_id and api_url:
            try:
                session = async_get_clientsession(hass)
                async with session.delete(f"{api_url}/watch/{watch_id}") as resp:
                    if resp.status not in (200, 204, 404):
                        _LOGGER.warning("Failed to unsubscribe watch %s: HTTP %s", watch_id, resp.status)
            except Exception as exc:
                _LOGGER.warning("Could not reach API to unsubscribe watch %s: %s", watch_id, exc)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
    return unload_ok
