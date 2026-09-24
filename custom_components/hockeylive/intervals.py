"""intervals.py – choose the coordinator's next poll interval from a /now payload.

Kept free of Home Assistant imports so it can be exercised from a plain
Python prompt.

The old rule polled once an hour on game day and only re-read the interval on
each poll, so the switch to live polling happened at the first hourly tick
after face-off: a 19:00 game went live on the panel at 19:40. The board is
only as live as the coordinator, so the interval now counts down to the two
moments that change the board – the API promoting today's game to "current"
(PRE_GAME_WINDOW_SECONDS before face-off) and face-off itself – and polls at
the live rate from face-off on, since the feed can lag the puck drop.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .const import (
    PRE_GAME_WINDOW_SECONDS,
    UPDATE_INTERVAL_GAME_DAY,
    UPDATE_INTERVAL_IDLE,
    UPDATE_INTERVAL_LIVE,
)


def _seconds_until(game: dict, now: datetime) -> float | None:
    raw = game.get("datetime")
    if not raw:
        return None
    try:
        start = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return (start - now).total_seconds()


def _bounded(seconds: float) -> int:
    """Never faster than live polling, never slower than the game-day tick."""
    return int(max(UPDATE_INTERVAL_LIVE, min(UPDATE_INTERVAL_GAME_DAY, seconds)))


def pick_update_interval(data: dict, now: datetime | None = None) -> int:
    """Seconds until the coordinator should poll again."""
    now = now or datetime.now(timezone.utc)
    current = data.get("current") or {}
    nxt = data.get("next") or {}

    if current.get("is_live"):
        return UPDATE_INTERVAL_LIVE

    if current and not current.get("is_completed"):
        # Promoted but not yet live: wake exactly at face-off, then poll at
        # the live rate until the API agrees the game is on.
        until = _seconds_until(current, now)
        if until is None or until <= 0:
            return UPDATE_INTERVAL_LIVE
        return _bounded(until)

    if nxt:
        # Wake when the API will promote it, so the pre-game board appears on
        # time rather than up to an hour later.
        until = _seconds_until(nxt, now)
        if until is None:
            return UPDATE_INTERVAL_GAME_DAY
        return _bounded(until - PRE_GAME_WINDOW_SECONDS)

    if current or data.get("previous"):
        return UPDATE_INTERVAL_GAME_DAY
    return UPDATE_INTERVAL_IDLE
