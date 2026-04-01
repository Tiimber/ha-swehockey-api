"""
app.py – FastAPI mini-API for Swedish ice hockey schedule and live results.

Endpoints
---------
GET /               API info & configured team
GET /next           Next (or currently ongoing) match
GET /last           Last completed match result
GET /live           Live match data (period, score); 404 if no live game
GET /status         Combined snapshot – ideal for Home Assistant
GET /schedule       Full schedule for the configured team
GET /teams          All teams found in configured seasons
GET /refresh        Force cache refresh (admin use)
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

import config as cfg_module
import scraper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_cache: dict = {
    "games": [],           # merged games from all configured season_ids
    "fetched_at": None,    # datetime
}

# Poll intervals (seconds)
CACHE_TTL_LIVE     = 30     # game in progress
CACHE_TTL_GAME_DAY = 3600   # game scheduled today, not yet started
CACHE_TTL_IDLE     = 21600  # no game today (6 hours)


def _is_today(game: dict) -> bool:
    """Return True if the game is scheduled for today (Stockholm time)."""
    dt = game.get("datetime")
    return bool(dt and dt.date() == datetime.now(STOCKHOLM_TZ).date())


def _current_ttl() -> int:
    """Return the appropriate cache TTL based on today's schedule."""
    now = datetime.now(STOCKHOLM_TZ)
    for g in _cache["games"]:
        dt = g.get("datetime")
        if not dt:
            continue
        delta = (now - dt).total_seconds()
        if 0 < delta < 14400 and not g["is_completed"]:
            return CACHE_TTL_LIVE   # live or potentially live
        if dt.date() == now.date():
            return CACHE_TTL_GAME_DAY  # game today, hasn't started yet
    return CACHE_TTL_IDLE


def _cache_stale(cfg: dict) -> bool:  # noqa: ARG001
    fetched = _cache["fetched_at"]
    if fetched is None:
        return True
    return (datetime.now(STOCKHOLM_TZ) - fetched).total_seconds() > _current_ttl()


