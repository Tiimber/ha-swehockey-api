#!/usr/bin/env python3
"""
generate_awtrix.py – Auto-generates HA automation YAML for AWTRIX Hockey Scoreboard.

Reads  : /data/options.json   (awtrix_prefix, per-watch awtrix / awtrix_icon flags)
         /data/watchlist.json  (watch IDs and names)
Writes : /config/packages/hockeylive_awtrix.yaml  (HA Package format)

Requires in configuration.yaml:
    homeassistant:
      packages: !include_dir_named packages/

Per watch (when awtrix: true) the following automations are created:
  1. No match today      – show team icon + date of next match
  2. Countdown           – match is today but not started, countdown in minutes
  3. Live scoreboard     – H letter | score | A letter + 5 period dots
  4. Goal notification   – 30-second notify with scorer / assists / situation
  5. Match finished      – final score + period dots coloured green/red
  6. Midnight clear      – removes the custom app at 00:00:30
"""

import json
import os
import re
import sys
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------------------
# Team colors – drawn directly via AWTRIX draw commands, no icon files needed.
# cols 0-2: primary | cols 3-4: secondary | cols 5-7: accent (or primary)
# ---------------------------------------------------------------------------
_TEAM_COLORS = {
    # SHL
    "brynas_if": ("#006633", "#FFD700", None),
    "brynas": ("#006633", "#FFD700", None),
    "djurgardens_if": ("#00297A", "#CE2029", "#FFD700"),
    "djurgardens": ("#00297A", "#CE2029", "#FFD700"),
    "farjestad_bk": ("#00542A", "#FFD700", None),
    "farjestad": ("#00542A", "#FFD700", None),
    "frolunda_hc": ("#6B1D2A", "#FFD700", None),
    "frolunda": ("#6B1D2A", "#FFD700", None),
    "hc_frolunda": ("#6B1D2A", "#FFD700", None),
    "hv71": ("#FFD700", "#FFFFFF", "#003F7F"),
    "linkoping_hc": ("#003F6D", "#FFFFFF", None),
    "linkoping": ("#003F6D", "#FFFFFF", None),
    "lhc": ("#003F6D", "#FFFFFF", None),
    "lulea_hf": ("#CC0000", "#FFFFFF", "#004080"),
    "lulea": ("#CC0000", "#FFFFFF", "#004080"),
    "malmo_redhawks": ("#CC0000", "#1C1C1C", "#FFFFFF"),
    "malmo": ("#CC0000", "#1C1C1C", "#FFFFFF"),
    "modo_hockey": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "modo": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "rogle_bk": ("#CC0000", "#FFFFFF", "#003F7F"),
    "rogle": ("#CC0000", "#FFFFFF", "#003F7F"),
    "skelleftea_aik": ("#FFD700", "#1C1C1C", None),
    "skelleftea": ("#FFD700", "#1C1C1C", None),
    "saik": ("#FFD700", "#1C1C1C", None),
    "timra_ik": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "timra": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "orebro_hk": ("#001B50", "#FFFFFF", "#FFD700"),
    "orebro": ("#001B50", "#FFFFFF", "#FFD700"),
    "vaxjo_lakers": ("#1E5E3D", "#FFFFFF", "#CC0000"),
    "vaxjo": ("#1E5E3D", "#FFFFFF", "#CC0000"),
    # HockeyAllsvenskan
    "aik": ("#1C1C1C", "#FFD700", None),
    "almtuna_is": ("#CC0000", "#FFFFFF", None),
    "almtuna": ("#CC0000", "#FFFFFF", None),
    "bik_karlskoga": ("#003F7F", "#FFFFFF", None),
    "karlskoga": ("#003F7F", "#FFFFFF", None),
    "if_bjorkloven": ("#006633", "#FFFFFF", None),
    "bjorkloven": ("#006633", "#FFFFFF", None),
    "ik_oskarshamn": ("#003F7F", "#FFFFFF", "#CC0000"),
    "oskarshamn": ("#003F7F", "#FFFFFF", "#CC0000"),
    "karlskrona_hk": ("#003F7F", "#FFFFFF", None),
    "karlskrona": ("#003F7F", "#FFFFFF", None),
    "kristianstad_ik": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "kristianstad": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "leksands_if": ("#0055A4", "#FFFFFF", None),
    "leksand": ("#0055A4", "#FFFFFF", None),
    "lif": ("#0055A4", "#FFFFFF", None),
    "mora_ik": ("#003F7F", "#FFD700", None),
    "mora": ("#003F7F", "#FFD700", None),
    "nybro_vikings_if": ("#006633", "#CC0000", "#FFFFFF"),
    "nybro": ("#006633", "#CC0000", "#FFFFFF"),
    "tingsryd_aif": ("#CC0000", "#FFFFFF", None),
    "tingsryd": ("#CC0000", "#FFFFFF", None),
    "vik_vasteras_hk": ("#00388A", "#FFFFFF", "#FFD700"),
    "vasteras": ("#00388A", "#FFFFFF", "#FFD700"),
    "vastervik_ik": ("#007755", "#FFFFFF", None),
    "vastervik": ("#007755", "#FFFFFF", None),
    "sodertalje_sk": ("#003F7F", "#FFFFFF", "#CC0000"),
    "sodertalje": ("#003F7F", "#FFFFFF", "#CC0000"),
    "huddinge_ik": ("#CC0000", "#FFFFFF", None),
    "huddinge": ("#CC0000", "#FFFFFF", None),
    "kalmar_hc": ("#CC0000", "#1C1C1C", None),
    "kalmar": ("#CC0000", "#1C1C1C", None),
}

