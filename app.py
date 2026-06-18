"""
app.py – FastAPI mini-API for Swedish ice hockey schedule and live results.

Existing endpoints (default team from config.yaml)
---------------------------------------------------
GET /               API info & configured team
GET /next           Next (or currently ongoing) match
GET /last           Last completed match result
GET /live           Live match data (period, score); 404 if no live game
GET /status         Combined snapshot – ideal for Home Assistant
GET /summary        Detailed previous/current/next snapshot
GET /schedule       Full schedule for the configured team
GET /teams          All teams found in configured seasons
GET /refresh        Force cache refresh (admin use)

Watch / team-discovery endpoints
---------------------------------
GET  /search?q=...                 Search for a team name across known seasons
GET  /watches                      List all watched team+season combinations
POST /watch                        Add a team+season to the watchlist
DELETE /watch?team=...&season_id=  Remove a team+season from the watchlist

Per-team endpoints (team name URL-encoded in path)
---------------------------------------------------
GET /team/{team}/next      Next match for a specific watched team
GET /team/{team}/last      Last result for a specific watched team
GET /team/{team}/live      Live game for a specific watched team (404 if none)
GET /team/{team}/status    Combined status snapshot for a specific watched team
GET /team/{team}/schedule  Full schedule for a specific watched team
"""

import asyncio
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config as cfg_module
import mqtt_publisher
import scraper
import watchlist

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# MQTT publisher – None if mqtt_host is not configured
_mqtt_pub: Optional[mqtt_publisher.MQTTPublisher] = None

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

# ---------------------------------------------------------------------------
# Per-season cache
# ---------------------------------------------------------------------------
# Keyed by season_id; stores ALL games for that season (not filtered by team).
#
# _schedule_cache[season_id] = {
#     "games":      list[dict],
#     "fetched_at": datetime,
#     "name":       str | None,   # human-readable name from the page title
# }
# ---------------------------------------------------------------------------

_schedule_cache: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Demo simulation state
# ---------------------------------------------------------------------------
# sim_minutes: float minutes since Monday 00:00 (0 = Mon 00:00, 10080 = next Mon)
# game_clock_seconds: seconds elapsed in current period (None when not in-game)
# period_index: 0=P1,1=P2,2=P3,3=OT,4=SO
# in_period_break: True when between periods
# game_index: which game is "current" (0,1,2) or None
_demo_state: dict = {
    "sim_minutes": 0.0,
    "game_clock_seconds": None,
    "period_index": 0,
    "in_period_break": False,
    "game_index": None,
    "rng_seed": 42,
}

# Cache of goals for matches that finished today, keyed by game_id.
# Populated on first request after the game ends; survives across polls.
_finished_game_goals_cache: dict[int, list] = {}

# Cache of game_ids discovered via fetch_game_id_by_date.
# Keyed by (date, home_team, away_team) so it survives schedule cache refreshes
# which replace all game dicts (losing any game_id written directly into them).
_live_game_id_cache: dict[tuple, int] = {}

# Poll intervals (seconds)
CACHE_TTL_LIVE = 30  # game in progress
CACHE_TTL_GAME_DAY = 3600  # game scheduled today, not yet started
CACHE_TTL_IDLE = 21600  # no game today (6 hours)


def _ttl_for_games(games: list[dict]) -> int:
    """Return the appropriate cache TTL given a list of games."""
    now = datetime.now(STOCKHOLM_TZ)
    for g in games:
        dt = g.get("datetime")
        if not dt:
            continue
        delta = (now - dt).total_seconds()
        if 0 < delta < 14400 and not g["is_completed"]:
            return CACHE_TTL_LIVE
        if dt.date() == now.date():
            return CACHE_TTL_GAME_DAY
    return CACHE_TTL_IDLE


def _is_season_stale(season_id: int) -> bool:
    entry = _schedule_cache.get(season_id)
    if not entry or not entry["fetched_at"]:
        return True
    ttl = _ttl_for_games(entry["games"])
    return (datetime.now(STOCKHOLM_TZ) - entry["fetched_at"]).total_seconds() > ttl


def _global_ttl() -> int:
    """Minimum TTL across all cached seasons (drives background loop sleep)."""
    if not _schedule_cache:
        return CACHE_TTL_IDLE
    all_games = [g for entry in _schedule_cache.values() for g in entry["games"]]
    return _ttl_for_games(all_games)


async def _refresh_season(season_id: int) -> None:
    """Fetch a single season page and update the cache entry."""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, scraper.fetch_season_info, season_id)
    if not info["games"]:
        existing = _schedule_cache.get(season_id)
        if existing and existing.get("games"):
            # Fetch returned no games — likely a transient network/parse error.
            # Keep the stale cache so that finished_today status is not lost.
            logger.warning(
                "Season %d: empty response, keeping stale cache (%d games)",
                season_id,
                len(existing["games"]),
            )
            return
    _schedule_cache[season_id] = {
        "games": info["games"],
        "fetched_at": datetime.now(STOCKHOLM_TZ),
        "name": info.get("name"),
    }
    logger.info(
        "Season %d refreshed: %d games (name=%r)",
        season_id,
        len(info["games"]),
        info.get("name"),
    )


async def _ensure_seasons_fresh(season_ids: list[int]) -> None:
    """Ensure each season in *season_ids* has fresh cached data."""
    for sid in season_ids:
        if _is_season_stale(sid):
            await _refresh_season(sid)


def _all_known_season_ids(cfg: dict) -> set[int]:
    """All season IDs from config plus the watchlist."""
    base = set(cfg.get("season_ids") or [])
    return base | watchlist.all_watched_season_ids()


