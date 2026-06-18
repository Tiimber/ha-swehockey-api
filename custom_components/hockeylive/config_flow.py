"""config_flow.py – HockeyLive team-first config flow: api_url → team → leagues."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class HockeyLiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """3-step flow: api_url → pick_team → pick_leagues."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_url: str = ""
        self._all_teams: list[str] = []
        self._team: str = ""
        self._competitions: list[dict] = []
        self._season_ids: list[int] = []

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> "HockeyLiveOptionsFlow":
        return HockeyLiveOptionsFlow(entry)

    # ------------------------------------------------------------------
    # Step 1: enter API URL
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_url = user_input.get("api_url", "").rstrip("/")
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(f"{api_url}/") as resp:
                    if resp.status != 200:
                        errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "cannot_connect"

            if not errors:
                try:
                    async with session.get(f"{api_url}/teams/all") as resp:
                        if resp.status != 200:
                            errors["base"] = "cannot_connect"
                        else:
                            body = await resp.json()
                            teams = body.get("teams", [])
                            if not teams:
                                errors["base"] = "no_teams_found"
                            else:
                                self._api_url = api_url
                                self._all_teams = teams
                except Exception:
                    errors["base"] = "cannot_connect"

            if not errors:
                return await self.async_step_pick_team()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("api_url"): str}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2: pick team from dropdown
    # ------------------------------------------------------------------
    async def async_step_pick_team(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._team = user_input["team"]
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{self._api_url}/team/{self._team}/leagues"
                ) as resp:
                    if resp.status != 200:
                        errors["base"] = "cannot_connect"
                    else:
                        body = await resp.json()
                        self._competitions = body.get("competitions", [])
                        if not self._competitions:
                            errors["base"] = "no_leagues_found"
            except Exception:
                errors["base"] = "cannot_connect"

            if not errors:
                return await self.async_step_pick_leagues()

        team_options = {t: t for t in self._all_teams}
        return self.async_show_form(
            step_id="pick_team",
            data_schema=vol.Schema({vol.Required("team"): vol.In(team_options)}),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3: multi-select competitions
    # ------------------------------------------------------------------
    async def async_step_pick_leagues(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_ids = [int(x) for x in user_input["competitions"]]
            self._season_ids = selected_ids if selected_ids else [0]

            session = async_get_clientsession(self.hass)
            watch_id = ""
            try:
                async with session.post(
                    f"{self._api_url}/watch",
                    json={"team": self._team, "season_ids": self._season_ids},
                ) as resp:
                    body = await resp.json()
                    watch_id = body.get("id", "")
            except Exception as exc:
                _LOGGER.warning("Failed to register watch: %s", exc)

            await self.async_set_unique_id(f"{self._api_url}_{self._team}")
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title=self._team,
                data={
                    "api_url": self._api_url,
                    "team": self._team,
                    "season_ids": self._season_ids,
                    "watch_id": watch_id,
                },
            )

        comp_options = [
            {
                "value": str(comp["season_id"]),
                "label": f"{comp['league']} – {comp['name']} ({comp['game_count']} games)",
            }
            for comp in self._competitions
        ]
        return self.async_show_form(
            step_id="pick_leagues",
            data_schema=vol.Schema(
                {
                    vol.Required("competitions"): SelectSelector(
                        SelectSelectorConfig(
                            options=comp_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors=errors,
        )


class HockeyLiveOptionsFlow(config_entries.OptionsFlow):
    """Re-select competitions for an existing entry."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._competitions: list[dict] = []

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Fetch current competitions and show multi-select."""
        api_url = self._entry.data.get("api_url", "")
        team = self._entry.data.get("team", "")
        current_ids = set(
            str(s) for s in (
                self._entry.options.get("season_ids")
                or self._entry.data.get("season_ids", [])
            )
        )

        if not self._competitions:
            session = async_get_clientsession(self.hass)
            try:
                async with session.get(f"{api_url}/team/{team}/leagues") as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        self._competitions = body.get("competitions", [])
            except Exception as exc:
                _LOGGER.warning("Options flow fetch failed: %s", exc)

        if user_input is not None:
            selected_ids = [int(x) for x in user_input["competitions"]]
            season_ids = selected_ids if selected_ids else [0]

            session = async_get_clientsession(self.hass)
            watch_id = self._entry.data.get("watch_id", "")
            try:
                async with session.post(
                    f"{api_url}/watch",
                    json={"team": team, "season_ids": season_ids},
                ) as resp:
                    body = await resp.json()
                    watch_id = body.get("id", watch_id)
            except Exception as exc:
                _LOGGER.warning("Options flow watch update failed: %s", exc)

            return self.async_create_entry(
                title="",
                data={"season_ids": season_ids, "watch_id": watch_id},
            )

        comp_options = [
            {
                "value": str(comp["season_id"]),
                "label": f"{comp['league']} – {comp['name']} ({comp['game_count']} games)",
            }
            for comp in self._competitions
        ]
        default_selected = [o["value"] for o in comp_options if o["value"] in current_ids]
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required("competitions", default=default_selected): SelectSelector(
                        SelectSelectorConfig(
                            options=comp_options,
                            multiple=True,
                            mode=SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
            errors={},
        )
