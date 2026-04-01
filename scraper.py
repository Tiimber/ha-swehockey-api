"""
scraper.py – Fetches and parses schedule / live data from stats.swehockey.se.

Key URLs
--------
Schedule    : https://stats.swehockey.se/ScheduleAndResults/Schedule/{season_id}
Game events : https://stats.swehockey.se/Game/Events/{game_id}

The site has no public JSON API; all data comes from HTML scraping.

Observed table row structure (8 cells per game row):
  [0] Date "YYYY-MM-DD"  OR  time "HH:MM" for same-day subsequent games
  [1] Full datetime "YYYY-MM-DD HH:MM"  OR  empty for same-day subsequent games
  [2] Time "HH:MM" (always present)
  [3] Game cell: "Home\\n - \\nAway"  (non-breaking spaces replaced by regular space)
       Contains <a javascript:openonlinewindow('/Game/Events/{id}','')> link
  [4] Score "H - A"  (empty if not yet played)
  [5] Period scores "(P1H-P1A, P2H-P2A, …)"  (empty if not yet complete)
  [6] Spectators (empty if not yet played)
  [7] Venue
"""

import re
import time
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://stats.swehockey.se"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HockeyLiveAPI/1.0; personal-use)"
}
STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.5   # seconds between requests (polite scraping)

