"""
coordinator.py

One DataUpdateCoordinator per configured team entry.

Shared schedule cache
---------------------
Schedule pages (one per season_id) are expensive to fetch and identical for
all teams in the same league.  We store them in hass.data[DOMAIN]["schedules"]
keyed by season_id so that a second team in the same league re-uses the already-
fetched data instead of making duplicate HTTP requests.

Structure of hass.data[DOMAIN]:
    {
        "schedules": {
            <season_id>: {
                "games":      list[dict],
                "fetched_at": datetime,
            },
            ...
        }
    }
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL_LIVE, UPDATE_INTERVAL_GAME_DAY, UPDATE_INTERVAL_IDLE, LIVE_WINDOW_SECONDS
from . import scraper

_LOGGER = logging.getLogger(__name__)
STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

# How long a cached schedule page is considered fresh (seconds)
SCHEDULE_CACHE_TTL_NORMAL = 300   # 5 min
SCHEDULE_CACHE_TTL_LIVE   = 30    # 30 sec when a game may be in progress


def _now() -> datetime:
    return datetime.now(STOCKHOLM_TZ)


def _ensure_domain_store(hass: HomeAssistant) -> None:
    """Create the shared domain store if it doesn't exist yet."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    if "schedules" not in hass.data[DOMAIN]:
        hass.data[DOMAIN]["schedules"] = {}


def _schedule_cache_stale(hass: HomeAssistant, season_id: int, live_mode: bool) -> bool:
    cache = hass.data[DOMAIN]["schedules"].get(season_id)
    if not cache:
        return True
    ttl = SCHEDULE_CACHE_TTL_LIVE if live_mode else SCHEDULE_CACHE_TTL_NORMAL
    return (_now() - cache["fetched_at"]).total_seconds() > ttl


def _get_cached_games(hass: HomeAssistant, season_id: int) -> list[dict]:
    cache = hass.data[DOMAIN]["schedules"].get(season_id)
    return cache["games"] if cache else []


def _store_schedule(hass: HomeAssistant, season_id: int, games: list[dict]) -> None:
    hass.data[DOMAIN]["schedules"][season_id] = {
        "games": games,
        "fetched_at": _now(),
    }