def _team_games(team: str, season_ids: list[int]) -> list[dict]:
    """Return all cached games for *team* across *season_ids*, sorted by datetime."""
    games: list[dict] = []
    for sid in season_ids:
        entry = _schedule_cache.get(sid, {})
        games.extend(scraper.filter_team_games(entry.get("games", []), team))
    games.sort(key=lambda g: g["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ))
    return games


def _resolve_season_ids(team: str, cfg: dict) -> list[int]:
    """
    Resolve all season_ids for *team* by merging every watchlist entry
    that matches the team name (case-insensitive).
    Falls back to config season_ids if it is the config team.
    Raises HTTPException(404) if the team is not tracked anywhere.
    """
    watches = watchlist.find_watches_for_team(team)
    if watches:
        ids: set[int] = set()
        for w in watches:
            ids.update(w["season_ids"])
        return sorted(ids)
    cfg_team = cfg.get("team")
    if cfg_team and team.lower() == cfg_team.lower():
        return cfg.get("season_ids") or []
    raise HTTPException(
        status_code=404,
        detail=(
            f"Team '{team}' is not watched. "
            "Add it with POST /watch or check spelling with GET /search."
        ),
    )


def _get_watch_or_404(watch_id: str) -> dict:
    """Return the watch entry dict, or raise HTTPException(404)."""
    watch = watchlist.get_watch(watch_id)
    if watch is None:
        raise HTTPException(
            status_code=404,
            detail=f"Watch ID '{watch_id}' not found. Use GET /watches to list all IDs.",
        )
    return watch


async def _ensure_fresh(cfg: dict) -> None:
    await _ensure_seasons_fresh(cfg["season_ids"])


# ---------------------------------------------------------------------------
# Background auto-refresh
# ---------------------------------------------------------------------------


async def _publish_all_watch_states() -> None:
    """Push current state to MQTT for every watch (only sends on change)."""
    if _mqtt_pub is None:
        return
    cfg = cfg_module.get()
    for watch_id, watch in watchlist.get_watches().items():
        try:
            payload = _team_status_payload(watch["team"], watch["season_ids"], cfg)
            _mqtt_pub.publish_state(watch_id, payload)
        except Exception as exc:
            logger.debug("MQTT state error for watch %s: %s", watch_id, exc)


async def _auto_refresh_loop(cfg: dict) -> None:
    while True:
        ttl = _global_ttl()
        await asyncio.sleep(ttl)

        # Refresh all known seasons (config + watchlist may have grown)
        for sid in list(_all_known_season_ids(cfg)):
            try:
                await _refresh_season(sid)
            except Exception as exc:
                logger.error("Auto-refresh season %d error: %s", sid, exc)

        # Push MQTT state updates after every scrape cycle
        await _publish_all_watch_states()

        # When no future games remain, hint about new seasons
        now = datetime.now(STOCKHOLM_TZ)
        all_games = [g for e in _schedule_cache.values() for g in e["games"]]
        has_future = any(g["datetime"] and g["datetime"] > now for g in all_games)
        if not has_future and all_games:
            loop = asyncio.get_event_loop()
            try:
                new_ids = await loop.run_in_executor(
                    None, scraper.discover_new_seasons, list(_all_known_season_ids(cfg))
                )
                if new_ids:
                    logger.info(
                        "New season ID(s) found on swehockey.se: %s – "
                        "add them to season_ids in config.yaml or via POST /watch",
                        new_ids,
                    )
            except Exception as exc:
                logger.warning("Season discovery failed: %s", exc)


# ---------------------------------------------------------------------------
# Leagues pre-warm + nightly midnight refresh
# ---------------------------------------------------------------------------


async def _prewarm_leagues() -> None:
    """Pre-warm leagues cache at startup; refresh nightly at midnight Stockholm."""
    STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")
    try:
        await asyncio.to_thread(scraper.fetch_leagues)
        logger.info("Leagues cache pre-warmed at startup")
    except Exception as exc:
        logger.warning("Leagues pre-warm failed: %s", exc)
    # Pre-warm all season schedules so /leagues responds instantly
    try:
        leagues_data = scraper._leagues_cache.get("data") or []
        all_season_ids = [
            sub["season_id"]
            for lg in leagues_data
            for sub in lg.get("sub_competitions", [])
            if sub.get("season_id") and sub["season_id"] != 0
        ]
        if all_season_ids:
            await _ensure_seasons_fresh(all_season_ids)
            logger.info("Season schedules pre-warmed for %d seasons", len(all_season_ids))
    except Exception as exc:
        logger.warning("Season schedule pre-warm failed: %s", exc)
    while True:
        now = datetime.now(STOCKHOLM_TZ)
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        sleep_seconds = (tomorrow - now).total_seconds()
        logger.info(
            "Leagues cache: next refresh in %.0f s (midnight Stockholm)", sleep_seconds
        )
        await asyncio.sleep(sleep_seconds)
        try:
            scraper._leagues_cache["fetched_at"] = None
            await asyncio.to_thread(scraper.fetch_leagues)
            logger.info("Leagues cache refreshed at midnight")
        except Exception as exc:
            logger.warning("Leagues midnight refresh failed: %s", exc)
        # Pre-warm all season schedules so /leagues responds instantly
        try:
            leagues_data = scraper._leagues_cache.get("data") or []
            all_season_ids = [
                sub["season_id"]
                for lg in leagues_data
                for sub in lg.get("sub_competitions", [])
                if sub.get("season_id") and sub["season_id"] != 0
            ]
            if all_season_ids:
                await _ensure_seasons_fresh(all_season_ids)
                logger.info("Season schedules pre-warmed for %d seasons", len(all_season_ids))
        except Exception as exc:
            logger.warning("Season schedule pre-warm failed: %s", exc)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_pub
    cfg = cfg_module.get()
    season_ids_to_warm = _all_known_season_ids(cfg)
    for sid in season_ids_to_warm:
        await _refresh_season(sid)

    # Connect to MQTT broker if configured
    _mqtt_pub = mqtt_publisher.create(cfg)
    if _mqtt_pub is not None:
        loop = asyncio.get_event_loop()
        connected = await loop.run_in_executor(None, _mqtt_pub.connect)
        print(f"[MQTT] connect() returned: {connected}", flush=True)
        await asyncio.sleep(2.0)  # wait for CONNACK
        watches = watchlist.get_watches()
        print(
            f"[MQTT] Publishing Discovery for {len(watches)} watch(es)...", flush=True
        )
        for watch in watches.values():
            _mqtt_pub.publish_discovery(watch)
        # Publish global MQTT discovery for AWTRIX currentApp sensor so
        # button automations can read which app is on screen without
        # needing input_text helpers that require a HA restart.
        try:
            import json as _json
            from pathlib import Path as _Path

            _opts = _json.loads(_Path("/data/options.json").read_text(encoding="utf-8"))
            _awtrix_prefix = (_opts.get("awtrix_prefix") or "").strip()
            if _awtrix_prefix:
                _mqtt_pub.publish_global_discovery(_awtrix_prefix)
        except Exception as _e:
            print(f"[MQTT] Could not publish global AWTRIX discovery: {_e}", flush=True)
        await _publish_all_watch_states()
        print("[MQTT] Startup complete", flush=True)
    else:
        print("[MQTT] Disabled (mqtt_host is empty)", flush=True)

    task = asyncio.create_task(_auto_refresh_loop(cfg))
    leagues_task = asyncio.create_task(_prewarm_leagues())
    yield
    task.cancel()
    leagues_task.cancel()
    if _mqtt_pub is not None:
        _mqtt_pub.disconnect()


app = FastAPI(
    title="HockeyLive API",
    description="Swedish ice hockey schedule and live results from swehockey.se",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(STOCKHOLM_TZ)


def _is_live(game: dict) -> bool:
    """
    A game is considered live if:
      - it has started (datetime is in the past)
      - it started within the last 4 hours
      - it is not yet completed (scraper requires >= 3 complete period entries)
    Games without a game_id can still be detected as live; _live_detail()
    gracefully falls back to heuristic period estimation when game_id is None.
    """
    dt = game.get("datetime")
    if not dt:
        return False
    delta = (_now() - dt).total_seconds()
    return 0 < delta < 14400 and not game["is_completed"]


def _game_to_dict(game: dict, cfg: dict) -> dict:
    """Serialize a game dict to JSON-safe dict, adding team perspective."""
    team = cfg["team"].lower()
    is_home = (game["home_team"] or "").lower() == team

    opponent = game["away_team"] if is_home else game["home_team"]
    score_for = game["home_score"] if is_home else game["away_score"]
    score_against = game["away_score"] if is_home else game["home_score"]

    won: Optional[bool] = None
    if score_for is not None and score_against is not None:
        won = score_for > score_against

    dt_iso = game["datetime"].isoformat() if game["datetime"] else None

    return {
        "game_id": game["game_id"],
        "season_id": game["season_id"],
        "date": game["date"],
        "time": game["time"],
        "datetime_iso": dt_iso,
        "home_team": game["home_team"],
        "away_team": game["away_team"],
        "is_home_game": is_home,
        "opponent": opponent,
        "venue": game["venue"],
        "round": game["round"],
        "home_score": game["home_score"],
        "away_score": game["away_score"],
        "score_for": score_for,
        "score_against": score_against,
        "period_scores": game["period_scores"],
        "won": won,
        "is_completed": game["is_completed"],
        "is_live": _is_live(game),
    }


def _live_detail(game: dict) -> dict:
    """
    Fetch game events and build a full live detail dict:
      period, period_clock, scores, goals, last_goal, penalties, active_penalties.
    Falls back to heuristic period estimation if scraping fails.
    """
    game_id = game.get("game_id")

    # The season schedule page omits the Game/Events link for live (in-progress)
    # games.  When game_id is missing, try the GamesByDate page which always
    # includes it.  Store the result in a module-level cache keyed by
    # (date, home_team, away_team) so it survives schedule cache refreshes
    # (which replace all game dicts every 30s, losing any game_id stored in them).
    if not game_id:
        _key = (game.get("date"), game.get("home_team"), game.get("away_team"))
        game_id = _live_game_id_cache.get(_key)
        if not game_id and all(_key):
            game_id = scraper.fetch_game_id_by_date(
                game["date"], game["home_team"], game["away_team"]
            )
            if game_id:
                _live_game_id_cache[_key] = game_id

    events_data = scraper.fetch_game_events(game_id)

    if events_data and events_data.get("period"):
        period = events_data["period"]
        home_score = events_data.get("home_score", game["home_score"] or 0)
        away_score = events_data.get("away_score", game["away_score"] or 0)
        period_clock = events_data.get("period_clock")
        is_overtime = events_data.get("is_overtime", False)
        is_shootout = events_data.get("is_shootout", False)
        goals = events_data.get("goals", [])
        last_goal = events_data.get("last_goal")
        penalties = events_data.get("penalties", [])
        active_penalties = events_data.get("active_penalties", [])
    else:
        # Primary fallback: infer period from period_scores on the schedule page.
        # The schedule shows one entry per completed period, e.g. "(2-1, 0-1)"
        # means 2 periods complete → game is in P3.  This is reliable regardless
        # of how long the game has been running (avoids the time-heuristic "SO" trap).
        ps = game.get("period_scores") or ""
        completed_periods = ps.count(",") + 1 if ps.startswith("(") else 0
        period = {0: "P1", 1: "P2", 2: "P3"}.get(completed_periods, "P3")

        home_score = game["home_score"] or 0
        away_score = game["away_score"] or 0
        period_clock = None
        is_overtime = False
        is_shootout = False
        goals = []
        last_goal = None
        penalties = []
        active_penalties = []

    # Human-readable period label in Swedish
    period_sv = {
        "P1": "Period 1",
        "P2": "Period 2",
        "P3": "Period 3",
        "OT": "Övertid",
        "SO": "Straffar",
    }.get(period, period)

    return {
        "period": period,
        "period_label": period_sv,
        "period_clock": period_clock,
        "home_score": home_score,
        "away_score": away_score,
        "is_overtime": is_overtime,
        "is_shootout": is_shootout,
        "goals": goals,
        "last_goal": last_goal,
        "penalties": penalties,
        "active_penalties": active_penalties,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def root():
    cfg = cfg_module.get()
    watches = watchlist.get_watches()
    return {
        "api": "HockeyLive API",
        "version": "2.0.0",
        "build_id": os.environ.get("BUILD_ID", "dev"),
        "team": cfg["team"],
        "season_ids": cfg["season_ids"],
        "source": "stats.swehockey.se",
        "endpoints": [
            "/next",
            "/last",
            "/live",
            "/status",
            "/summary",
            "/schedule",
            "/teams",
            "/refresh",
            "/search",
            "/watches",
            "/watch",
            "/watch/{id}/status",
            "/watch/{id}/next",
            "/watch/{id}/last",
            "/watch/{id}/live",
            "/watch/{id}/schedule",
            "/team/{team}/next",
            "/team/{team}/last",
            "/team/{team}/live",
            "/team/{team}/status",
            "/team/{team}/schedule",
        ],
        "watched_teams": list(watches.keys()),
        "cached_seasons": {
            str(sid): {
                "name": entry.get("name"),
                "games": len(entry.get("games", [])),
                "fetched_at": entry["fetched_at"].isoformat()
                if entry.get("fetched_at")
                else None,
            }
            for sid, entry in _schedule_cache.items()
        },
    }


_NO_DEFAULT_TEAM = HTTPException(status_code=503, detail="No default team configured")


@app.get("/next")
async def next_match():
    """Next upcoming or currently ongoing match."""
    cfg = cfg_module.get()
    if not cfg.get("team"):
        raise _NO_DEFAULT_TEAM
    await _ensure_fresh(cfg)

    now = _now()
    games = _team_games(cfg["team"], cfg["season_ids"])

    # First check for a live game
    for g in games:
        if _is_live(g):
            return {"status": "live", "game": _game_to_dict(g, cfg)}

    # Then find the next upcoming game (datetime in the future)
    for g in sorted(
        games, key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ)
    ):
        if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
            return {"status": "upcoming", "game": _game_to_dict(g, cfg)}

    raise HTTPException(status_code=404, detail="No upcoming match found.")


@app.get("/last")
async def last_match():
    """Most recently completed match and its result."""
    cfg = cfg_module.get()
    if not cfg.get("team"):
        raise _NO_DEFAULT_TEAM
    await _ensure_fresh(cfg)

    completed = [
        g for g in _team_games(cfg["team"], cfg["season_ids"]) if g["is_completed"]
    ]
    if not completed:
        raise HTTPException(status_code=404, detail="No completed matches found.")

    last = max(
        completed,
        key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ),
    )
    return {"game": _game_to_dict(last, cfg)}


@app.get("/live")
async def live_match():
    """
    Returns live score and period if a match is currently in progress.
    Returns 404 if no game is live.
    """
    cfg = cfg_module.get()
    if not cfg.get("team"):
        raise _NO_DEFAULT_TEAM
    await _ensure_fresh(cfg)

    for g in _team_games(cfg["team"], cfg["season_ids"]):
        if _is_live(g):
            base = _game_to_dict(g, cfg)
            detail = _live_detail(g)
            return {"status": "live", "game": {**base, **detail}}

    return JSONResponse(
        status_code=404,
        content={
            "status": "no_live_game",
            "detail": "No match is currently in progress.",
        },
    )


@app.get("/status")
async def status():
    """
    Combined status snapshot – designed for Home Assistant REST sensors.
    Always returns 200 with a consistent JSON structure.
    """
    cfg = cfg_module.get()
    if not cfg.get("team"):
        raise _NO_DEFAULT_TEAM
    await _ensure_fresh(cfg)

    team = cfg["team"]
    games = _team_games(team, cfg["season_ids"])
    now = _now()

    # --- live ---
    live_game = next((g for g in games if _is_live(g)), None)
    live_data: dict = {"is_playing": False}
    if live_game:
        live_data = {"is_playing": True, **_live_detail(live_game)}
        live_data["home_team"] = live_game["home_team"]
        live_data["away_team"] = live_game["away_team"]
        live_data["venue"] = live_game["venue"]

    # --- next ---
    next_game = None
    if not live_game:
        for g in sorted(
            games,
            key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ),
        ):
            if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
                next_game = g
                break
    next_data: Optional[dict] = None
    if next_game:
        next_data = _game_to_dict(next_game, cfg)

    # --- last ---
    completed = [g for g in games if g["is_completed"]]
    last_game = (
        max(
            completed,
            key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ),
        )
        if completed
        else None
    )
    last_data: Optional[dict] = _game_to_dict(last_game, cfg) if last_game else None

    return {
        "team": team,
        "updated_at": now.isoformat(),
        "live": live_data,
        "next_match": next_data,
        "last_match": last_data,
    }


