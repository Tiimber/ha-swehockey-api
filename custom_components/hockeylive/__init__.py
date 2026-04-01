"""
__init__.py – HockeyLive custom integration entry point.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import HockeyLiveCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a HockeyLive config entry (one per team)."""
    # Ensure the shared domain store exists (coordinator also calls this,
    # but we do it here so the schedules dict survives coordinator restarts).
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    hass.data[DOMAIN].setdefault("schedules", {})

    team_name  = entry.data["team"]
    season_ids = entry.data["season_ids"]

    coordinator = HockeyLiveCoordinator(
        hass,
        team=team_name,
        season_ids=season_ids,
        entry_id=entry.entry_id,
    )

    # Initial refresh – raises ConfigEntryNotReady on failure
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:
        raise ConfigEntryNotReady(f"Could not load data for {team_name}: {exc}") from exc

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove global schedules cache only if no more entries remain
        remaining = [k for k in hass.data[DOMAIN] if k != "schedules"]
        if not remaining:
            hass.data.pop(DOMAIN, None)
    return unload_ok
