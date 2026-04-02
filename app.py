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
    return set(cfg["season_ids"]) | watchlist.all_watched_season_ids()


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
    if team.lower() == cfg["team"].lower():
        return cfg["season_ids"]
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
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mqtt_pub
    cfg = cfg_module.get()
    for sid in _all_known_season_ids(cfg):
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
        await _publish_all_watch_states()
        print("[MQTT] Startup complete", flush=True)
    else:
        print("[MQTT] Disabled (mqtt_host is empty)", flush=True)

    task = asyncio.create_task(_auto_refresh_loop(cfg))
    yield
    task.cancel()
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
      - it is not yet completed (no period_scores)
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
    events_data = scraper.fetch_game_events(game["game_id"])

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
        # Heuristic: estimate period from elapsed real time
        # Approx intermission-aware breakpoints (minutes after puck drop):
        #   P1: 0–25, intermission1: 25–42, P2: 42–67,
        #   intermission2: 67–84, P3: 84–109, OT: 109–125, SO: 125+
        elapsed = (_now() - game["datetime"]).total_seconds() / 60
        if elapsed < 25:
            period = "P1"
        elif elapsed < 42:
            period = "P1"  # still in first intermission, game clock stopped
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
        home_score = game["home_score"] or 0
        away_score = game["away_score"] or 0
        period_clock = None
        is_overtime = period == "OT"
        is_shootout = period == "SO"
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


@app.get("/next")
async def next_match():
    """Next upcoming or currently ongoing match."""
    cfg = cfg_module.get()
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

    return {
        "team": team,
        "season_ids": season_ids,
        "updated_at": now.isoformat(),
        "live": live_data,
        "next_match": _game_to_dict(next_game, fake_cfg) if next_game else None,
        "last_match": _game_to_dict(last_game, fake_cfg) if last_game else None,
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