@app.get("/summary")
async def summary():
    """
    Single-call summary for a team.

    Sections
    --------
    previous  – Most recently completed match before today.
    current   – Today's match, but only once it is within 2 h of kick-off
                (or has already started / finished).
                  started=False  → pre-game window (kick-off imminent)
                  started=True   → in progress or already done
    next      – Next future match. Includes today's match if it is still
                more than 2 h away.
    """
    cfg = cfg_module.get()
    if not cfg.get("team"):
        raise _NO_DEFAULT_TEAM
    await _ensure_fresh(cfg)

    team = cfg["team"]
    games = _team_games(team, cfg["season_ids"])
    now = _now()
    today = now.date()

    PRE_GAME_WINDOW = 7200  # seconds before kick-off to promote game to "current"

    # Partition today's games: promoted to current once within 2 h of start.
    today_games = [g for g in games if g["datetime"] and g["datetime"].date() == today]
    today_as_current = [
        g
        for g in today_games
        if (g["datetime"] - now).total_seconds() <= PRE_GAME_WINDOW
    ]
    today_as_next = [
        g
        for g in today_games
        if (g["datetime"] - now).total_seconds() > PRE_GAME_WINDOW
        and not g["is_completed"]
    ]

    before_today = [
        g
        for g in games
        if g["datetime"] and g["datetime"].date() < today and g["is_completed"]
    ]
    after_today = [
        g
        for g in games
        if g["datetime"] and g["datetime"].date() > today and not g["is_completed"]
    ]

    # --- previous: last completed game before today ---
    previous_game = (
        max(before_today, key=lambda g: g["datetime"]) if before_today else None
    )

    def _prev_dict(g: dict) -> dict:
        d = _game_to_dict(g, cfg)
        return {
            "home_team": d["home_team"],
            "away_team": d["away_team"],
            "home_score": d["home_score"],
            "away_score": d["away_score"],
            "score_for": d["score_for"],
            "score_against": d["score_against"],
            "won": d["won"],
            "overtime": bool(
                g.get("period_scores") and "OT" in (g["period_scores"] or "")
            ),
            "shootout": bool(
                g.get("period_scores") and "SO" in (g["period_scores"] or "")
            ),
            "datetime": d["datetime_iso"],
            "venue": d["venue"],
            "round": d["round"],
        }

    # --- current: today's game if within 2 h (or already started/done) ---
    current_data: Optional[dict] = None
    if today_as_current:
        tg = today_as_current[0]
        base = _game_to_dict(tg, cfg)
        started = tg["datetime"] <= now
        is_live_now = _is_live(tg)
        is_done = tg["is_completed"]
        won: Optional[bool] = None
        if (
            is_done
            and base["score_for"] is not None
            and base["score_against"] is not None
        ):
            won = base["score_for"] > base["score_against"]

        current_data = {
            "home_team": tg["home_team"],
            "away_team": tg["away_team"],
            "home_score": tg["home_score"],
            "away_score": tg["away_score"],
            "score_for": base["score_for"],
            "score_against": base["score_against"],
            "started": started,
            "is_live": is_live_now,
            "is_completed": is_done,
            "won": won,
            "datetime": base["datetime_iso"],
            "venue": base["venue"],
            "round": base["round"],
            # Live/completed fields (filled below when started)
            "period": None,
            "period_label": None,
            "period_clock": None,
            "is_overtime": False,
            "is_shootout": False,
        }
        if is_live_now:
            detail = _live_detail(tg)
            current_data.update(
                {
                    "home_score": detail["home_score"],
                    "away_score": detail["away_score"],
                    "period": detail["period"],
                    "period_label": detail["period_label"],
                    "period_clock": detail["period_clock"],
                    "is_overtime": detail["is_overtime"],
                    "is_shootout": detail["is_shootout"],
                }
            )

    # --- next: next future match (tomorrow+, plus today's games still >2 h away) ---
    next_candidates = sorted(
        today_as_next + after_today,
        key=lambda g: g["datetime"],
    )
    next_game = next_candidates[0] if next_candidates else None

    def _next_dict(g: dict) -> dict:
        d = _game_to_dict(g, cfg)
        return {
            "home_team": d["home_team"],
            "away_team": d["away_team"],
            "datetime": d["datetime_iso"],
            "venue": d["venue"],
            "round": d["round"],
            "is_home": d["is_home_game"],
            "opponent": d["opponent"],
        }

    return {
        "team": team,
        "updated_at": now.isoformat(),
        "previous": _prev_dict(previous_game) if previous_game else None,
        "current": current_data,
        "next": _next_dict(next_game) if next_game else None,
    }


