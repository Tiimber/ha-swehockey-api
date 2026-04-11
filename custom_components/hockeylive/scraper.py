"""
scraper.py – Fetches and parses schedule / live data from stats.swehockey.se.
Bundled inside the custom integration so HA can import it directly.

Two observed table layouts on swehockey.se:

8-cell rows (regular season):
  [0] Date "YYYY-MM-DD" OR carry-over time
  [1] "YYYY-MM-DD HH:MM" OR empty for same-day games
  [2] Time "HH:MM"
  [3] "Home\\n - \\nAway"
  [4] "H - A" score (contains game link)
  [5] "(P1, P2, …)" period scores
  [6] Spectators
  [7] Venue

7-cell rows (playoffs):
  [0] Empty or round label
  [1] "YYYY-MM-DD\\xa019:00" date+time with nbsp
  [2] "Home\\n - \\nAway [RoundName]"
  [3] "H - A" score (contains game link) OR empty
  [4] "(P1, P2, …)" OR empty
  [5] Spectators OR empty
  [6] Venue
"""

import re
import time
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://stats.swehockey.se"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HockeyLiveHA/1.0; personal-use)"}
STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.5  # minimum seconds between outgoing requests

logger = logging.getLogger(__name__)

# Shared rate-limiter (thread-safe: serialises all requests from all teams)
_request_lock = threading.Lock()
_last_request: float = 0.0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _get(url: str) -> Optional[str]:
    global _last_request
    with _request_lock:
        wait = REQUEST_DELAY - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            # swehockey.se returns text/html without charset; force UTF-8
            resp.encoding = "utf-8"
            return resp.text
        except requests.RequestException as exc:
            logger.error("Request failed [%s]: %s", url, exc)
            return None
        finally:
            _last_request = time.monotonic()


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


def fetch_schedule(season_id: int) -> list[dict]:
    """Return all games for *season_id* as a list of game dicts."""
    url = f"{BASE_URL}/ScheduleAndResults/Schedule/{season_id}"
    html = _get(url)
    return _parse_schedule(html, season_id) if html else []


def _cell_text(td) -> str:
    return re.sub(
        r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")
    ).strip()


_ROUND_RE = re.compile(
    r"\s+(Åttondelsfinal|Kvartsfinal|Semifinal|SM-final|Final)\s*\d*\s*$",
    re.IGNORECASE,
)


