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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HockeyLiveAPI/1.0; personal-use)"}
STOCKHOLM_TZ = ZoneInfo("Europe/Stockholm")
REQUEST_TIMEOUT = 15
REQUEST_DELAY = 1.5  # seconds between requests (polite scraping)

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


def _extract_season_name(html: str) -> Optional[str]:
    """
    Try to extract a human-readable season/league name from a schedule page.
    Looks at <title> and then the first <h1>/<h2> on the page.
    """
    soup = BeautifulSoup(html, "lxml")
    title_tag = soup.find("title")
    if title_tag:
        text = title_tag.get_text(strip=True)
        # Discard generic single-word titles like "Stats"
        if text and " " in text:
            return text
    for tag in ("h1", "h2"):
        el = soup.find(tag)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text
    return None


def fetch_season_info(season_id: int) -> dict:
    """
    Fetch a season page once and return both the game list and a best-effort
    human-readable season name.

    Returns: {"name": str | None, "games": list[dict]}
    """
    url = f"{BASE_URL}/ScheduleAndResults/Schedule/{season_id}"
    html = _get(url)
    if not html:
        return {"name": None, "games": []}
    return {
        "name": _extract_season_name(html),
        "games": _parse_schedule(html, season_id),
    }


def _cell_text(td) -> str:
    """Get clean text from a BeautifulSoup td, normalising whitespace/nbsp."""
    return re.sub(
        r"\s+", " ", td.get_text(" ", strip=True).replace("\xa0", " ")
    ).strip()


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
            game_text = texts[3]
            score_text = texts[4]
            period_text = texts[5]
            venue_text = texts[7]
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
            game_text = texts[2]
            score_text = texts[3]
            period_text = texts[4]
            venue_text = texts[6]
            score_cell_idx = 3

        if not date_str:
            continue

        # ── Teams (strip embedded round name from away team) ──────
        if " - " not in game_text:
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
        period_scores: Optional[str] = (
            period_text if period_text.startswith("(") else None
        )

        # ── Venue ──────────────────────────────────────────────────
        venue: Optional[str] = venue_text if venue_text else None

        # ── Datetime (Stockholm TZ) ────────────────────────────────
        game_dt: Optional[datetime] = None
        try:
            naive = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
            game_dt = naive.replace(tzinfo=STOCKHOLM_TZ)
        except ValueError:
            pass

        # A game is completed only when all 3 regulation periods have a score entry.
        # During live play the schedule page shows partial period scores (e.g. "(2-1)"
        # after P1, "(2-1, 0-1)" after P2) which would otherwise trigger a false match.
        # We require ≥ 3 comma-separated entries (P1, P2, P3 all done).
        period_entry_count = period_scores.count(",") + 1 if period_scores else 0
        is_completed = home_score is not None and period_entry_count >= 3

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
                "is_completed": is_completed,
            }
        )

    return games


# ---------------------------------------------------------------------------
# Game events (live data)
# ---------------------------------------------------------------------------

# Period boundaries – cumulative game-clock SECONDS
#   P1:  0    – 1200  ( 0 – 20 min)
#   P2:  1200 – 2400  (20 – 40 min)
#   P3:  2400 – 3600  (40 – 60 min)
#   OT:  3600 – 3900  (60 – 65 min, SHL/HA 5-min OT)
#   SO:  3900+
_PERIOD_SECS: list[tuple[int, int, str]] = [
    (0, 1200, "P1"),
    (1200, 2400, "P2"),
    (2400, 3600, "P3"),
    (3600, 3900, "OT"),
    (3900, 99999, "SO"),
]
_PERIOD_OFFSET_SECS: dict[str, int] = {
    "P1": 0,
    "P2": 1200,
    "P3": 2400,
    "OT": 3600,
    "SO": 3900,
}

# Keep for backward compatibility with callers that passed minutes
_PERIOD_FROM_MINUTES = [
    (0, 20, "P1"),
    (20, 40, "P2"),
    (40, 60, "P3"),
    (60, 65, "OT"),
    (65, 9999, "SO"),
]