@app.get("/schedule")
async def schedule():
    """Full schedule for the configured team (all seasons)."""
    cfg = cfg_module.get()
    await _ensure_fresh(cfg)
    games = _team_games(cfg["team"], cfg["season_ids"])
    return {
        "team": cfg["team"],
        "season_ids": cfg["season_ids"],
        "games": [_game_to_dict(g, cfg) for g in games],
        "total": len(games),
    }


@app.get("/teams")
async def teams():
    """
    List all team names found in the configured seasons.
    Useful for discovering the exact team name spelling used by swehockey.se.
    """
    cfg = cfg_module.get()
    await _ensure_fresh(cfg)
    season_ids = cfg["season_ids"]
    all_teams: set[str] = set()
    for sid in season_ids:
        entry = _schedule_cache.get(sid, {})
        for g in entry.get("games", []):
            if g.get("home_team"):
                all_teams.add(g["home_team"])
            if g.get("away_team"):
                all_teams.add(g["away_team"])
    return {"season_ids": season_ids, "teams": sorted(all_teams)}


@app.get("/leagues")
async def list_leagues():
    """
    Discover all current leagues and their sub-competitions from swehockey.se.
    Cached for 1 hour.
    """
    _FALLBACK = [
        {
            "league": "SHL",
            "season_id": 18263,
            "sub_competitions": [
                {"name": "SHL", "season_id": 18263, "teams": []},
                {"name": "SM-slutspel SHL", "season_id": 19791, "teams": []},
            ],
        },
        {
            "league": "HockeyAllsvenskan",
            "season_id": 18266,
            "sub_competitions": [
                {"name": "HockeyAllsvenskan", "season_id": 18266, "teams": []},
                {"name": "SM-slutspel Allsvenskan", "season_id": 19979, "teams": []},
            ],
        },
    ]
    try:
        raw = await asyncio.to_thread(scraper.fetch_leagues)
    except Exception as exc:
        logger.warning("fetch_leagues failed, using fallback: %s", exc)
        return {"leagues": [{"league": "Demo", "season_id": 0, "sub_competitions": [{"name": "Demo", "season_id": 0, "teams": ["demo"]}]}] + _FALLBACK}

    enriched: list[dict] = []
    first_entry = True
    for league in raw:
        sub_list: list[dict] = []
        for sub in league.get("sub_competitions", []):
            sid = sub["season_id"]
            try:
                await _ensure_seasons_fresh([sid])
                entry = _schedule_cache.get(sid, {})
                team_set: set[str] = set()
                for g in entry.get("games", []):
                    if g.get("home_team"):
                        team_set.add(g["home_team"])
                    if g.get("away_team"):
                        team_set.add(g["away_team"])
                teams_list = sorted(team_set)
            except Exception:
                teams_list = []
            sub_list.append({"name": sub["name"], "season_id": sid, "teams": teams_list})
        if first_entry and sub_list:
            sub_list[0] = dict(sub_list[0], teams=["demo"] + sub_list[0]["teams"])
            first_entry = False
        enriched.append({"league": league["league"], "season_id": league["season_id"], "sub_competitions": sub_list})

    demo_entry = {"league": "Demo", "season_id": 0, "sub_competitions": [{"name": "Demo", "season_id": 0, "teams": ["demo"]}]}
    return {"leagues": [demo_entry] + enriched}


@app.get("/refresh")
async def force_refresh():
    """Force an immediate cache refresh of all known seasons."""
    cfg = cfg_module.get()
    season_ids = list(_all_known_season_ids(cfg))
    for sid in season_ids:
        await _refresh_season(sid)
    return {
        "ok": True,
        "seasons_refreshed": season_ids,
        "fetched_at": datetime.now(STOCKHOLM_TZ).isoformat(),
    }


# ===========================================================================
# Watch / team-discovery endpoints
# ===========================================================================


@app.get("/search")
async def search_team(
    q: str = Query(..., description="Team name to search for (partial match)"),
):
    """
    Search for a team name across all known seasons (config + watchlist).
    Returns a list of {team, season_id, season_name} matches.
    """
    cfg = cfg_module.get()
    season_ids = list(_all_known_season_ids(cfg))
    await _ensure_seasons_fresh(season_ids)

    q_lower = q.strip().lower()
    if not q_lower:
        raise HTTPException(
            status_code=422, detail="Query parameter 'q' must not be empty."
        )

    results: list[dict] = []
    for sid in sorted(season_ids):
        entry = _schedule_cache.get(sid, {})
        teams_in_season: set[str] = set()
        for g in entry.get("games", []):
            for name in (g.get("home_team"), g.get("away_team")):
                if name and q_lower in name.lower():
                    teams_in_season.add(name)
        for team in sorted(teams_in_season):
            watched_ids = watchlist.get_season_ids_for_team(team)
            results.append(
                {
                    "team": team,
                    "season_id": sid,
                    "season_name": entry.get("name"),
                    "is_watched": sid in watched_ids,
                }
            )

    return {"query": q, "count": len(results), "results": results}


@app.get("/watches")
async def list_watches():
    """List all watched team+season combinations with their unique IDs."""
    watches = watchlist.get_watches()
    items = []
    for watch_id, w in watches.items():
        seasons = []
        for sid in w["season_ids"]:
            entry = _schedule_cache.get(sid, {})
            seasons.append(
                {
                    "season_id": sid,
                    "season_name": entry.get("name"),
                }
            )
        items.append(
            {
                "id": watch_id,
                "team": w["team"],
                "seasons": seasons,
            }
        )
    return {"count": len(items), "watches": items}


class WatchRequest(BaseModel):
    name: Optional[str] = None  # stable alias; ID is based on this if provided
    team: str
    season_ids: list[int]


@app.post("/watch", status_code=201)
async def add_watch(req: WatchRequest):
    """
    Add a team + one or more season_ids to the watchlist.
    All season_ids are validated against swehockey.se before saving.

    The returned ID is deterministic: posting the same team+season_ids
    combination again returns the same ID with `created=false`.

    Use GET /search?q=... to find the correct team name spelling first.
    """
    if req.team.lower() == "demo":
        watch_id, created = watchlist.add_watch("demo", req.season_ids or [0], req.name)
        return {
            "id": watch_id,
            "created": created,
            "team": "demo",
            "seasons": [{"season_id": sid, "season_name": "Demo Season"} for sid in (req.season_ids or [0])],
            "links": {
                "status": f"/watch/{watch_id}/status",
                "next": f"/watch/{watch_id}/next",
                "last": f"/watch/{watch_id}/last",
                "live": f"/watch/{watch_id}/live",
                "schedule": f"/watch/{watch_id}/schedule",
            },
        }

    if not req.season_ids:
        raise HTTPException(status_code=422, detail="season_ids must not be empty.")

    # Freshen all requested seasons
    await _ensure_seasons_fresh(req.season_ids)

    # Validate team presence in each season and resolve canonical name
    canonical: Optional[str] = None
    for sid in req.season_ids:
        entry = _schedule_cache.get(sid, {})
        games = entry.get("games", [])
        if not games:
            raise HTTPException(
                status_code=404,
                detail=f"Season {sid} not found or contains no games.",
            )
        name_map: dict[str, str] = {}
        for g in games:
            for name in (g.get("home_team"), g.get("away_team")):
                if name:
                    name_map[name.lower()] = name
        found = name_map.get(req.team.lower())
        if found is None:
            close = [v for k, v in name_map.items() if req.team.lower() in k]
            hint = f" Did you mean one of: {close[:5]}?" if close else ""
            raise HTTPException(
                status_code=404,
                detail=f"Team '{req.team}' not found in season {sid}.{hint}",
            )
        # Use the first canonical match; confirm it's consistent across seasons
        if canonical is None:
            canonical = found
        elif canonical != found:
            # Extremely unlikely, but guard against mismatched spelling
            canonical = found  # accept later spelling without crashing

    watch_id, created = watchlist.add_watch(canonical, req.season_ids, req.name)
    if created and _mqtt_pub is not None:
        watch_entry = watchlist.get_watch(watch_id)
        _mqtt_pub.publish_discovery(watch_entry)
        try:
            state_payload = _team_status_payload(canonical, req.season_ids, cfg)
            _mqtt_pub.publish_state(watch_id, state_payload)
        except Exception as exc:
            logger.debug("MQTT initial state error for %s: %s", watch_id, exc)
    season_names = [
        {"season_id": sid, "season_name": _schedule_cache.get(sid, {}).get("name")}
        for sid in sorted(req.season_ids)
    ]
    return {
        "id": watch_id,
        "created": created,
        "team": canonical,
        "seasons": season_names,
        "links": {
            "status": f"/watch/{watch_id}/status",
            "next": f"/watch/{watch_id}/next",
            "last": f"/watch/{watch_id}/last",
            "live": f"/watch/{watch_id}/live",
            "schedule": f"/watch/{watch_id}/schedule",
        },
    }