_TRANSLIT = str.maketrans("åäöÅÄÖéüÜ", "aaoAAOeuu")


def _tslug(name: str) -> str:
    s = name.translate(_TRANSLIT).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _draw_from_colors(colors: tuple | None, x_offset: int = 0) -> str:
    """Generate diagonal draw commands at x_offset for a color triple (p, s, a)."""
    if colors:
        p, s, a = colors
    else:
        p, s, a = "#444444", "#888888", None

    parts: list[str] = []

    def seg(x0: int, y: int, x1: int, color: str) -> None:
        x0 += x_offset
        x1 += x_offset
        if x0 > x1:
            return
        if x0 == x1:
            parts.append(f'{{"dp":[{x0},{y},"{color}"]}}')
        else:
            parts.append(f'{{"dl":[{x0},{y},{x1},{y},"{color}"]}}')

    for r in range(8):
        if a is None:
            seg(0, r, 7 - r, p)
            seg(8 - r, r, 7, s)
        else:
            seg(0, r, min(7, 5 - r), p)
            seg(max(0, 6 - r), r, min(7, 7 - r), s)
            seg(max(0, 8 - r), r, 7, a)

    return ",".join(parts)


def _team_draw_fragment(team_name: str) -> str:
    """Return diagonal draw commands for a team at x=0..7 (left logo)."""
    slug = _tslug(team_name)
    colors = _TEAM_COLORS.get(slug)
    if not colors:
        for k, v in _TEAM_COLORS.items():
            if slug.startswith(k) or k.startswith(slug):
                colors = v
                break
    return _draw_from_colors(colors, 0)


def _jinja_awtrix_draw_header() -> str:
    """Generate Jinja2 dicts + macro for runtime home/away diagonal logos.

    Produces:
      _ld  – slug → draw string at x=0..7  (left / home)
      _rd  – slug → draw string at x=24..31 (right / away)
      _ts  – macro: team name → slug (transliterate + slugify)
      _fl/_fr – fallback grey draw strings
    """
    l_entries: list[str] = []
    r_entries: list[str] = []
    for slug, colors in _TEAM_COLORS.items():
        l = _draw_from_colors(colors, 0)
        r = _draw_from_colors(colors, 24)
        l_entries.append("'" + slug + "':'" + l + "'")
        r_entries.append("'" + slug + "':'" + r + "'")
    fallback_l = _draw_from_colors(None, 0)
    fallback_r = _draw_from_colors(None, 24)
    ld = "{%- set _ld={" + ",".join(l_entries) + "} -%}"
    rd = "{%- set _rd={" + ",".join(r_entries) + "} -%}"
    ts = (
        "{%- macro _ts(n) -%}"
        "{{- n|lower"
        "|replace('\u00e5','a')|replace('\u00e4','a')|replace('\u00f6','o')"
        "|replace('\u00e9','e')|replace('\u00fc','u')"
        "|replace(' ','_')|replace('-','_')|replace('.','') -}}"
        "{%- endmacro -%}"
    )
    fl = "{%- set _fl='" + fallback_l + "' -%}"
    fr = "{%- set _fr='" + fallback_r + "' -%}"
    return ld + rd + ts + fl + fr


OPTIONS = Path("/data/options.json")
WATCHLIST = Path("/data/watchlist.json")
OUT_FILE = Path("/config/packages/hockeylive_awtrix.yaml")


def _slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_str.lower())


def _sub(tmpl: str, **kw) -> str:
    """Replace __KEY__ placeholders; HA Jinja2 {{ }} are left untouched."""
    for k, v in kw.items():
        tmpl = tmpl.replace(f"__{k}__", str(v))
    return tmpl