async def _refresh_cache(cfg: dict) -> None:
    """Fetch all seasons and update the in-memory cache."""
    loop = asyncio.get_event_loop()
    team = cfg["team"]
    season_ids = cfg["season_ids"]

    all_games: list[dict] = []
    for sid in season_ids:
        games = await loop.run_in_executor(
            None, scraper.fetch_schedule, sid
        )
        team_games = scraper.filter_team_games(games, team)
        all_games.extend(team_games)

    # Sort by datetime (None datetimes go last)
    all_games.sort(key=lambda g: g["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ))

    _cache["games"] = all_games
    _cache["fetched_at"] = datetime.now(STOCKHOLM_TZ)
    logger.info(
        "Cache refreshed: %d games for %s across %d season(s)",
        len(all_games),
        team,
        len(season_ids),
    )


async def _ensure_fresh(cfg: dict) -> None:
    if _cache_stale(cfg):
        await _refresh_cache(cfg)


# ---------------------------------------------------------------------------
# Background auto-refresh
# ---------------------------------------------------------------------------

async def _auto_refresh_loop(cfg: dict) -> None:
    while True:
        try:
            await _refresh_cache(cfg)
        except Exception as exc:
            logger.error("Auto-refresh error: %s", exc)

        ttl = _current_ttl()

        # When no future games remain, check swehockey.se for new seasons.
        now = datetime.now(STOCKHOLM_TZ)
        has_future = any(
            g["datetime"] and g["datetime"] > now for g in _cache["games"]
        )
        if not has_future and _cache["games"]:
            loop = asyncio.get_event_loop()
            try:
                new_ids = await loop.run_in_executor(
                    None, scraper.discover_new_seasons, cfg["season_ids"]
                )
                if new_ids:
                    logger.info(
                        "New season ID(s) found on swehockey.se: %s – "
                        "add them to season_ids in config.yaml",
                        new_ids,
                    )
            except Exception as exc:
                logger.warning("Season discovery failed: %s", exc)

        await asyncio.sleep(ttl)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = cfg_module.get()
    await _refresh_cache(cfg)
    task = asyncio.create_task(_auto_refresh_loop(cfg))
    yield
    task.cancel()


app = FastAPI(
    title="HockeyLive API",
    description="Swedish ice hockey schedule and live results from swehockey.se",
    version="1.0.0",
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
    Fetch game events and build period/score detail for a live game.
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
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    cfg = cfg_module.get()
    return {
        "api": "HockeyLive API",
        "version": "1.0.0",
        "team": cfg["team"],
        "season_ids": cfg["season_ids"],
        "source": "stats.swehockey.se",
        "endpoints": ["/next", "/last", "/live", "/status", "/schedule", "/teams", "/refresh"],
        "cache_fetched_at": (
            _cache["fetched_at"].isoformat() if _cache["fetched_at"] else None
        ),
    }


@app.get("/next")
async def next_match():
    """Next upcoming or currently ongoing match."""
    cfg = cfg_module.get()
    await _ensure_fresh(cfg)

    now = _now()
    games = _cache["games"]

    # First check for a live game
    for g in games:
        if _is_live(g):
            return {"status": "live", "game": _game_to_dict(g, cfg)}

    # Then find the next upcoming game (datetime in the future)
    for g in sorted(games, key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ)):
        if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
            return {"status": "upcoming", "game": _game_to_dict(g, cfg)}

    raise HTTPException(status_code=404, detail="No upcoming match found.")


@app.get("/last")
async def last_match():
    """Most recently completed match and its result."""
    cfg = cfg_module.get()
    await _ensure_fresh(cfg)

    completed = [g for g in _cache["games"] if g["is_completed"]]
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

    for g in _cache["games"]:
        if _is_live(g):
            base = _game_to_dict(g, cfg)
            detail = _live_detail(g)
            return {"status": "live", "game": {**base, **detail}}

    return JSONResponse(
        status_code=404,
        content={"status": "no_live_game", "detail": "No match is currently in progress."},
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
    games = _cache["games"]
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
        for g in sorted(games, key=lambda x: x["datetime"] or datetime.max.replace(tzinfo=STOCKHOLM_TZ)):
            if g["datetime"] and g["datetime"] > now and not g["is_completed"]:
                next_game = g
                break
    next_data: Optional[dict] = None
    if next_game:
        next_data = _game_to_dict(next_game, cfg)

    # --- last ---
    completed = [g for g in games if g["is_completed"]]
    last_game = (
        max(completed, key=lambda g: g["datetime"] or datetime.min.replace(tzinfo=STOCKHOLM_TZ))
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
    games = _cache["games"]
    now = _now()
    today = now.date()

    PRE_GAME_WINDOW = 7200  # seconds before kick-off to promote game to "current"

    # Partition today's games: promoted to current once within 2 h of start.
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

    # --- previous: last completed game before today ---
    previous_game = (
        max(before_today, key=lambda g: g["datetime"])
        if before_today else None
    )

    def _prev_dict(g: dict) -> dict:
        d = _game_to_dict(g, cfg)
        return {
            "home_team":   d["home_team"],
            "away_team":   d["away_team"],
            "home_score":  d["home_score"],
            "away_score":  d["away_score"],
            "score_for":   d["score_for"],
            "score_against": d["score_against"],
            "won":         d["won"],
            "overtime":    bool(g.get("period_scores") and "OT" in (g["period_scores"] or "")),
            "shootout":    bool(g.get("period_scores") and "SO" in (g["period_scores"] or "")),
            "datetime":    d["datetime_iso"],
            "venue":       d["venue"],
            "round":       d["round"],
        }

    # --- current: today's game if within 2 h (or already started/done) ---
    current_data: Optional[dict] = None
    if today_as_current:
        tg = today_as_current[0]
        base = _game_to_dict(tg, cfg)
        started    = tg["datetime"] <= now
        is_live_now = _is_live(tg)
        is_done    = tg["is_completed"]
        won: Optional[bool] = None
        if is_done and base["score_for"] is not None and base["score_against"] is not None:
            won = base["score_for"] > base["score_against"]

        current_data = {
            "home_team":    tg["home_team"],
            "away_team":    tg["away_team"],
            "home_score":   tg["home_score"],
            "away_score":   tg["away_score"],
            "score_for":    base["score_for"],
            "score_against": base["score_against"],
            "started":      started,
            "is_live":      is_live_now,
            "is_completed": is_done,
            "won":          won,
            "datetime":     base["datetime_iso"],
            "venue":        base["venue"],
            "round":        base["round"],
            # Live/completed fields (filled below when started)
            "period":       None,
            "period_label": None,
            "period_clock": None,
            "is_overtime":  False,
            "is_shootout":  False,
        }
        if is_live_now:
            detail = _live_detail(tg)
            current_data.update({
                "home_score":   detail["home_score"],
                "away_score":   detail["away_score"],
                "period":       detail["period"],
                "period_label": detail["period_label"],
                "period_clock": detail["period_clock"],
                "is_overtime":  detail["is_overtime"],
                "is_shootout":  detail["is_shootout"],
            })

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
            "datetime":  d["datetime_iso"],
            "venue":     d["venue"],
            "round":     d["round"],
            "is_home":   d["is_home_game"],
            "opponent":  d["opponent"],
        }

    return {
        "team":       team,
        "updated_at": now.isoformat(),
        "previous":   _prev_dict(previous_game) if previous_game else None,
        "current":    current_data,
        "next":       _next_dict(next_game) if next_game else None,
    }


@app.get("/schedule")
async def schedule():
    """Full schedule for the configured team (all seasons)."""
    cfg = cfg_module.get()
    await _ensure_fresh(cfg)

    return {
        "team": cfg["team"],
        "season_ids": cfg["season_ids"],
        "games": [_game_to_dict(g, cfg) for g in _cache["games"]],
        "total": len(_cache["games"]),
    }


@app.get("/teams")
async def teams():
    """
    List all team names found in the configured seasons.
    Useful for discovering the exact team name spelling used by swehockey.se.
    """
    cfg = cfg_module.get()
    season_ids = cfg["season_ids"]
    loop = asyncio.get_event_loop()

    all_teams: set[str] = set()
    for sid in season_ids:
        season_teams = await loop.run_in_executor(
            None, scraper.list_teams_in_season, sid
        )
        all_teams.update(season_teams)

    return {"season_ids": season_ids, "teams": sorted(all_teams)}


@app.get("/refresh")
async def force_refresh():
    """Force an immediate cache refresh."""
    cfg = cfg_module.get()
    await _refresh_cache(cfg)
    return {
        "ok": True,
        "games_loaded": len(_cache["games"]),
        "fetched_at": _cache["fetched_at"].isoformat(),
    }