@app.delete("/watch/{watch_id}")
async def remove_watch(watch_id: str):
    """Remove a watch entry by its ID (see GET /watches for IDs)."""
    watch = watchlist.get_watch(watch_id)
    if watch is None:
        raise HTTPException(status_code=404, detail=f"Watch ID '{watch_id}' not found.")
    watchlist.remove_watch(watch_id)
    if _mqtt_pub is not None:
        _mqtt_pub.remove_watch(watch_id)
    return {
        "removed": True,
        "id": watch_id,
        "team": watch["team"],
        "season_ids": watch["season_ids"],
    }


# ===========================================================================
# Per-watch-ID endpoints  (/watch/{id}/...)
# ===========================================================================
#
# Use the ID returned by POST /watch (or visible in GET /watches).
# The ID encodes both the team name and the set of season_ids it covers,
# so one ID may span SHL + CHL or any other league combination.
# ===========================================================================


def _watch_payload_base(watch: dict, cfg: dict) -> tuple[str, list[int], dict]:
    """Unpack a watch entry into (team, season_ids, fake_cfg)."""
    team = watch["team"]
    season_ids = watch["season_ids"]
    fake_cfg = {**cfg, "team": team}
    return team, season_ids, fake_cfg


@app.get("/watch/{watch_id}/status")
async def watch_status(watch_id: str):
    """Combined status snapshot for a specific watch entry."""
    cfg = cfg_module.get()
    watch = _get_watch_or_404(watch_id)
    team, season_ids, fake_cfg = _watch_payload_base(watch, cfg)
    await _ensure_seasons_fresh(season_ids)
    payload = _team_status_payload(team, season_ids, cfg)
    payload["watch_id"] = watch_id
    return payload


@app.get("/watch/{watch_id}/next")
async def watch_next(watch_id: str):
    """Next upcoming or currently ongoing match for a specific watch entry."""
    cfg = cfg_module.get()
    watch = _get_watch_or_404(watch_id)
    team, season_ids, fake_cfg = _watch_payload_base(watch, cfg)
    await _ensure_seasons_fresh(season_ids)
    now = _now()
    games = _team_games(team, season_ids)
    for g in games:
        if _is_live(g):
            return {
                "watch_id": watch_id,
                "status": "live",
                "game": _game_to_dict(g, fake_cfg),
            }
    for g in sorted(
        games, key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ)
    ):
        if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
            return {
                "watch_id": watch_id,
                "status": "upcoming",
                "game": _game_to_dict(g, fake_cfg),
            }
    raise HTTPException(
        status_code=404, detail=f"No upcoming match found for watch '{watch_id}'."
    )


@app.get("/watch/{watch_id}/last")
async def watch_last(watch_id: str):
    """Most recently completed match for a specific watch entry."""
    cfg = cfg_module.get()
    watch = _get_watch_or_404(watch_id)
    team, season_ids, fake_cfg = _watch_payload_base(watch, cfg)
    await _ensure_seasons_fresh(season_ids)
    completed = [g for g in _team_games(team, season_ids) if g["is_completed"]]
    if not completed:
        raise HTTPException(
            status_code=404,
            detail=f"No completed matches found for watch '{watch_id}'.",
        )
    last = max(
        completed,
        key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ),
    )
    return {"watch_id": watch_id, "game": _game_to_dict(last, fake_cfg)}


@app.get("/watch/{watch_id}/live")
async def watch_live(watch_id: str):
    """Live score and period for a specific watch entry; 404 if no game is live."""
    cfg = cfg_module.get()
    watch = _get_watch_or_404(watch_id)
    team, season_ids, fake_cfg = _watch_payload_base(watch, cfg)
    await _ensure_seasons_fresh(season_ids)
    for g in _team_games(team, season_ids):
        if _is_live(g):
            base = _game_to_dict(g, fake_cfg)
            detail = _live_detail(g)
            return {"watch_id": watch_id, "status": "live", "game": {**base, **detail}}
    return JSONResponse(
        status_code=404,
        content={
            "status": "no_live_game",
            "watch_id": watch_id,
            "detail": "No match is currently in progress.",
        },
    )


@app.get("/watch/{watch_id}/schedule")
async def watch_schedule(watch_id: str):
    """Full schedule for all seasons in a specific watch entry."""
    cfg = cfg_module.get()
    watch = _get_watch_or_404(watch_id)
    team, season_ids, fake_cfg = _watch_payload_base(watch, cfg)
    await _ensure_seasons_fresh(season_ids)
    games = _team_games(team, season_ids)
    return {
        "watch_id": watch_id,
        "team": team,
        "season_ids": season_ids,
        "games": [_game_to_dict(g, fake_cfg) for g in games],
        "total": len(games),
    }


# ===========================================================================
# Per-team endpoints  (/team/{team}/...)
# ===========================================================================
#
# {team} must be URL-encoded (e.g. "HV%2071" for "HV 71").
# The team must either be in the watchlist or be the config team.
# Season_ids are merged from ALL watch entries for that team.
# ===========================================================================


def _team_status_payload(team: str, season_ids: list[int], cfg: dict) -> dict:
    """Build the combined status dict for any team."""
    # Reuse _game_to_dict with a temporary cfg-like dict so team fields are correct
    fake_cfg = {**cfg, "team": team}
    games = _team_games(team, season_ids)
    now = _now()

    live_game = next((g for g in games if _is_live(g)), None)
    live_data: dict = {"is_playing": False}
    if live_game:
        live_data = {"is_playing": True, **_live_detail(live_game)}
        live_data["home_team"] = live_game["home_team"]
        live_data["away_team"] = live_game["away_team"]
        live_data["venue"] = live_game["venue"]

    next_game = None
    if not live_game:
        for g in sorted(
            games,
            key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ),
        ):
            if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
                next_game = g
                break

    completed = [g for g in games if g["is_completed"]]
    last_game = (
        max(
            completed,
            key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ),
        )
        if completed
        else None
    )

    # Build last_match dict; if it was today, enrich with goal-by-goal breakdown
    # so that finished_today matches can show goal history even after a restart.
    last_data: Optional[dict] = None
    if last_game:
        last_data = _game_to_dict(last_game, fake_cfg)
        today_str = now.strftime("%Y-%m-%d")
        if last_game.get("date") == today_str:
            gid = last_game.get("game_id")
            if gid and gid not in _finished_game_goals_cache:
                ev = scraper.fetch_game_events(gid)
                _finished_game_goals_cache[gid] = ev.get("goals", [])
            if gid is not None:
                last_data["goals"] = _finished_game_goals_cache.get(gid, [])

    return {
        "team": team,
        "season_ids": season_ids,
        "updated_at": now.isoformat(),
        "live": live_data,
        "next_match": _game_to_dict(next_game, fake_cfg) if next_game else None,
        "last_match": last_data,
    }


@app.get("/team/{team}/status")
async def team_status(team: str):
    """Combined status snapshot for a watched team."""
    cfg = cfg_module.get()
    season_ids = _resolve_season_ids(team, cfg)
    await _ensure_seasons_fresh(season_ids)
    return _team_status_payload(team, season_ids, cfg)


@app.get("/team/{team}/next")
async def team_next(team: str):
    """Next upcoming or currently ongoing match for a watched team."""
    cfg = cfg_module.get()
    season_ids = _resolve_season_ids(team, cfg)
    await _ensure_seasons_fresh(season_ids)
    fake_cfg = {**cfg, "team": team}
    now = _now()
    games = _team_games(team, season_ids)

    for g in games:
        if _is_live(g):
            return {"status": "live", "game": _game_to_dict(g, fake_cfg)}
    for g in sorted(
        games, key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ)
    ):
        if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
            return {"status": "upcoming", "game": _game_to_dict(g, fake_cfg)}
    raise HTTPException(
        status_code=404, detail=f"No upcoming match found for '{team}'."
    )


@app.get("/team/{team}/last")
async def team_last(team: str):
    """Most recently completed match for a watched team."""
    cfg = cfg_module.get()
    season_ids = _resolve_season_ids(team, cfg)
    await _ensure_seasons_fresh(season_ids)
    fake_cfg = {**cfg, "team": team}
    completed = [g for g in _team_games(team, season_ids) if g["is_completed"]]
    if not completed:
        raise HTTPException(
            status_code=404, detail=f"No completed matches found for '{team}'."
        )
    last = max(
        completed,
        key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ),
    )
    return {"game": _game_to_dict(last, fake_cfg)}