# ---------------------------------------------------------------------------
# Automation YAML template – one block per watch.
# Uses __PLACEHOLDER__ style substitution (not Python f-strings) so that
# HA Jinja2 {{ }} expressions are left as literal strings.
# ---------------------------------------------------------------------------
_T = """\
# --- __NAME__ (watch_id: __WID__) ---

- alias: "AWTRIX __NAME__ - Ingen match idag"
  id: "awtrix___WID___no_match"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: time_pattern
      minutes: "/30"
    - platform: homeassistant
      event: start
  condition:
    - condition: template
      value_template: >
        {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
        {{ states('sensor.hockeylive___SLUG___status') in ['upcoming','idle']
           and ndt is not none
           and (as_timestamp(ndt) - now().timestamp()) >= 86400 }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {%- set day = (ndt[8:10]|int(0))|string if ndt else '' -%}
          {%- set dx = 26 if day|length == 1 else 24 -%}
          {%- if day -%}{"draw":[__TEAMDRAW__,{"dt":[12,2,"@","#FFFFFF"]},{"df":[23,0,9,2,"#CC0000"]},{"df":[23,2,9,6,"#FFFFFF"]},{"dt":[{{ dx }},2,"{{ day }}","#000000"]}],"noScroll":true,"duration":10,"lifetime":600}{%- else -%}{%- endif -%}

- alias: "AWTRIX __NAME__ - Nedrakning till match"
  id: "awtrix___WID___countdown"
  trigger:
    - platform: time_pattern
      minutes: "/1"
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: homeassistant
      event: start
  condition:
    - condition: template
      value_template: >
        {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
        {{ states('sensor.hockeylive___SLUG___status') == 'upcoming'
           and ndt is not none
           and (as_timestamp(ndt) - now().timestamp()) < 86400
           and (as_timestamp(ndt) - now().timestamp()) > 7200
           and states('binary_sensor.hockeylive___SLUG___live') == 'off' }}
  action:
    - variables:
        mins_left: >
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {{ [((as_timestamp(ndt) - now().timestamp()) / 60)|int, 0]|max if ndt else 0 }}
        cdtext: >
          {%- set m = mins_left|int -%}
          {{ '%d:%02d'|format(m // 60, m % 60) }}
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {"draw":[__TEAMDRAW__],"text":[{"t":"{{ cdtext }}","c":"FFFFFF"}],"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Prematch scoreboard"
  id: "awtrix___WID___prematch"
  trigger:
    - platform: time_pattern
      minutes: "/1"
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: homeassistant
      event: start
  condition:
    - condition: template
      value_template: >
        {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
        {{ states('sensor.hockeylive___SLUG___status') == 'upcoming'
           and ndt is not none
           and (as_timestamp(ndt) - now().timestamp()) <= 7200
           and states('binary_sensor.hockeylive___SLUG___live') == 'off' }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set _ht = state_attr('sensor.hockeylive___SLUG___status','next_home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','next_away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"0-0","#FFFFFF"]},{"dp":[9,7,"#404040"]},{"dp":[12,7,"#404040"]},{"dp":[15,7,"#404040"]},{"dp":[18,7,"#404040"]},{"dp":[21,7,"#404040"]},{{ _ad }}],"noScroll":true,"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Live scoreboard"
  id: "awtrix___WID___live"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: state
      entity_id: binary_sensor.hockeylive___SLUG___live
    - platform: time_pattern
      minutes: "/1"
    - platform: homeassistant
      event: start
  condition:
    - condition: state
      entity_id: binary_sensor.hockeylive___SLUG___live
      state: "on"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set p = state_attr('sensor.hockeylive___SLUG___status','period') -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','home_score')|default(0)|int -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','away_score')|default(0)|int -%}
          {%- set _ht = state_attr('sensor.hockeylive___SLUG___status','home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {%- set p1c = '#1E3A5F' if p in ['P2','P3','OT','SO'] else '#FFD700' if p == 'P1' else '#404040' -%}
          {%- set p2c = '#1E3A5F' if p in ['P3','OT','SO'] else '#FFD700' if p == 'P2' else '#404040' -%}
          {%- set p3c = '#1E3A5F' if p in ['OT','SO'] else '#FFD700' if p == 'P3' else '#404040' -%}
          {%- set otc = '#1E3A5F' if p == 'SO' else '#FFD700' if p == 'OT' else '#404040' -%}
          {%- set soc = '#FFD700' if p == 'SO' else '#404040' -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"{{ hs }}-{{ as_ }}","#FFD700"]},{"dp":[9,7,"{{ p1c }}"]},{"dp":[12,7,"{{ p2c }}"]},{"dp":[15,7,"{{ p3c }}"]},{"dp":[18,7,"{{ otc }}"]},{"dp":[21,7,"{{ soc }}"]},{{ _ad }}],"noScroll":true,"lifetime":120}

- alias: "AWTRIX __NAME__ - Mal!"
  id: "awtrix___WID___goal"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___last_goal_scorer
  condition:
    - condition: state
      entity_id: binary_sensor.hockeylive___SLUG___live
      state: "on"
    - condition: template
      value_template: >
        {{ trigger.to_state.state not in ['unknown','unavailable','-'] }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/notify"
        payload: >-
          {%- set sc = trigger.to_state.state -%}
          {%- set team = state_attr('sensor.hockeylive___SLUG___status','last_goal_team') or '?' -%}
          {%- set ass = state_attr('sensor.hockeylive___SLUG___status','last_goal_assists') or [] -%}
          {%- set sit = state_attr('sensor.hockeylive___SLUG___status','last_goal_situation') or 'ES' -%}
          {%- set score = state_attr('sensor.hockeylive___SLUG___status','score') -%}
          {%- set sitc = 'FFD700' if sit == 'PP' else '00AAFF' if sit == 'SH' else 'FFFFFF' -%}
          {"draw":[__TEAMDRAW__],"text":[{"t":"MAL! ","c":"FFD700"},{"t":"{{ sc }}","c":"FFFFFF"},{"t":" {{ score }}","c":"FFD700"}{% if ass %},{"t":" Ass: {{ ass|join(', ') }}","c":"888888"}{% endif %}{% if sit not in ['ES',''] %},{"t":" ({{ sit }})","c":"{{ sitc }}"}{% endif %}],"duration":30,"stack":false,"wakeup":true}

- alias: "AWTRIX __NAME__ - Match slut"
  id: "awtrix___WID___finished"
  trigger:
    - platform: state
      entity_id: binary_sensor.hockeylive___SLUG___live
      to: "off"
  condition:
    - condition: template
      value_template: "{{ trigger.from_state is not none and trigger.from_state.state == 'on' }}"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set won = state_attr('sensor.hockeylive___SLUG___status','last_won') -%}
          {%- set ls = state_attr('sensor.hockeylive___SLUG___status','last_score') or '-' -%}
          {%- set ot = state_attr('sensor.hockeylive___SLUG___status','last_went_ot') -%}
          {%- set dc = '#00C800' if won else '#C80000' -%}
          {%- set oc = dc if ot else '#404040' -%}
          {"draw":[__TEAMDRAW__,{"dp":[8,7,"{{ dc }}"]},{"dp":[13,7,"{{ dc }}"]},{"dp":[18,7,"{{ dc }}"]},{"dp":[23,7,"{{ oc }}"]},{"dp":[28,7,"#404040"]}],"text":[{"t":"{{ ls }} {{ '+' if won else 'x' }}","c":"{{ dc }}"}],"duration":30,"lifetime":3600}

- alias: "AWTRIX __NAME__ - Matchresultat idag"
  id: "awtrix___WID___result_today"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: time_pattern
      minutes: "/10"
    - platform: homeassistant
      event: start
  condition:
    - condition: template
      value_template: >
        {{ states('sensor.hockeylive___SLUG___status') == 'finished_today'
           or (state_attr('sensor.hockeylive___SLUG___status','last_date') is not none
               and state_attr('sensor.hockeylive___SLUG___status','last_date') == now().strftime('%Y-%m-%d')) }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          __AWTRIX_DRAW_HDR__{%- set _ht = state_attr('sensor.hockeylive___SLUG___status','last_home_team')|string -%}
          {%- set _at = state_attr('sensor.hockeylive___SLUG___status','last_away_team')|string -%}
          {%- set _hslug = _ts(_ht) -%}
          {%- set _aslug = _ts(_at) -%}
          {%- set _hd = _ld[_hslug] if _hslug in _ld else _fl -%}
          {%- set _ad = _rd[_aslug] if _aslug in _rd else _fr -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','last_home_score')|int(0) -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','last_away_score')|int(0) -%}
          {%- set won = state_attr('sensor.hockeylive___SLUG___status','last_won') -%}
          {%- set ot = state_attr('sensor.hockeylive___SLUG___status','last_went_ot') -%}
          {%- set dc = '#00C800' if won else '#C80000' -%}
          {%- set oc = dc if ot else '#404040' -%}
          {"draw":[{{ _hd }},{"dt":[10,1,"{{ hs }}-{{ as_ }}","{{ dc }}"]},{"dp":[9,7,"{{ dc }}"]},{"dp":[12,7,"{{ dc }}"]},{"dp":[15,7,"{{ dc }}"]},{"dp":[18,7,"{{ oc }}"]},{"dp":[21,7,"#404040"]},{{ _ad }}],"noScroll":true,"duration":10,"lifetime":1800}

- alias: "AWTRIX __NAME__ - Rensa vid midnatt"
  id: "awtrix___WID___midnight"
  trigger:
    - platform: time
      at: "00:00:30"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: ""
"""