def _parse_schedule(html: str, season_id: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    games: list[dict] = []
    current_date: Optional[str] = None

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        n = len(cells)
        if n not in (7, 8):
            continue

        texts = [_cell_text(c) for c in cells]
        date_str = time_str = game_text = score_text = period_text = venue_text = None
        score_cell_idx = 0

        if n == 8:
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", texts[1])
            if m:
                date_str, time_str = m.group(1), m.group(2)
                current_date = date_str
            elif current_date and re.fullmatch(r"\d{2}:\d{2}", texts[2]):
                date_str, time_str = current_date, texts[2]
            game_text, score_text, period_text, venue_text = (
                texts[3],
                texts[4],
                texts[5],
                texts[7],
            )
            score_cell_idx = 4

        elif n == 7:
            dt_raw = cells[1].get_text(" ", strip=True).replace("\xa0", " ").strip()
            m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", dt_raw)
            if m:
                date_str, time_str = m.group(1), m.group(2)
                current_date = date_str
            elif current_date:
                tm = re.search(r"(\d{2}:\d{2})$", dt_raw)
                if tm:
                    date_str, time_str = current_date, tm.group(1)
            game_text, score_text, period_text, venue_text = (
                texts[2],
                texts[3],
                texts[4],
                texts[6],
            )
            score_cell_idx = 3

        if not date_str or not game_text or " - " not in game_text:
            continue

        parts = game_text.split(" - ", 1)
        home_team = parts[0].strip()
        away_raw = parts[1].strip() if len(parts) > 1 else ""

        round_name: Optional[str] = None
        rm = _ROUND_RE.search(away_raw)
        if rm:
            away_team = away_raw[: rm.start()].strip()
            round_name = rm.group(1).strip()
        else:
            away_team = away_raw

        if not home_team or not away_team:
            continue

        game_id: Optional[int] = None
        game_id_in_score_cell = False
        for ci in range(score_cell_idx, min(score_cell_idx + 3, n)):
            link = cells[ci].find("a")
            if link:
                gm = re.search(r"Game/Events/(\d+)", link.get("href", ""))
                if gm:
                    game_id = int(gm.group(1))
                    game_id_in_score_cell = (ci == score_cell_idx)
                    break

        home_score = away_score = None
        sm = re.fullmatch(r"(\d+)\s*-\s*(\d+)", score_text or "")
        if sm:
            home_score, away_score = int(sm.group(1)), int(sm.group(2))

        period_scores = period_text if (period_text or "").startswith("(") else None
        venue = venue_text if venue_text else None

        game_dt: Optional[datetime] = None
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            game_dt = naive.replace(tzinfo=STOCKHOLM_TZ)
        except ValueError:
            pass

        # A game is completed when period_scores is present (any period text means
        # swehockey added the result block) OR when the Game/Events link is in the
        # score cell (swehockey places it there only for completed games).
        games.append(
            {
                "season_id": season_id,
                "game_id": game_id,
                "date": date_str,
                "time": time_str,
                "datetime": game_dt,
                "home_team": home_team,
                "away_team": away_team,
                "round": round_name,
                "home_score": home_score,
                "away_score": away_score,
                "period_scores": period_scores,
                "venue": venue,
                "is_completed": home_score is not None and (period_scores is not None or game_id_in_score_cell),
            }
        )

    return games


# ---------------------------------------------------------------------------
# Game events (live data)
# ---------------------------------------------------------------------------

_PERIOD_FROM_MINUTES = [
    (0, 20, "P1"),
    (20, 40, "P2"),
    (40, 60, "P3"),
    (60, 65, "OT"),
    (65, 9999, "SO"),
]


def _minutes_to_period(total_minutes: float) -> str:
    for lo, hi, label in _PERIOD_FROM_MINUTES:
        if lo <= total_minutes < hi:
            return label
    return "SO"


def fetch_game_events(game_id: int) -> dict:
    url = f"{BASE_URL}/Game/Events/{game_id}"
    html = _get(url)
    return _parse_game_events(html) if html else {}


def _parse_game_events(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    result: dict = {
        "home_score": 0,
        "away_score": 0,
        "period": None,
        "period_clock": None,
        "is_overtime": False,
        "is_shootout": False,
        "events": [],
    }

    header_score = re.search(r"(\d+)\s*[-–]\s*(\d+)", soup.get_text()[:2000])
    if header_score:
        result["home_score"] = int(header_score.group(1))
        result["away_score"] = int(header_score.group(2))

    events: list[dict] = []
    last_time_str: Optional[str] = None
    last_period: Optional[str] = None
    current_period_label: Optional[str] = None

    for table in soup.find_all("table"):
        current_period_label = None
        for row in table.find_all("tr"):
            header = row.find("th")
            if header:
                ht = header.get_text(strip=True)
                if ht in ("1", "2", "3"):
                    current_period_label = f"P{ht}"
                    last_period = current_period_label
                elif ht.upper() in ("OT", "OT5", "FLD", "FÖRL"):
                    current_period_label = last_period = "OT"
                elif ht.upper() in ("SO", "PSO", "STRAFFAR"):
                    current_period_label = last_period = "SO"
                continue

            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            cell_texts = [
                re.sub(
                    r"\s+", " ", c.get_text(" ", strip=True).replace("\xa0", " ")
                ).strip()
                for c in cells
            ]
            # New format period headers: single-cell rows like "3rd period"
            if len(cells) == 1:
                h_upper = cell_texts[0].upper()
                _HDR_MAP = {
                    "1ST PERIOD": "P1",
                    "2ND PERIOD": "P2",
                    "3RD PERIOD": "P3",
                    "OT": "OT",
                    "OVERTIME": "OT",
                    "SO": "SO",
                    "SHOOTOUT": "SO",
                }
                if h_upper in _HDR_MAP:
                    current_period_label = last_period = _HDR_MAP[h_upper]
                continue
            time_cell = cell_texts[0]
            if not re.fullmatch(r"\d{1,3}:\d{2}", time_cell):
                continue

            last_time_str = time_cell
            try:
                mins, secs = map(int, time_cell.split(":"))
                inferred = _minutes_to_period(mins + secs / 60)
                if current_period_label is None:
                    current_period_label = inferred
                last_period = current_period_label
            except ValueError:
                pass

            event_type = cell_texts[1] if len(cell_texts) > 1 else ""
            team = cell_texts[2] if len(cell_texts) > 2 else ""
            players_text = cell_texts[3] if len(cell_texts) > 3 else ""
            extra_text = cell_texts[4] if len(cell_texts) > 4 else ""
            events.append(
                {
                    "time": time_cell,
                    "period": current_period_label,
                    "type": event_type,
                    "team": team,
                    "players": players_text,
                    "extra": extra_text,
                }
            )

        if events:
            break

    goals: list[dict] = []
    penalties: list[dict] = []
    _ACTIONS_GOAL_RE = re.compile(r"^(\d+)-(\d+)\s*\(([^)]+)\)\s*$")
    _ACTIONS_PEN_RE = re.compile(r"^(\d+)\s*min\b", re.I)
    _VALID_DURS = {2, 4, 5, 10, 20, 25}

    for ev in events:
        game_time = ev["time"]
        period = ev["period"] or ""
        event_type = ev["type"]
        team = ev["team"]
        players_text = ev["players"]
        extra_text = ev["extra"]
        try:
            mm, ss = map(int, game_time.split(":"))
            game_secs = mm * 60 + ss
        except ValueError:
            game_secs = 0
        offset_secs = {"P1": 0, "P2": 1200, "P3": 2400, "OT": 3600, "SO": 3900}.get(
            period, 0
        )
        period_clock_secs = max(0, game_secs - offset_secs)
        period_clock = f"{period_clock_secs // 60:02d}:{period_clock_secs % 60:02d}"

        gm = _ACTIONS_GOAL_RE.match(event_type)
        if gm:
            home_after, away_after = int(gm.group(1)), int(gm.group(2))
            situation = gm.group(3).strip()
            # Parse players: "34. Brodin, Daniel (1) 32. Olofsson, Jacob"
            cleaned = re.sub(r"\(\d+\)", "", players_text)  # remove goal count
            parts = [p.strip() for p in re.split(r"\b\d+\.\s*", cleaned) if p.strip()]

            def _fmt(name: str) -> str:
                if "," in name:
                    last, first = name.split(",", 1)
                    return f"{first.strip()} {last.strip()}"
                return name

            scorer = _fmt(parts[0]) if parts else players_text
            assists = [_fmt(p) for p in parts[1:]]
            goals.append(
                {
                    "game_time": game_time,
                    "game_time_secs": game_secs,
                    "period": period,
                    "period_clock": period_clock,
                    "team": team,
                    "scorer": scorer,
                    "assists": assists,
                    "situation": situation,
                    "home_score_after": home_after,
                    "away_score_after": away_after,
                    "secs_since": 0,
                }
            )
        else:
            pm = _ACTIONS_PEN_RE.match(event_type)
            if pm:
                dur = int(pm.group(1))
                if dur not in _VALID_DURS:
                    dur = None
                pen_parts = [
                    p.strip()
                    for p in re.split(r"\b\d+\.\s*", players_text)
                    if p.strip()
                ]
                player_raw = pen_parts[0] if pen_parts else players_text
                player_raw = player_raw.rstrip(",")
                if "," in player_raw:
                    last, first = player_raw.split(",", 1)
                    player = f"{first.strip()} {last.strip()}"
                else:
                    player = player_raw
                offense = re.sub(
                    r"\s*\(\d+:\d+\s*-\s*\d+:\d+\)\s*$", "", extra_text
                ).strip()
                penalties.append(
                    {
                        "game_time": game_time,
                        "game_time_secs": game_secs,
                        "period": period,
                        "period_clock": period_clock,
                        "team": team,
                        "player": player,
                        "duration_min": dur,
                        "offense": offense,
                        "is_active": False,
                    }
                )

    # Events come newest-first; reverse goals to chronological order
    goals_chrono = list(reversed(goals))

    if events:
        result["events"] = events
        result["period"] = last_period
        result["is_overtime"] = last_period == "OT"
        result["is_shootout"] = last_period == "SO"
        result["goals"] = goals_chrono
        result["last_goal"] = goals_chrono[-1] if goals_chrono else None
        result["penalties"] = penalties
        result["active_penalties"] = []
        if last_time_str and last_period:
            try:
                mins, secs = map(int, last_time_str.split(":"))
                offset = {"P1": 0, "P2": 20, "P3": 40, "OT": 60, "SO": 65}.get(
                    last_period, 0
                )
                result["period_clock"] = (
                    f"{int(mins + secs / 60 - offset):02d}:{secs:02d}"
                )
            except ValueError:
                result["period_clock"] = last_time_str

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def list_teams_in_season(season_id: int) -> list[str]:
    games = fetch_schedule(season_id)
    teams: set[str] = set()
    for g in games:
        if g["home_team"]:
            teams.add(g["home_team"])
        if g["away_team"]:
            teams.add(g["away_team"])
    return sorted(teams)


def filter_team_games(games: list[dict], team_name: str) -> list[dict]:
    tl = team_name.lower()
    return [
        g
        for g in games
        if (g["home_team"] or "").lower() == tl or (g["away_team"] or "").lower() == tl
    ]


# ---------------------------------------------------------------------------
# New season discovery
# ---------------------------------------------------------------------------


def discover_new_seasons(known_ids: list[int]) -> list[int]:
    """
    Fetch the swehockey.se front page and return any Schedule season IDs
    that are not in *known_ids*.  Call this when no future games remain so
    the user / integration can be notified that new seasons are available.
    """
    html = _get(f"{BASE_URL}/")
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    found: set[int] = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r"/ScheduleAndResults/Schedule/(\d+)", a["href"])
        if m:
            found.add(int(m.group(1)))
    known = set(known_ids)
    return sorted(found - known)
