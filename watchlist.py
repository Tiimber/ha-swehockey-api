"""
watchlist.py – Persistent watchlist of team/season combinations.

Storage: watchlist.json in the same directory as config.yaml (CWD or /data/).

Each watch entry has a deterministic ID based on the team name and sorted
season_ids, so posting the same combination always returns the same ID.

Structure of watchlist.json (v2):
    {
        "watches": {
            "a1b2c3d4": {
                "id":         "a1b2c3d4",
                "team":       "HV 71",
                "season_ids": [18263, 19791]
            }
        }
    }

v1 format ({team: [ids]}) is automatically migrated on first load.
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Mirror config.py search order
_CANDIDATES = [
    Path(os.environ.get("HOCKEY_CONFIG", "")).parent / "watchlist.json"
    if os.environ.get("HOCKEY_CONFIG")
    else None,
    Path("/data/watchlist.json"),
    Path("/config/watchlist.json"),
    Path("watchlist.json"),
]


def _watchlist_path() -> Path:
    for p in _CANDIDATES:
        if p and p.exists():
            return p
    for p in _CANDIDATES:
        if p and p.parent.exists():
            return p
    return Path("watchlist.json")


def generate_id(team: str, season_ids: list[int]) -> str:
    """Return a deterministic 8-char hex ID for a team+season_ids combination."""
    key = f"{team.lower()}:{sorted(season_ids)}"
    return hashlib.sha1(key.encode()).hexdigest()[:8]


def _migrate_v1(old: dict) -> dict:
    """Migrate from {team: [season_ids]} to {id: {id, team, season_ids}}."""
    new_watches: dict = {}
    for team, season_ids in old.get("watches", {}).items():
        if not isinstance(season_ids, list):
            continue
        watch_id = generate_id(team, season_ids)
        new_watches[watch_id] = {
            "id": watch_id,
            "team": team,
            "season_ids": sorted(season_ids),
        }
    return {"watches": new_watches}


def _load() -> dict:
    path = _watchlist_path()
    if path.exists():
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("root is not a dict")
            # Detect v1 format: values are lists instead of dicts
            watches = data.get("watches", {})
            first_val = next(iter(watches.values()), None)
            if isinstance(first_val, list):
                logger.info("Migrating watchlist from v1 format")
                data = _migrate_v1(data)
                _save(data)
            return data
        except Exception as exc:
            logger.error("Failed to load watchlist (%s): %s", _watchlist_path(), exc)
    return {"watches": {}}


def _save(data: dict) -> None:
    path = _watchlist_path()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.error("Failed to save watchlist (%s): %s", path, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_watches() -> dict[str, dict]:
    """Return {id: {id, team, season_ids}} for all watched entries."""
    return dict(_load().get("watches", {}))


def get_watch(watch_id: str) -> Optional[dict]:
    """Return the watch entry for *watch_id*, or None if not found."""
    return _load()["watches"].get(watch_id)


def add_watch(team: str, season_ids: list[int]) -> tuple[str, bool]:
    """
    Add a watch entry for team across the given season_ids.
    The ID is deterministic: re-posting the same team+season_ids returns the
    same ID with was_created=False.

    Returns (watch_id, was_created).
    """
    watch_id = generate_id(team, season_ids)
    data = _load()
    watches: dict = data.setdefault("watches", {})
    if watch_id in watches:
        return watch_id, False
    watches[watch_id] = {
        "id": watch_id,
        "team": team,
        "season_ids": sorted(season_ids),
    }
    _save(data)
    return watch_id, True


def remove_watch(watch_id: str) -> bool:
    """Remove a watch entry by ID. Returns True if removed, False if not found."""
    data = _load()
    watches: dict = data.get("watches", {})
    if watch_id not in watches:
        return False
    del watches[watch_id]
    _save(data)
    return True


def find_watches_for_team(team: str) -> list[dict]:
    """Return all watch entries where team name matches (case-insensitive)."""
    tl = team.lower()
    return [w for w in get_watches().values() if w["team"].lower() == tl]


def find_team_canonical(team: str) -> Optional[str]:
    """Return the canonical team name from any matching watch entry."""
    tl = team.lower()
    for w in get_watches().values():
        if w["team"].lower() == tl:
            return w["team"]
    return None


def all_watched_season_ids() -> set[int]:
    """Return the set of all season_ids referenced anywhere in the watchlist."""
    ids: set[int] = set()
    for w in get_watches().values():
        ids.update(w["season_ids"])
    return ids