logger = logging.getLogger(__name__)
_last_request: float = 0.0


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get(url: str) -> Optional[str]:
    """Rate-limited GET. Returns HTML text, or None on error."""
    global _last_request
    wait = REQUEST_DELAY - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        _last_request = time.monotonic()
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        _last_request = time.monotonic()
        logger.error("Request failed [%s]: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Schedule scraping
# ---------------------------------------------------------------------------

def fetch_schedule(season_id: int) -> list[dict]:
    """Return all games for *season_id* as a list of game dicts."""
    url = f"{BASE_URL}/ScheduleAndResults/Schedule/{season_id}"
    html = _get(url)
    if not html:
        return []
    return _parse_schedule(html, season_id)


def _cell_text(td) -> str:
    """Get clean text from a BeautifulSoup td, normalising whitespace/nbsp."""
    return re.sub(r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")).strip()


def _parse_schedule(html: str, season_id: int) -> list[dict]:
    """
    Parse the schedule HTML page into a list of game dicts.

    Two observed table layouts (both used by swehockey.se):

    8-cell rows (regular season):
      [0] Date "YYYY-MM-DD" OR time carry-over
      [1] "YYYY-MM-DD HH:MM" OR empty for same-day games
      [2] Time "HH:MM"
      [3] "Home\\n - \\nAway"
      [4] "H - A" (score, contains game link)
      [5] "(P1, P2, …)" period scores
      [6] Spectators
      [7] Venue

    7-cell rows (playoffs):
      [0] Empty or round label
      [1] "YYYY-MM-DD\\xa019:00" (date+time with nbsp)
      [2] "Home\\n - \\nAway [RoundName]"
      [3] "H - A" (score, contains game link) OR empty
      [4] "(P1, P2, …)" OR empty
      [5] Spectators OR empty
      [6] Venue
    """
    soup = BeautifulSoup(html, "lxml")
    games: list[dict] = []
    current_date: Optional[str] = None

    # Round-name suffixes that may trail the away team (playoffs)
    _ROUND_RE = re.compile(
        r"\s+(Åttondelsfinal|Kvartsfinal|Semifinal|SM-final|Final)\s*\d*\s*$",
        re.IGNORECASE,
    )

    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        n = len(cells)
        if n not in (7, 8):
            continue

        texts = [_cell_text(c) for c in cells]

        # ── Date / time ───────────────────────────────────────────
        date_str: Optional[str] = None
        time_str: Optional[str] = None
        game_text: str = ""
        score_text: str = ""
        period_text: str = ""
        venue_text: str = ""
        score_cell_idx: int = 0

        if n == 8:
            # Full datetime in cell[1], game in cell[3], score in cell[4]
            full_dt_m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", texts[1])
            if full_dt_m:
                date_str = full_dt_m.group(1)
                time_str = full_dt_m.group(2)
                current_date = date_str
            elif current_date and re.fullmatch(r"\d{2}:\d{2}", texts[2]):
                date_str = current_date
                time_str = texts[2]
            game_text     = texts[3]
            score_text    = texts[4]
            period_text   = texts[5]
            venue_text    = texts[7]
            score_cell_idx = 4

        elif n == 7:
            # Date+time in cell[1] separated by non-breaking space; game in cell[2]
            dt_raw = cells[1].get_text(" ", strip=True).replace("\xa0", " ").strip()
            dt_m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", dt_raw)
            if dt_m:
                date_str = dt_m.group(1)
                time_str = dt_m.group(2)
                current_date = date_str
            elif current_date:
                # Inherit date; parse time from raw cell
                t_m = re.search(r"(\d{2}:\d{2})$", dt_raw)
                if t_m:
                    date_str = current_date
                    time_str = t_m.group(1)
            game_text     = texts[2]
            score_text    = texts[3]
            period_text   = texts[4]
            venue_text    = texts[6]
            score_cell_idx = 3

        if not date_str:
            continue

        # ── Teams (strip embedded round name from away team) ──────
        if " - " not in game_text:
            continue
        parts = game_text.split(" - ", 1)
        home_team = parts[0].strip()
        away_raw  = parts[1].strip() if len(parts) > 1 else ""

        round_name: Optional[str] = None
        rm = _ROUND_RE.search(away_raw)
        if rm:
            away_team  = away_raw[: rm.start()].strip()
            round_name = rm.group(1).strip()
        else:
            away_team = away_raw

        if not home_team or not away_team:
            continue

        # ── Game ID from javascript link (typically in the score cell) ─
        game_id: Optional[int] = None
        for ci in range(score_cell_idx, min(score_cell_idx + 3, n)):
            link = cells[ci].find("a")
            if link:
                m = re.search(r"Game/Events/(\d+)", link.get("href", ""))
                if m:
                    game_id = int(m.group(1))
                    break

        # ── Score ──────────────────────────────────────────────────
        home_score: Optional[int] = None
        away_score: Optional[int] = None
        sc_m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", score_text)
        if sc_m:
            home_score = int(sc_m.group(1))
            away_score = int(sc_m.group(2))

        # ── Period scores ──────────────────────────────────────────
        period_scores: Optional[str] = period_text if period_text.startswith("(") else None

        # ── Venue ──────────────────────────────────────────────────
        venue: Optional[str] = venue_text if venue_text else None

        # ── Datetime (Stockholm TZ) ────────────────────────────────
        game_dt: Optional[datetime] = None
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            game_dt = naive.replace(tzinfo=STOCKHOLM_TZ)
        except ValueError:
            pass

        is_completed = home_score is not None and period_scores is not None

        games.append({
            "season_id":    season_id,
            "game_id":      game_id,
            "date":         date_str,
            "time":         time_str,
            "datetime":     game_dt,
            "home_team":    home_team,
            "away_team":    away_team,
            "round":        round_name,
            "home_score":   home_score,
            "away_score":   away_score,
            "period_scores": period_scores,
            "venue":        venue,
            "is_completed": is_completed,
        })

    return games


# ---------------------------------------------------------------------------
# Game events (live data)
# ---------------------------------------------------------------------------

# Period boundaries in cumulative game-clock minutes
# P1: 0–20, P2: 20–40, P3: 40–60, OT: 60–65, SO: 65+
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
    """
    Fetch the game events page and return a dict with:
        home_score, away_score, period, period_clock,
        events (list), is_live, is_complete
    Returns an empty dict on failure.
    """
    url = f"{BASE_URL}/Game/Events/{game_id}"
    html = _get(url)
    if not html:
        return {}
    return _parse_game_events(html)


def _parse_game_events(html: str) -> dict:
    """Parse the Game/Events HTML page."""
    soup = BeautifulSoup(html, "lxml")

    result: dict = {
        "home_team": None,
        "away_team": None,
        "home_score": 0,
        "away_score": 0,
        "period": None,
        "period_clock": None,    # e.g. "12:34" (mm:ss within period)
        "period_scores": [],
        "is_overtime": False,
        "is_shootout": False,
        "events": [],
    }

    # ---- Try to find the score summary header ----------------------------
    # The page usually has a table with "Home X – Y Away" in the header area.
    header_score = re.search(
        r"(\d+)\s*[-–]\s*(\d+)", soup.get_text()[:2000]
    )
    if header_score:
        result["home_score"] = int(header_score.group(1))
        result["away_score"] = int(header_score.group(2))

    # ---- Parse all tables ------------------------------------------------
    tables = soup.find_all("table")

    events: list[dict] = []
    home_goals = 0
    away_goals = 0
    last_time_str: Optional[str] = None
    last_period: Optional[str] = None

    # Look for the events table: contains rows with mm:ss time values
    for table in tables:
        rows = table.find_all("tr")
        current_period_label: Optional[str] = None

        for row in rows:
            # Period header rows often span multiple columns (th or td with
            # "Period" text or "1", "2", "3", "OT", "SO" labels)
            header = row.find("th")
            if header:
                h_text = header.get_text(strip=True)
                if h_text in ("1", "2", "3"):
                    current_period_label = f"P{h_text}"
                    last_period = current_period_label
                elif h_text.upper() in ("OT", "OT5", "FLD", "FÖRL"):
                    current_period_label = "OT"
                    last_period = "OT"
                elif h_text.upper() in ("SO", "PSO", "STRAFFAR"):
                    current_period_label = "SO"
                    last_period = "SO"
                continue

            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            cell_texts = [c.get_text(strip=True) for c in cells]

            # Events have a mm:ss time in the first column
            time_cell = cell_texts[0]
            if not re.fullmatch(r"\d{1,2}:\d{2}", time_cell):
                continue

            last_time_str = time_cell

            # Parse cumulative minutes to determine period if not labelled
            try:
                mins, secs = map(int, time_cell.split(":"))
                total_mins = mins + secs / 60
                inferred_period = _minutes_to_period(total_mins)
                if current_period_label is None:
                    current_period_label = inferred_period
                last_period = current_period_label
            except ValueError:
                pass

            event_type = cell_texts[1] if len(cell_texts) > 1 else ""
            team = cell_texts[2] if len(cell_texts) > 2 else ""
            players = cell_texts[3] if len(cell_texts) > 3 else ""

            ev = {
                "time": time_cell,
                "period": current_period_label,
                "type": event_type,
                "team": team,
                "players": players,
            }
            events.append(ev)

            # Track goals for score calculation
            if "mål" in event_type.lower() or "goal" in event_type.lower():
                # We'll rely on the summary score if possible;
                # this is a fallback counter
                if result["home_team"] and team == result["home_team"]:
                    home_goals += 1
                elif result["away_team"] and team == result["away_team"]:
                    away_goals += 1

        # If we found events in this table, stop looking at other tables
        if events:
            break

    if events:
        result["events"] = events
        result["period"] = last_period
        result["is_overtime"] = last_period == "OT"
        result["is_shootout"] = last_period == "SO"

        # Refine period clock: if we have the last event time in mm:ss,
        # convert it to a per-period clock value
        if last_time_str and last_period:
            try:
                mins, secs = map(int, last_time_str.split(":"))
                total = mins + secs / 60
                offsets = {"P1": 0, "P2": 20, "P3": 40, "OT": 60, "SO": 65}
                offset = offsets.get(last_period, 0)
                period_mins = total - offset
                result["period_clock"] = (
                    f"{int(period_mins):02d}:{secs:02d}"
                )
            except ValueError:
                result["period_clock"] = last_time_str

    # If we couldn't find events but got a score from header, keep it
    if not events and header_score:
        pass  # home_score / away_score already set from header

    return result


# ---------------------------------------------------------------------------
# Team / season helpers
# ---------------------------------------------------------------------------

def list_teams_in_season(season_id: int) -> list[str]:
    """Return a sorted list of unique team names found in a season."""
    games = fetch_schedule(season_id)
    teams: set[str] = set()
    for g in games:
        if g["home_team"]:
            teams.add(g["home_team"])
        if g["away_team"]:
            teams.add(g["away_team"])
    return sorted(teams)


def filter_team_games(games: list[dict], team_name: str) -> list[dict]:
    """Return only games where *team_name* appears (case-insensitive)."""
    tl = team_name.lower()
    return [
        g
        for g in games
        if (g["home_team"] or "").lower() == tl
        or (g["away_team"] or "").lower() == tl
    ]


# ---------------------------------------------------------------------------
# New season discovery
# ---------------------------------------------------------------------------

def discover_new_seasons(known_ids: list[int]) -> list[int]:
    """
    Fetch the swehockey.se front page and return any Schedule season IDs
    that are *not* in *known_ids*.  Useful when the current season has ended
    and a new one is expected.
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