@app.get("/team/{team}/live")
async def team_live(team: str):
    """Live score and period for a watched team; 404 if no game is live."""
    cfg = cfg_module.get()
    season_ids = _resolve_season_ids(team, cfg)
    await _ensure_seasons_fresh(season_ids)
    fake_cfg = {**cfg, "team": team}
    for g in _team_games(team, season_ids):
        if _is_live(g):
            base = _game_to_dict(g, fake_cfg)
            detail = _live_detail(g)
            return {"status": "live", "game": {**base, **detail}}
    return JSONResponse(
        status_code=404,
        content={
            "status": "no_live_game",
            "detail": f"No match is currently in progress for '{team}'.",
        },
    )


@app.get("/team/{team}/schedule")
async def team_schedule(team: str):
    """Full schedule for a watched team across all its watched seasons."""
    cfg = cfg_module.get()
    season_ids = _resolve_season_ids(team, cfg)
    await _ensure_seasons_fresh(season_ids)
    fake_cfg = {**cfg, "team": team}
    games = _team_games(team, season_ids)
    return {
        "team": team,
        "season_ids": season_ids,
        "games": [_game_to_dict(g, fake_cfg) for g in games],
        "total": len(games),
    }


# ---------------------------------------------------------------------------
# Demo simulation engine
# ---------------------------------------------------------------------------
import random as _random

# Week layout (minutes from Mon 00:00):
#   Game 0: Mon 19:00 = 1140 min  Home vs Team 1 (T1)  → Demo loses 1-4
#   Game 1: Thu 19:00 = 4380 min  Away vs Team 2 (T2)  → Demo wins 5-3 (many goals)
#   Game 2: Sun 15:15 = 9255 min  Home vs Team 3 (T3)  → Shootout win

_DEMO_WEEK_MINUTES = 7 * 24 * 60  # 10080

_DEMO_GAMES = [
    {
        "idx": 0, "start_min": 1140, "is_home": True,
        "home": "Demo FC", "away": "Team 1", "home_short": "DFC", "away_short": "T1",
        "venue": "Demo Arena", "round": "Round 31",
        "outcome": "loss",
        # goals: list of (abs_game_second, period, scorer, team, h_score, a_score)
        "goals": [
            (480,  "P1", "E. Opponent", "Team 1", 0, 1),
            (820,  "P1", "F. Rival",    "Team 1", 0, 2),
            (1250, "P2", "A. Demo",     "Demo FC", 1, 2),
            (2800, "P3", "G. Foe",      "Team 1", 1, 3),
            (3200, "P3", "H. Enemy",    "Team 1", 1, 4),
        ],
        "penalties": [
            (600,  "B. Rough", "Demo FC", 2, "Roughing"),
            (2100, "C. Slash", "Team 1",  2, "Slashing"),
        ],
        "final_h": 1, "final_a": 4,
        "overtime": False, "shootout": False,
        "period_scores": "0-2,1-0,0-2",
    },
    {
        "idx": 1, "start_min": 4380, "is_home": False,
        "home": "Team 2", "away": "Demo FC", "home_short": "T2", "away_short": "DFC",
        "venue": "Away Arena", "round": "Round 32",
        "outcome": "win",
        "goals": [
            (310,  "P1", "A. Demo",     "Demo FC", 0, 1),
            (750,  "P1", "X. Home",     "Team 2",  1, 1),
            (1100, "P2", "B. Demo",     "Demo FC", 1, 2),
            (1450, "P2", "Y. Home",     "Team 2",  2, 2),
            (1700, "P2", "C. Demo",     "Demo FC", 2, 3),
            (2200, "P3", "Z. Home",     "Team 2",  3, 3),
            (2600, "P3", "D. Demo",     "Demo FC", 3, 4),
            (3100, "P3", "E. Demo",     "Demo FC", 3, 5),
        ],
        "penalties": [
            (900,  "P. Push",  "Team 2",  2, "Pushing"),
            (1900, "Q. Hook",  "Demo FC", 2, "Hooking"),
        ],
        "final_h": 3, "final_a": 5,
        "outcome_for_demo": "win",
        "overtime": False, "shootout": False,
        "period_scores": "1-1,2-2,0-2",
    },
    {
        "idx": 2, "start_min": 9255, "is_home": True,
        "home": "Demo FC", "away": "Team 3", "home_short": "DFC", "away_short": "T3",
        "venue": "Demo Arena", "round": "Round 33",
        "outcome": "shootout_win",
        "goals": [
            (600,  "P1", "A. Demo",     "Demo FC", 1, 0),
            (1500, "P2", "V. Opp",      "Team 3",  1, 1),
            (2400, "P3", "B. Demo",     "Demo FC", 2, 1),
            (3200, "P3", "W. Opp",      "Team 3",  2, 2),
            # OT goal: none (goes to SO)
        ],
        "penalties": [
            (800,  "R. Trip", "Team 3",  2, "Tripping"),
            (2900, "S. Elbow","Demo FC", 2, "Elbowing"),
        ],
        "final_h": 2, "final_a": 2,
        "ot_goals": [],
        "so_winner": "Demo FC",
        "overtime": True, "shootout": True,
        "period_scores": "1-0,0-1,1-1,0-0,SO",
    },
]

# Period durations in seconds
_PERIOD_DUR = {0: 1200, 1: 1200, 2: 1200, 3: 600, 4: 0}  # P1,P2,P3,OT,SO
_PERIOD_LABELS = {0: ("P1", "Period 1"), 1: ("P2", "Period 2"), 2: ("P3", "Period 3"), 3: ("OT", "Overtime"), 4: ("SO", "Shootout")}


def _demo_fmt_clock(secs: float) -> str:
    m = int(secs) // 60
    s = int(secs) % 60
    return f"{m:02d}:{s:02d}"


def _demo_goals_at(game: dict, period_idx: int, clock_secs: float) -> list:
    """Return all goals that have occurred up to this point in the game."""
    period_key = _PERIOD_LABELS[period_idx][0]
    period_order = ["P1", "P2", "P3", "OT", "SO"]
    current_period_order = period_order.index(period_key) if period_key in period_order else 0
    goals = []
    for (abs_sec, period, scorer, team, h, a) in game["goals"]:
        goal_period_order = period_order.index(period) if period in period_order else 0
        if goal_period_order < current_period_order:
            goals.append({"time": _demo_fmt_clock(abs_sec % 1200), "period": period, "scorer": scorer, "team": team, "score": f"{h}-{a}"})
        elif goal_period_order == current_period_order:
            goal_clock = abs_sec - goal_period_order * 1200
            if goal_clock <= clock_secs:
                goals.append({"time": _demo_fmt_clock(goal_clock), "period": period, "scorer": scorer, "team": team, "score": f"{h}-{a}"})
    return goals


def _demo_score_at(game: dict, period_idx: int, clock_secs: float) -> tuple:
    """Return (home_score, away_score) at current game time."""
    goals = _demo_goals_at(game, period_idx, clock_secs)
    if not goals:
        return 0, 0
    last = goals[-1]
    parts = last["score"].split("-")
    return int(parts[0]), int(parts[1])


def _demo_penalties_at(game: dict, period_idx: int, clock_secs: float) -> tuple:
    """Return active penalties (within 2 min window)."""
    period_key = _PERIOD_LABELS[period_idx][0]
    period_order = ["P1", "P2", "P3", "OT", "SO"]
    current_period_order = period_order.index(period_key) if period_key in period_order else 0
    abs_clock = current_period_order * 1200 + clock_secs
    active = []
    all_pen = []
    for (pen_abs, player, pen_team, mins, ptype) in game["penalties"]:
        all_pen.append({"player": player, "team": pen_team, "minutes": mins, "type": ptype,
                        "time": _demo_fmt_clock(pen_abs % 1200)})
        if pen_abs <= abs_clock <= pen_abs + mins * 60:
            active.append({"player": player, "team": pen_team, "minutes": mins, "type": ptype,
                           "time": _demo_fmt_clock(pen_abs % 1200)})
    return all_pen, active


def _demo_game_to_prev(game: dict, demo_team: str = "Demo FC") -> dict:
    """Build a 'previous' dict from a completed game definition."""
    is_home = game["is_home"]
    h, a = game["final_h"], game["final_a"]
    # For shootout games, the SO winner gets the extra point in display
    if game.get("shootout") and game.get("so_winner"):
        if game["so_winner"] == game["home"]:
            h += 1
        else:
            a += 1
    score_for = h if is_home else a
    score_against = a if is_home else h
    won = score_for > score_against
    return {
        "datetime": None,  # filled by caller
        "home_team": game["home"],
        "away_team": game["away"],
        "home_score": h,
        "away_score": a,
        "score_for": score_for,
        "score_against": score_against,
        "won": won,
        "overtime": game.get("overtime", False),
        "shootout": game.get("shootout", False),
        "venue": game["venue"],
        "round": game["round"],
        "period_scores": game.get("period_scores"),
    }