class HockeyLiveCoordinator(DataUpdateCoordinator):
    """
    Coordinator for one configured team.

    `data` after a successful update:
    {
        "team":        str,
        "live":        dict   (is_playing, period, period_label, …),
        "next_match":  dict | None,
        "last_match":  dict | None,
        "all_games":   list[dict],
    }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        team: str,
        season_ids: list[int],
        entry_id: str,
    ) -> None:
        self._team = team
        self._season_ids = season_ids
        self._entry_id = entry_id

        super().__init__(
            hass,
            _LOGGER,
            name=f"HockeyLive – {team}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_IDLE),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_live(self, game: dict) -> bool:
        dt = game.get("datetime")
        if not dt:
            return False
        delta = (_now() - dt).total_seconds()
        return 0 < delta < LIVE_WINDOW_SECONDS and not game["is_completed"]

    def _game_extra(self, game: dict) -> dict:
        """Add team-perspective fields to a game dict."""
        team_lower = self._team.lower()
        is_home = (game["home_team"] or "").lower() == team_lower
        opponent = game["away_team"] if is_home else game["home_team"]
        score_for     = game["home_score"] if is_home else game["away_score"]
        score_against = game["away_score"] if is_home else game["home_score"]
        won: bool | None = None
        if score_for is not None and score_against is not None:
            won = score_for > score_against
        dt_iso = game["datetime"].isoformat() if game["datetime"] else None
        return {
            **game,
            "datetime": None,           # not JSON-serialisable; use datetime_iso
            "datetime_iso": dt_iso,
            "is_home_game": is_home,
            "opponent":     opponent,
            "score_for":    score_for,
            "score_against": score_against,
            "won":          won,
            "is_live":      self._is_live(game),
        }

    def _live_detail(self, game: dict) -> dict:
        """Fetch period/clock detail for a live game."""
        detail: dict = {
            "is_playing":   True,
            "home_team":    game["home_team"],
            "away_team":    game["away_team"],
            "home_score":   game["home_score"] or 0,
            "away_score":   game["away_score"] or 0,
            "venue":        game["venue"],
            "period":       None,
            "period_label": None,
            "period_clock": None,
            "is_overtime":  False,
            "is_shootout":  False,
        }

        if game.get("game_id"):
            ev = scraper.fetch_game_events(game["game_id"])
            if ev and ev.get("period"):
                detail["home_score"]   = ev.get("home_score", detail["home_score"])
                detail["away_score"]   = ev.get("away_score", detail["away_score"])
                detail["period"]       = ev["period"]
                detail["period_clock"] = ev.get("period_clock")
                detail["is_overtime"]  = ev.get("is_overtime", False)
                detail["is_shootout"]  = ev.get("is_shootout", False)
                return detail

        # Fallback: estimate period from elapsed real time
        elapsed = (_now() - game["datetime"]).total_seconds() / 60
        if elapsed < 25:
            period = "P1"
        elif elapsed < 42:
            period = "P1"
        elif elapsed < 67:
            period = "P2"
        elif elapsed < 84:
            period = "P2"
        elif elapsed < 109:
            period = "P3"
        elif elapsed < 125:
            period = "OT"
        else:
            period = "SO"
        detail["period"] = period
        detail["is_overtime"]  = period == "OT"
        detail["is_shootout"]  = period == "SO"

        detail["period_label"] = {
            "P1": "Period 1", "P2": "Period 2", "P3": "Period 3",
            "OT": "Övertid",  "SO": "Straffar",
        }.get(period, period)

        return detail

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        _ensure_domain_store(self.hass)

        # Determine if we may have a live game (based on last known data)
        live_mode = False
        if self.data:
            live_mode = self.data.get("live", {}).get("is_playing", False)

        # Fetch / reuse schedule pages (shared across all teams)
        all_games: list[dict] = []
        for sid in self._season_ids:
            if _schedule_cache_stale(self.hass, sid, live_mode):
                _LOGGER.debug("Fetching schedule for season_id=%s (team=%s)", sid, self._team)
                try:
                    games = await self.hass.async_add_executor_job(
                        scraper.fetch_schedule, sid
                    )
                except Exception as exc:
                    raise UpdateFailed(f"Failed to fetch season {sid}: {exc}") from exc
                _store_schedule(self.hass, sid, games)
            else:
                _LOGGER.debug(
                    "Re-using cached schedule for season_id=%s (team=%s)", sid, self._team
                )

            all_games.extend(_get_cached_games(self.hass, sid))

        # Filter for our team
        team_games = scraper.filter_team_games(all_games, self._team)
        team_games.sort(
            key=lambda g: g["datetime"]
            or datetime.max.replace(tzinfo=STOCKHOLM_TZ)
        )

        now = _now()

        # --- Today's game (for "current" slot) ---
        today = now.date()
        todays_games = [
            g for g in team_games
            if g["datetime"] and g["datetime"].date() == today
        ]
        todays_game = todays_games[0] if todays_games else None

        # --- Live ---
        live_game = next((g for g in team_games if self._is_live(g)), None)
        if live_game:
            live_data = await self.hass.async_add_executor_job(
                self._live_detail, live_game
            )
            period = live_data.get("period", "")
            live_data["period_label"] = {
                "P1": "Period 1", "P2": "Period 2", "P3": "Period 3",
                "OT": "Övertid", "SO": "Straffar",
            }.get(period, period or "")
        else:
            live_data = {"is_playing": False}

        # --- Next ---
        next_game = None
        if not live_game:
            for g in team_games:
                if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
                    next_game = g
                    break

        # --- Last ---
        completed = [g for g in team_games if g["is_completed"]]
        last_game = (
            max(completed, key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ))
            if completed else None
        )

        # --- New season discovery when no future games remain ---
        has_future = any(
            g["datetime"] and g["datetime"] > now for g in team_games
        )
        if not has_future and team_games:
            _LOGGER.info(
                "%s: no future games found – checking swehockey.se for new seasons",
                self._team,
            )
            try:
                new_ids = await self.hass.async_add_executor_job(
                    scraper.discover_new_seasons, self._season_ids
                )
                if new_ids:
                    _LOGGER.info(
                        "%s: discovered new season ID(s): %s – add them to your config entry",
                        self._team, new_ids,
                    )
            except Exception as exc:
                _LOGGER.warning("%s: season discovery failed: %s", self._team, exc)

        # --- Adapt poll interval ---
        if live_game:
            interval = UPDATE_INTERVAL_LIVE
        elif todays_game:
            interval = UPDATE_INTERVAL_GAME_DAY
        else:
            interval = UPDATE_INTERVAL_IDLE
        self.update_interval = timedelta(seconds=interval)

        return {
            "team":        self._team,
            "live":        live_data,
            "todays_game": self._game_extra(todays_game) if todays_game else None,
            "next_match":  self._game_extra(next_game) if next_game else None,
            "last_match":  self._game_extra(last_game) if last_game else None,
            "all_games":   [self._game_extra(g) for g in team_games],
        }
