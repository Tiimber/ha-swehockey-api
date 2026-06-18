"""config_flow.py – HockeyLive team-first config flow using GET /leagues."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class HockeyLiveConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """4-step flow: api_url → pick_league → pick_sub (optional) → pick_team."""

    VERSION = 1

    def __init__(self) -> None:
        self._api_url: str = ""
        self._leagues: list[dict] = []
        self._selected_league: dict = {}
        self._selected_sub: dict = {}
        self._team: str = ""
        self._season_ids: list[int] = []

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> "HockeyLiveOptionsFlow":
        return HockeyLiveOptionsFlow(entry)

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
                    async with session.get(f"{api_url}/leagues") as resp:
                        if resp.status != 200:
                            errors["base"] = "cannot_connect"
                        else:
                            body = await resp.json()
                            leagues = body.get("leagues", [])
                            if not leagues:
                                errors["base"] = "no_leagues_found"
                            else:
                                self._api_url = api_url
                                self._leagues = leagues
                except Exception:
                    errors["base"] = "cannot_connect"

            if not errors:
                return await self.async_step_pick_league()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("api_url"): str}),
            errors=errors,
        )

    async def async_step_pick_league(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        league_options = {lg["league"]: lg["league"] for lg in self._leagues}

        if user_input is not None:
            selected_name = user_input["league"]
            self._selected_league = next(
                lg for lg in self._leagues if lg["league"] == selected_name
            )
            subs = self._selected_league.get("sub_competitions", [])
            if len(subs) <= 1:
                self._selected_sub = subs[0] if subs else {}
                return await self.async_step_pick_team()
            return await self.async_step_pick_sub()

        return self.async_show_form(
            step_id="pick_league",
            data_schema=vol.Schema({vol.Required("league"): vol.In(league_options)}),
            errors=errors,
        )

    async def async_step_pick_sub(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        subs = self._selected_league.get("sub_competitions", [])
        sub_options = {s["name"]: s["name"] for s in subs}

        if user_input is not None:
            selected_name = user_input["sub_competition"]
            self._selected_sub = next(s for s in subs if s["name"] == selected_name)
            return await self.async_step_pick_team()

        return self.async_show_form(
            step_id="pick_sub",
            data_schema=vol.Schema(
                {vol.Required("sub_competition"): vol.In(sub_options)}
            ),
            errors=errors,
        )

    async def async_step_pick_team(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        teams: list[str] = self._selected_sub.get("teams", [])

        if not teams:
            errors["base"] = "no_teams_found"
            return self.async_show_form(
                step_id="pick_team",
                data_schema=vol.Schema({vol.Required("team"): str}),
                errors=errors,
            )

        if user_input is not None:
            self._team = user_input["team"]
            if self._team == "demo":
                self._season_ids = [0]
            else:
                self._season_ids = [
                    sub["season_id"]
                    for sub in self._selected_league.get("sub_competitions", [])
                    if sub["season_id"] != 0
                ]

            session = async_get_clientsession(self.hass)
            try:
                async with session.post(
                    f"{self._api_url}/watch",
                    json={"team": self._team, "season_ids": self._season_ids},
                ) as resp:
                    body = await resp.json()
                    watch_id: str = body.get("id", "")
            except Exception as exc:
                _LOGGER.warning("Failed to register watch: %s", exc)
                watch_id = ""

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

        return self.async_show_form(
            step_id="pick_team",
            data_schema=vol.Schema({vol.Required("team"): vol.In(teams)}),
            errors=errors,
        )


class HockeyLiveOptionsFlow(config_entries.OptionsFlow):
    """Allow updating api_url after setup."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        current_url = self._entry.data.get("api_url", "")

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
                return self.async_create_entry(title="", data={"api_url": api_url})

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required("api_url", default=current_url): str}
            ),
            errors=errors,
        )