def _demo_game_to_next(game: dict) -> dict:
    """Build a 'next' dict from an upcoming game definition."""
    return {
        "datetime": None,  # filled by caller
        "home_team": game["home"],
        "away_team": game["away"],
        "is_home": game["is_home"],
        "opponent": game["away"] if game["is_home"] else game["home"],
        "venue": game["venue"],
        "round": game["round"],
    }


def _demo_now() -> dict:
    """Advance simulated time and return the demo team_now payload."""
    global _demo_state

    st = _demo_state
    rng = _random.Random(int(st["sim_minutes"] * 1000))

    # --- determine which game is "active" based on sim_minutes ---
    sim = st["sim_minutes"]
    now_iso = _now().isoformat()

    # If game_completed is set but sim is before the completed game's start, we've wrapped
    gidx = st.get("game_index")
    if st.get("game_completed") and gidx is not None:
        completed_game = _DEMO_GAMES[gidx]
        if sim < completed_game["start_min"]:
            # Wrapped around — reset game state
            st["game_completed"] = False
            st["game_index"] = None
            st["period_index"] = 0
            st["game_clock_seconds"] = None
            st["in_period_break"] = False

    # Find previous/current/next game relative to sim time
    WEEK = _DEMO_WEEK_MINUTES
    PRE_GAME_WINDOW_MIN = 120  # 2 hours before = pregame

    def _game_dt_str(game: dict) -> str:
        # Fake ISO datetime anchored to a Monday (2026-06-22 = Monday)
        base_day = {0: "2026-06-22", 1: "2026-06-25", 2: "2026-06-29"}
        day_str = base_day[game["idx"]]
        start_min = game["start_min"]
        h = (start_min % (24 * 60)) // 60
        m = start_min % 60
        return f"{day_str}T{h:02d}:{m:02d}:00+02:00"

    # Determine game states
    prev_game = None
    cur_game = None
    next_game = None
    cur_game_phase = "idle"

    for g in _DEMO_GAMES:
        gstart = g["start_min"]
        # Approximate game duration: 3 periods * 20min + breaks = ~75min, OT+SO adds ~20min
        gdur = 75 if not g.get("overtime") else 95
        gend = gstart + gdur

        if sim < gstart - PRE_GAME_WINDOW_MIN:
            # Before pregame window
            if next_game is None:
                next_game = g
        elif sim < gstart:
            # In pregame window
            cur_game = g
            cur_game_phase = "pregame"
        elif sim < gend:
            # During game
            cur_game = g
            cur_game_phase = "live"
        else:
            # After game
            prev_game = g

    # If we've passed all games (sim > last game end), wrap: prev=game2, next=game0 (next week)
    if prev_game is None and cur_game is None and next_game is None:
        prev_game = _DEMO_GAMES[2]
        next_game = _DEMO_GAMES[0]

    # Build prev dict
    prev_dict = None
    if prev_game:
        pd = _demo_game_to_prev(prev_game)
        pd["datetime"] = _game_dt_str(prev_game)
        prev_dict = pd

    # Build next dict
    next_dict = None
    if next_game and cur_game is None:
        nd = _demo_game_to_next(next_game)
        nd["datetime"] = _game_dt_str(next_game)
        next_dict = nd

    # Build current dict
    cur_dict = None
    if cur_game is not None:
        g = cur_game
        gstart = g["start_min"]
        gdur = 75 if not g.get("overtime") else 95
        gend = gstart + gdur
        game_dt_str = _game_dt_str(g)

        if cur_game_phase == "pregame":
            cur_dict = {
                "datetime": game_dt_str,
                "home_team": g["home"], "away_team": g["away"],
                "started": False, "is_live": False, "is_completed": False,
                "home_score": None, "away_score": None,
                "score_for": None, "score_against": None, "won": None,
                "period": None, "period_label": None, "period_clock": None,
                "is_overtime": False, "is_shootout": False,
                "goals": [], "last_goal": None, "penalties": [], "active_penalties": [],
                "venue": g["venue"], "round": g["round"],
            }
        else:
            # live: figure out period and clock from game_clock_seconds
            gc = st.get("game_clock_seconds") or 0.0
            pidx = st.get("period_index") or 0
            in_break = st.get("in_period_break") or False

            # Determine scores
            if g.get("shootout") and pidx == 4:
                # Shootout: show final SO result
                h_score, a_score = g["final_h"], g["final_a"]
                # SO winner gets +1 in display
                if g["so_winner"] == g["home"]:
                    h_score = g["final_h"] + 1
                else:
                    a_score = g["final_a"] + 1
                goals = _demo_goals_at(g, 3, 600)  # all OT goals
            else:
                h_score, a_score = _demo_score_at(g, pidx, gc)
                goals = _demo_goals_at(g, pidx, gc)

            all_pen, active_pen = _demo_penalties_at(g, pidx, gc)
            last_goal = goals[-1] if goals else None

            period_key, period_label = _PERIOD_LABELS.get(pidx, ("P1", "Period 1"))
            is_ot = pidx == 3
            is_so = pidx == 4

            is_completed = st.get("game_completed") or False
            is_live = not is_completed and not in_break

            if in_break:
                period_clock = None
                period_label = f"Break after {period_label}"
            elif is_so:
                period_clock = None
            else:
                period_clock = _demo_fmt_clock(gc)

            is_home = g["is_home"]
            score_for = h_score if is_home else a_score
            score_against = a_score if is_home else h_score
            won = None
            if is_completed:
                won = score_for > score_against

            cur_dict = {
                "datetime": game_dt_str,
                "home_team": g["home"], "away_team": g["away"],
                "started": True, "is_live": is_live, "is_completed": is_completed,
                "home_score": h_score, "away_score": a_score,
                "score_for": score_for, "score_against": score_against, "won": won,
                "period": period_key, "period_label": period_label,
                "period_clock": period_clock,
                "is_overtime": is_ot, "is_shootout": is_so,
                "goals": goals, "last_goal": last_goal,
                "penalties": all_pen, "active_penalties": active_pen,
                "venue": g["venue"], "round": g["round"],
            }

    # --- advance simulated time for next call ---
    _demo_advance_time(rng)

    return {"team": "demo", "updated_at": now_iso, "previous": prev_dict, "current": cur_dict, "next": next_dict}


def _demo_advance_time(rng: "_random.Random") -> None:
    """Mutate _demo_state to advance simulated time by one tick."""
    global _demo_state
    st = _demo_state
    sim = st["sim_minutes"]
    WEEK = _DEMO_WEEK_MINUTES

    PRE_GAME_WINDOW_MIN = 120

    # Find which game we're in/near
    active_game = None
    phase = "idle"
    for g in _DEMO_GAMES:
        gstart = g["start_min"]
        gdur = 75 if not g.get("overtime") else 95
        gend = gstart + gdur
        if gstart - PRE_GAME_WINDOW_MIN <= sim < gstart:
            active_game = g
            phase = "pregame"
            break
        if gstart <= sim < gend:
            active_game = g
            phase = "live"
            break

    if phase == "idle":
        # Check if we're close to a pregame window
        for g in _DEMO_GAMES:
            gstart = g["start_min"]
            if sim < gstart - PRE_GAME_WINDOW_MIN:
                # How far to pregame?
                dist = gstart - PRE_GAME_WINDOW_MIN - sim
                if dist <= 120:
                    # Within 2h of pregame start: advance 15 min
                    st["sim_minutes"] = (sim + 15) % WEEK
                else:
                    st["sim_minutes"] = (sim + 120) % WEEK
                return
        # Past all games this week
        st["sim_minutes"] = (sim + 120) % WEEK
        return

    if phase == "pregame":
        gstart = active_game["start_min"]
        dist_to_start = gstart - sim
        if dist_to_start > 120:
            st["sim_minutes"] = sim + 120
        else:
            st["sim_minutes"] = sim + 15
        # Reset game clock when we hit game start
        if st["sim_minutes"] >= gstart:
            st["sim_minutes"] = float(gstart)
            st["game_clock_seconds"] = 0.0
            st["period_index"] = 0
            st["in_period_break"] = False
            st["game_completed"] = False
            st["game_index"] = active_game["idx"]
        return

    if phase == "live":
        g = active_game
        pidx = st.get("period_index") or 0
        gc = st.get("game_clock_seconds") or 0.0
        in_break = st.get("in_period_break") or False
        completed = st.get("game_completed") or False

        if completed:
            # Game done, advance wall time
            st["sim_minutes"] = (sim + 120) % WEEK
            return

        if in_break:
            # One tick in break, then start next period
            st["in_period_break"] = False
            st["game_clock_seconds"] = 0.0
            return

        # Advance game clock by random 5-10 minutes
        chunk = rng.uniform(5 * 60, 10 * 60)
        period_dur = _PERIOD_DUR.get(pidx, 1200)

        if pidx == 4:
            # Shootout: one tick then complete
            st["game_completed"] = True
            st["sim_minutes"] = sim + 2  # small wall advance
            return

        new_gc = gc + chunk
        if new_gc >= period_dur:
            # Period ended
            max_period = 4 if g.get("shootout") else (3 if g.get("overtime") else 2)
            if pidx >= max_period:
                # Game over
                st["game_clock_seconds"] = float(period_dur)
                st["game_completed"] = True
                st["sim_minutes"] = sim + 2
            else:
                # Enter break
                st["game_clock_seconds"] = float(period_dur)
                st["in_period_break"] = True
                st["period_index"] = pidx + 1
        else:
            st["game_clock_seconds"] = new_gc
            st["sim_minutes"] = sim + chunk / 60  # keep wall time in sync


