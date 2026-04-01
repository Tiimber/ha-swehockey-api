"""
config_flow.py – UI-based configuration for HockeyLive.

Flow
----
Step 1 (user):    Enter season IDs (comma-separated integers).
                  → Integration fetches available teams from those seasons.
Step 2 (confirm): User picks their team from a dropdown.
                  → Entry saved.

Re-auth / options: not needed (season IDs rarely change mid-season).
"""

from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN
from . import scraper

_LOGGER = logging.getLogger(__name__)


def _parse_season_ids(raw: str) -> list[int]:
    """Parse a comma-separated list of integers from user input."""
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return [int(p) for p in parts]


class HockeyLiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HockeyLive."""

    VERSION = 1

    def __init__(self) -> None:
        self._season_ids: list[int] = []
        self._available_teams: list[str] = []
        self._errors: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Step 1 – enter season IDs."""
        self._errors = {}

        if user_input is not None:
            raw_ids = user_input.get("season_ids", "")
            try:
                season_ids = _parse_season_ids(raw_ids)
                if not season_ids:
                    raise ValueError("empty")
            except (ValueError, TypeError):
                self._errors["season_ids"] = "invalid_season_ids"
            else:
                # Fetch team list from swehockey.se (executor)
                try:
                    teams: list[str] = []
                    for sid in season_ids:
                        found = await self.hass.async_add_executor_job(
                            scraper.list_teams_in_season, sid
                        )
                        teams.extend(t for t in found if t not in teams)
                    teams.sort()
                except Exception as exc:
                    _LOGGER.exception("Failed to fetch teams: %s", exc)
                    self._errors["base"] = "cannot_connect"
                else:
                    if not teams:
                        self._errors["season_ids"] = "no_teams_found"
                    else:
                        self._season_ids = season_ids
                        self._available_teams = teams
                        return await self.async_step_pick_team()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "season_ids",
                        description={"suggested_value": "18263, 19791"},
                    ): str,
                }
            ),
            errors=self._errors,
        )

    async def async_step_pick_team(
        self, user_input: dict | None = None
    ) -> FlowResult:
        """Step 2 – choose team from the discovered list."""
        self._errors = {}

        if user_input is not None:
            team = user_input["team"]

            # Prevent duplicate entries for the same team+seasons combo
            await self.async_set_unique_id(
                f"{team}_{'-'.join(str(s) for s in sorted(self._season_ids))}"
            )
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=team,
                data={
                    "team": team,
                    "season_ids": self._season_ids,
                },
            )

        return self.async_show_form(
            step_id="pick_team",
            data_schema=vol.Schema(
                {
                    vol.Required("team"): vol.In(self._available_teams),
                }
            ),
            description_placeholders={
                "season_ids": ", ".join(str(s) for s in self._season_ids),
                "team_count": str(len(self._available_teams)),
            },
            errors=self._errors,
        )