def _parse_mmss(s: str) -> Optional[int]:
    """Parse 'mm:ss' into total seconds, or None on failure."""
    m = re.fullmatch(r"(\d{1,3}):(\d{2})", s.strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _secs_to_mmss(secs: int) -> str:
    secs = max(0, int(secs))
    return f"{secs // 60:02d}:{secs % 60:02d}"


def _period_from_secs(total_secs: int) -> str:
    for lo, hi, label in _PERIOD_SECS:
        if lo <= total_secs < hi:
            return label
    return "SO"


def _minutes_to_period(total_minutes: float) -> str:
    for lo, hi, label in _PERIOD_FROM_MINUTES:
        if lo <= total_minutes < hi:
            return label
    return "SO"


def _period_clock_str(game_secs: int, period: str) -> str:
    """Return mm:ss elapsed within the indicated period."""
    offset = _PERIOD_OFFSET_SECS.get(period, 0)
    return _secs_to_mmss(game_secs - offset)


# ── Event type classifiers ───────────────────────────────────────────────────
_GOAL_RE = re.compile(r"\bm[åa]l\b|goal", re.I)
_PENALTY_RE = re.compile(r"utvisning|penalty|\bstraff\b(?!skott)", re.I)
_MISCONDUCT_RE = re.compile(r"matchstraff|game\s*misconduct", re.I)

# Valid Swedish/English period header tokens
_PERIOD_HEADERS: dict[str, str] = {
    "1": "P1",
    "2": "P2",
    "3": "P3",
    "OT": "OT",
    "OT5": "OT",
    "FLD": "OT",
    "FÖRL": "OT",
    "SO": "SO",
    "PSO": "SO",
    "STRAFFAR": "SO",
}


def _parse_situation(all_texts: list[str]) -> str:
    """
    Determine game situation (PP1, PP2, SH, SH2, EN, PS, EQ) from row cells.
    Precedence: PP2 > PP1 > SH2 > SH > EN > PS > EQ.
    """
    combined = " ".join(all_texts)
    if re.search(r"\bPP[-_ ]?2\b", combined, re.I):
        return "PP2"
    if re.search(r"\bPP[-_ ]?1?\b", combined, re.I):
        return "PP1"
    if re.search(r"\bSH[-_ ]?2\b", combined, re.I):
        return "SH2"
    if re.search(r"\bSH[-_ ]?1?\b", combined, re.I):
        return "SH"
    if re.search(r"\bEN\b|tom\s*m[åa]l", combined, re.I):
        return "EN"
    if re.search(r"\bPS\b|straffskott|penalty\s*shot", combined, re.I):
        return "PS"
    return "EQ"


# Valid penalty durations (minutes)
_VALID_DURATIONS: set[int] = {2, 4, 5, 10, 20, 25}


def _parse_duration_min(texts: list[str]) -> Optional[int]:
    """Find penalty duration in minutes from a list of cell strings."""
    for t in texts:
        # "2 min", "5min", "2m"
        m = re.search(r"\b(\d+)\s*(?:min|m)\b", t, re.I)
        if m and int(m.group(1)) in _VALID_DURATIONS:
            return int(m.group(1))
        # Bare valid number in its own cell (e.g. a "2" cell)
        stripped = t.strip()
        if re.fullmatch(r"\d+", stripped) and int(stripped) in _VALID_DURATIONS:
            return int(stripped)
    return None


def _parse_goal_players(cell: str) -> tuple[str, list[str]]:
    """
    Parse the players cell of a goal row into (scorer, assists).

    Observed formats on swehockey.se:
      "Eriksson (Andersson / Larsson)"
      "Eriksson, Erik (Andersson, Anders / Larsson, Lars)"
      "23. Eriksson (4. Andersson)"
      "Eriksson"
    """
    cell = cell.strip()
    # Strip jersey number prefix "23. "
    cell = re.sub(r"^\d+\.\s*", "", cell)

    paren = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", cell)
    if paren:
        scorer = paren.group(1).strip().rstrip(",")
        assists_raw = paren.group(2)
        assists = [
            re.sub(r"^\d+\.\s*", "", a).strip()
            for a in re.split(r"[/,]", assists_raw)
            if a.strip()
        ]
    else:
        scorer = cell
        assists = []
    return scorer, assists


def _parse_penalty_player(cells: list[str]) -> tuple[str, Optional[int], str]:
    """
    From a list of penalty row cells return (player, duration_min, offense).

    Typical column layout (but varies):
      [..., player_name, duration, offense_description, ...]
    Also handles all-in-one formats like "Eriksson - 2 min - Holding".
    """
    duration = _parse_duration_min(cells)
    player = ""
    offense = ""

    for t in cells:
        if not t:
            continue
        # Skip clock-like strings
        if re.fullmatch(r"\d{1,3}:\d{2}", t):
            continue
        # Skip event-type labels
        if _PENALTY_RE.search(t) or _MISCONDUCT_RE.search(t):
            continue
        # Skip pure duration tokens
        if re.fullmatch(r"\d+\s*(?:min|m)?", t, re.I) and len(t) <= 6:
            continue
        # First meaningful token → player
        if not player:
            player = re.sub(r"^\d+\.\s*", "", t).strip()
            # Handle "Eriksson - 2 min - Holding" packed into one cell
            if " - " in player:
                parts = [p.strip() for p in player.split(" - ")]
                player = parts[0]
                if not duration:
                    duration = _parse_duration_min(parts[1:])
                if len(parts) >= 3:
                    offense = parts[-1]
            continue
        if not offense:
            offense = t

    return player, duration, offense


def _classify_events(
    raw_events: list[dict],
    home_team: str,
    away_team: str,
    current_game_secs: int,
) -> tuple[list[dict], list[dict]]:
    """
    Classify raw events into structured goals and penalties lists.
    Returns (goals, penalties).

    Each goal dict contains:
        game_time, game_time_secs, period, period_clock,
        team, scorer, assists, situation,
        home_score_after, away_score_after, secs_since

    Each penalty dict contains:
        game_time, game_time_secs, period, period_clock,
        team, player, duration_min, offense,
        is_active, elapsed_secs, elapsed_mmss,
        remaining_secs, remaining_mmss
    """
    goals: list[dict] = []
    penalties: list[dict] = []
    home_score = 0
    away_score = 0

    for ev in raw_events:
        event_type = ev.get("type", "")
        game_secs = _parse_mmss(ev.get("game_time", "")) or 0
        period = ev.get("period") or ""
        p_clock = (
            _period_clock_str(game_secs, period) if period else ev.get("game_time", "")
        )
        team = ev.get("team", "")
        players = ev.get("players", "")
        extra = ev.get("_extra", [])
        all_texts = [event_type, team, players] + extra
        secs_since = max(0, current_game_secs - game_secs)

        if _GOAL_RE.search(event_type):
            scorer, assists = _parse_goal_players(players)
            situation = _parse_situation(all_texts)
            if team.lower() == home_team.lower() and home_team:
                home_score += 1
            elif team.lower() == away_team.lower() and away_team:
                away_score += 1
            goals.append(
                {
                    "game_time": ev["game_time"],
                    "game_time_secs": game_secs,
                    "period": period,
                    "period_clock": p_clock,
                    "team": team,
                    "scorer": scorer,
                    "assists": assists,
                    "situation": situation,
                    "home_score_after": home_score,
                    "away_score_after": away_score,
                    "secs_since": secs_since,
                }
            )

        elif _PENALTY_RE.search(event_type) or _MISCONDUCT_RE.search(event_type):
            player, duration, offense = _parse_penalty_player([players] + extra)
            if duration is not None:
                penalty_end = game_secs + duration * 60
                elapsed = max(0, current_game_secs - game_secs)
                remaining = max(0, penalty_end - current_game_secs)
                is_active = remaining > 0
            else:
                elapsed = remaining = None
                is_active = False
            penalties.append(
                {
                    "game_time": ev["game_time"],
                    "game_time_secs": game_secs,
                    "period": period,
                    "period_clock": p_clock,
                    "team": team,
                    "player": player,
                    "duration_min": duration,
                    "offense": offense,
                    "is_active": is_active,
                    "elapsed_secs": elapsed,
                    "elapsed_mmss": _secs_to_mmss(elapsed)
                    if elapsed is not None
                    else None,
                    "remaining_secs": remaining,
                    "remaining_mmss": _secs_to_mmss(remaining)
                    if remaining is not None
                    else None,
                }
            )

    return goals, penalties


def fetch_game_events(game_id: int) -> dict:
    """
    Fetch the game events page and return a structured dict:
        home_team, away_team, home_score, away_score,
        period, period_clock, is_overtime, is_shootout,
        goals, last_goal, penalties, active_penalties,
        events (raw, for backward compatibility)
    Returns an empty dict on failure.
    """
    if not game_id:
        return {}
    url = f"{BASE_URL}/Game/Events/{game_id}"
    html = _get(url)
    if not html:
        return {}
    return _parse_game_events(html)


def _parse_game_events(html: str) -> dict:
    """Parse the Game/Events HTML page into a structured result."""
    soup = BeautifulSoup(html, "lxml")

    # ── Score from page header ────────────────────────────────────────────
    header_score = re.search(r"(\d+)\s*[-–]\s*(\d+)", soup.get_text()[:2000])
    home_score_total = int(header_score.group(1)) if header_score else 0
    away_score_total = int(header_score.group(2)) if header_score else 0

    # ── Try to extract home/away team names from a prominent heading ──────
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    for tag in ("h1", "h2", "h3"):
        el = soup.find(tag)
        if el:
            text = el.get_text(" ", strip=True)
            m = re.match(r"(.+?)\s+[-–]\s+(.+?)(?:\s*\d|$)", text)
            if m:
                home_team = m.group(1).strip()
                away_team = m.group(2).strip()
                break

    # ── Parse event rows from all tables ─────────────────────────────────
    raw_events: list[dict] = []
    last_time_str: Optional[str] = None
    last_period: Optional[str] = None

    for table in soup.find_all("table"):
        current_period_label: Optional[str] = None

        for row in table.find_all("tr"):
            # Period separator rows (th element)
            header = row.find("th")
            if header:
                h_text = header.get_text(strip=True).upper()
                mapped = _PERIOD_HEADERS.get(h_text)
                if mapped:
                    current_period_label = mapped
                    last_period = mapped
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

            # Events have a cumulative mm:ss clock in the first cell
            time_cell = cell_texts[0]
            if not re.fullmatch(r"\d{1,3}:\d{2}", time_cell):
                continue

            last_time_str = time_cell

            # Infer period from cumulative seconds when no header has been seen
            game_secs = _parse_mmss(time_cell) or 0
            inferred = _period_from_secs(game_secs)
            if current_period_label is None:
                current_period_label = inferred
            last_period = current_period_label

            event_type = cell_texts[1] if len(cell_texts) > 1 else ""
            team = cell_texts[2] if len(cell_texts) > 2 else ""
            players = cell_texts[3] if len(cell_texts) > 3 else ""
            extra = cell_texts[4:] if len(cell_texts) > 4 else []

            raw_events.append(
                {
                    "game_time": time_cell,
                    "period": current_period_label,
                    "type": event_type,
                    "team": team,
                    "players": players,
                    "_extra": extra,
                }
            )

        if raw_events:
            break

    # ── Current game clock ────────────────────────────────────────────────
    current_game_secs = _parse_mmss(last_time_str) or 0 if last_time_str else 0

    # ── Period clock (position within current period) ─────────────────────
    period_clock: Optional[str] = None
    if last_time_str and last_period:
        period_clock = _period_clock_str(current_game_secs, last_period)

    # ── Classify events into goals / penalties ────────────────────────────
    goals, penalties = _classify_events(
        raw_events,
        home_team or "",
        away_team or "",
        current_game_secs,
    )

    # Strip internal _extra key from the public events list
    public_events = [
        {k: v for k, v in ev.items() if k != "_extra"} for ev in raw_events
    ]

    return {
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score_total,
        "away_score": away_score_total,
        "period": last_period,
        "period_clock": period_clock,
        "period_scores": [],
        "is_overtime": last_period == "OT",
        "is_shootout": last_period == "SO",
        "goals": goals,
        "last_goal": goals[-1] if goals else None,
        "penalties": penalties,
        "active_penalties": [p for p in penalties if p["is_active"]],
        "events": public_events,
    }


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
        if (g["home_team"] or "").lower() == tl or (g["away_team"] or "").lower() == tl
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