@app.get("/team/{team}/now")
async def team_now(team: str):
    """Previous / current / next summary for any watched team."""
    # --- demo intercept ---
    if team.lower() == "demo":
        return _demo_now()

    # --- real logic ---
    cfg = cfg_module.get()
    season_ids = list(watchlist.get_season_ids_for_team(team))
    if not season_ids:
        cfg_team = cfg.get("team")
        if cfg_team and team.lower() == cfg_team.lower():
            season_ids = cfg.get("season_ids") or []
    if not season_ids:
        raise HTTPException(
            status_code=404,
            detail=f"Team '{team}' is not watched. Add it with POST /watch.",
        )

    await _ensure_seasons_fresh(season_ids)
    fake_cfg = {**cfg, "team": team}
    games = _team_games(team, season_ids)
    now = _now()
    today = now.date()

    PRE_GAME_WINDOW = 7200

    today_games = [g for g in games if g["datetime"] and g["datetime"].date() == today]
    today_as_current = [
        g for g in today_games
        if (g["datetime"] - now).total_seconds() <= PRE_GAME_WINDOW
    ]
    today_as_next = [
        g for g in today_games
        if (g["datetime"] - now).total_seconds() > PRE_GAME_WINDOW
        and not g["is_completed"]
    ]
    before_today = [
        g for g in games
        if g["datetime"] and g["datetime"].date() < today and g["is_completed"]
    ]
    after_today = [
        g for g in games
        if g["datetime"] and g["datetime"].date() > today and not g["is_completed"]
    ]

    previous_game = max(before_today, key=lambda g: g["datetime"]) if before_today else None

    def _prev_dict(g: dict) -> dict:
        d = _game_to_dict(g, fake_cfg)
        return {
            "datetime": d["datetime_iso"],
            "home_team": d["home_team"],
            "away_team": d["away_team"],
            "home_score": d["home_score"],
            "away_score": d["away_score"],
            "score_for": d["score_for"],
            "score_against": d["score_against"],
            "won": d["won"],
            "overtime": bool(g.get("period_scores") and "OT" in (g["period_scores"] or "")),
            "shootout": bool(g.get("period_scores") and "SO" in (g["period_scores"] or "")),
            "venue": d["venue"],
            "round": d["round"],
            "period_scores": g.get("period_scores"),
        }

    current_data: Optional[dict] = None
    if today_as_current:
        tg = today_as_current[0]
        base = _game_to_dict(tg, fake_cfg)
        started = tg["datetime"] <= now
        is_live_now = _is_live(tg)
        is_done = tg["is_completed"]
        won: Optional[bool] = None
        if is_done and base["score_for"] is not None and base["score_against"] is not None:
            won = base["score_for"] > base["score_against"]
        current_data = {
            "datetime": base["datetime_iso"],
            "home_team": tg["home_team"],
            "away_team": tg["away_team"],
            "home_score": tg["home_score"],
            "away_score": tg["away_score"],
            "score_for": base["score_for"],
            "score_against": base["score_against"],
            "started": started,
            "is_live": is_live_now,
            "is_completed": is_done,
            "won": won,
            "period": None,
            "period_label": None,
            "period_clock": None,
            "is_overtime": False,
            "is_shootout": False,
            "goals": [],
            "last_goal": None,
            "penalties": [],
            "active_penalties": [],
            "venue": base["venue"],
            "round": base["round"],
        }
        if is_live_now:
            detail = _live_detail(tg)
            current_data.update({
                "home_score": detail["home_score"],
                "away_score": detail["away_score"],
                "period": detail["period"],
                "period_label": detail["period_label"],
                "period_clock": detail["period_clock"],
                "is_overtime": detail["is_overtime"],
                "is_shootout": detail["is_shootout"],
                "goals": detail.get("goals", []),
                "last_goal": detail.get("last_goal"),
                "penalties": detail.get("penalties", []),
                "active_penalties": detail.get("active_penalties", []),
            })

    next_candidates = sorted(today_as_next + after_today, key=lambda g: g["datetime"])
    next_game = next_candidates[0] if next_candidates else None

    def _next_dict(g: dict) -> dict:
        d = _game_to_dict(g, fake_cfg)
        return {
            "datetime": d["datetime_iso"],
            "home_team": d["home_team"],
            "away_team": d["away_team"],
            "is_home": d["is_home_game"],
            "opponent": d["opponent"],
            "venue": d["venue"],
            "round": d["round"],
        }

    return {
        "team": team,
        "updated_at": now.isoformat(),
        "previous": _prev_dict(previous_game) if previous_game else None,
        "current": current_data,
        "next": _next_dict(next_game) if next_game else None,
    }


@app.get("/teams/all")
async def teams_all():
    """All unique team names across every cached season — for config-flow step 1."""
    leagues_data = scraper._leagues_cache.get("data") or []
    if not leagues_data:
        await asyncio.to_thread(scraper.fetch_leagues)
        leagues_data = scraper._leagues_cache.get("data") or []

    all_season_ids = [
        sub["season_id"]
        for lg in leagues_data
        for sub in lg.get("sub_competitions", [])
        if sub.get("season_id")
    ]
    if all_season_ids:
        await _ensure_seasons_fresh(all_season_ids)

    def _is_valid_team_name(name: str) -> bool:
        if not name or len(name.strip()) < 3:
            return False
        if name.strip().isdigit():
            return False
        return True

    team_set: set[str] = set()
    for entry in _schedule_cache.values():
        for g in entry.get("games", []):
            ht = g.get("home_team", "")
            at = g.get("away_team", "")
            if _is_valid_team_name(ht):
                team_set.add(ht)
            if _is_valid_team_name(at):
                team_set.add(at)

    sorted_teams = sorted(team_set)
    return {"teams": ["demo"] + sorted_teams}


@app.get("/team/{team}/leagues")
async def team_leagues(team: str):
    """All sub-competitions where *team* has at least one game."""
    if team.lower() == "demo":
        return {
            "team": "demo",
            "competitions": [
                {"league": "Demo", "name": "Demo", "season_id": 0, "game_count": 5}
            ],
        }

    leagues_data = scraper._leagues_cache.get("data") or []
    if not leagues_data:
        await asyncio.to_thread(scraper.fetch_leagues)
        leagues_data = scraper._leagues_cache.get("data") or []

    all_season_ids = [
        sub["season_id"]
        for lg in leagues_data
        for sub in lg.get("sub_competitions", [])
        if sub.get("season_id")
    ]
    if all_season_ids:
        await _ensure_seasons_fresh(all_season_ids)

    _VIEW_SUFFIX = re.compile(r'\s*[–-]\s*(Games|Players|Teams|Schedule|Overview)\s*$', re.IGNORECASE)
    _VIEW_ONLY_NAMES = frozenset({"games", "players", "teams", "schedule", "overview", "results"})

    def _clean_comp_name(name: str, league: str = "") -> str:
        cleaned = _VIEW_SUFFIX.sub('', name).strip()
        if cleaned.lower() in _VIEW_ONLY_NAMES:
            return league or cleaned
        return cleaned

    team_lower = team.lower()
    competitions: list[dict] = []
    for lg in leagues_data:
        for sub in lg.get("sub_competitions", []):
            sid = sub.get("season_id")
            if not sid:
                continue
            entry = _schedule_cache.get(sid, {})
            count = sum(
                1 for g in entry.get("games", [])
                if (g.get("home_team") or "").lower() == team_lower
                or (g.get("away_team") or "").lower() == team_lower
            )
            if count > 0:
                competitions.append({
                    "league": _clean_comp_name(lg["league"]),
                    "name": _clean_comp_name(sub["name"], _clean_comp_name(lg["league"])),
                    "season_id": sid,
                    "game_count": count,
                })

    seen_season_ids: set[int] = set()
    unique_competitions = []
    for comp in competitions:
        if comp["season_id"] not in seen_season_ids:
            seen_season_ids.add(comp["season_id"])
            unique_competitions.append(comp)
    competitions = unique_competitions

    return {"team": team, "competitions": competitions}