def main() -> None:
    if not OPTIONS.exists():
        print("[generate_awtrix] /data/options.json not found – skipping")
        return

    options = json.loads(OPTIONS.read_text(encoding="utf-8"))
    prefix = (options.get("awtrix_prefix") or "").strip()
    if not prefix:
        print("[generate_awtrix] awtrix_prefix not configured – skipping")
        return

    if not WATCHLIST.exists():
        print(
            "[generate_awtrix] watchlist.json not found – run generate_config.py first"
        )
        return

    raw = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    watches = (raw.get("watches") or raw) if isinstance(raw, dict) else {}

    # Map display name → options entry (for awtrix flag + icon)
    opt_map = {
        (w.get("name") or w.get("team", "")): w for w in options.get("watches", [])
    }

    enabled = []
    disabled = []  # watches with awtrix: false – need their app cleared
    for wid, w in watches.items():
        name = w.get("name") or w.get("team", "")
        ow = opt_map.get(name, {})
        if ow.get("awtrix"):
            enabled.append({**w, "awtrix_icon": (ow.get("awtrix_icon") or "").strip()})
        else:
            disabled.append({**w})

    if not enabled and not disabled:
        print("[generate_awtrix] No watches configured – skipping")
        return

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    awtrix_draw_hdr = _jinja_awtrix_draw_header()
    automation_blocks = []
    for w in enabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        teamdraw = _team_draw_fragment(name)
        automation_blocks.append(
            _sub(
                _T,
                NAME=name,
                WID=w["id"],
                SLUG=slug,
                TEAMDRAW=teamdraw,
                AWTRIX_DRAW_HDR=awtrix_draw_hdr,
                APP=f"hockey_{slug}",
                PREFIX=prefix,
            )
        )

    # Clear automations for disabled (awtrix: false) watches
    _T_CLEAR = """\
- alias: "AWTRIX __NAME__ - Rensa (inaktiverad)"
  id: "awtrix___WID___disabled_clear"
  trigger:
    - platform: homeassistant
      event: start
    - platform: time_pattern
      hours: "/1"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: ""
"""
    for w in disabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        automation_blocks.append(
            _sub(
                _T_CLEAR,
                NAME=name,
                WID=w["id"],
                APP=f"hockey_{slug}",
                PREFIX=prefix,
            )
        )

    # Write as HA Package (works alongside any existing automation: !include setup)
    header = "\n".join(
        [
            "# Auto-generated by HockeyLive add-on – do not edit manually",
            "# Re-generated on every add-on restart",
            "#",
            "# Add to configuration.yaml:",
            "#   homeassistant:",
            "#     packages: !include_dir_named packages/",
            "",
            "automation:",
        ]
    )
    # Indent each automation block so it becomes a YAML list under "automation:"
    indented = []
    for block in automation_blocks:
        for i, line in enumerate(block.splitlines()):
            if line.startswith("- alias:"):
                indented.append("  " + line)
            elif line.strip() == "":
                indented.append("")
            else:
                indented.append("  " + line)
        indented.append("")

    OUT_FILE.write_text(header + "\n" + "\n".join(indented), encoding="utf-8")
    print(
        f"[generate_awtrix] {len(enabled)} enabled + {len(disabled)} disabled → {OUT_FILE}"
    )
    print(
        f"[generate_awtrix] Prefix: {prefix}, Watches: {[w.get('name') or w['team'] for w in enabled]}"
    )
    # Auto-reload automations via Supervisor API (only available inside the add-on)
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if supervisor_token:
        try:
            import urllib.request

            req = urllib.request.Request(
                "http://supervisor/core/api/services/automation/reload",
                data=b"{}",
                method="POST",
            )
            req.add_header("Authorization", f"Bearer {supervisor_token}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                print(f"[generate_awtrix] HA automations reloaded (HTTP {resp.status})")
        except Exception as exc:
            print(f"[generate_awtrix] Could not reload automations: {exc}")
    else:
        print(
            "[generate_awtrix] Reload automations in HA: Developer Tools → YAML → Reload Automations"
        )


if __name__ == "__main__":
    main()
