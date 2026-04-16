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

Global (once per prefix):
  7. Track active app    – mirrors AWTRIX currentApp MQTT → input_text
  8. Middle button       – cycles through goal history when on a scoreboard
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

# Team abbreviations overlaid on logos.
# Key = canonical slug (must match a key in _TEAM_COLORS).
# Value = 2–4 char string, all uppercase.
_TEAM_ABBR: dict[str, str] = {
    # SHL
    "brynas_if": "BIF",
    "djurgardens_if": "DIF",
    "frolunda_hc": "FHC",
    "farjestad_bk": "FBK",
    "hv71": "HV",
    "leksands_if": "LIF",
    "linkoping_hc": "LHC",
    "lulea_hf": "LHF",
    "malmo_redhawks": "MAL",
    "rogle_bk": "RBK",
    "skelleftea_aik": "SAIK",
    "timra_ik": "TIK",
    "vaxjo_lakers": "VLH",
    "orebro_hk": "ÖHK",
    # HockeyAllsvenskan
    "aik": "AIK",
    "almtuna_is": "AIS",
    "bik_karlskoga": "BIK",
    "if_bjorkloven": "IFB",
    "ik_oskarshamn": "IKO",
    "modo_hockey": "MODO",
    "mora_ik": "MIK",
    "nybro_vikings_if": "NYB",
    "troja_ljungby": "TRO",
    "sodertalje_sk": "SSK",
    "vik_vasteras_hk": "VIK",
    "kalmar_hc": "KHC",
    "ostersunds_ik": "ÖIK",
    "vimmerby_hockey": "VHC",
}

# ---------------------------------------------------------------------------
# 3×5 pixel font (cols × rows). Each char = list of 5 ints, bit 2 = leftmost.
# ---------------------------------------------------------------------------
_FONT3X5: dict[str, list[int]] = {
    "A": [0b010, 0b101, 0b111, 0b101, 0b101],
    "B": [0b110, 0b101, 0b110, 0b101, 0b110],
    "C": [0b011, 0b100, 0b100, 0b100, 0b011],
    "D": [0b110, 0b101, 0b101, 0b101, 0b110],
    "E": [0b111, 0b100, 0b110, 0b100, 0b111],
    "F": [0b111, 0b100, 0b110, 0b100, 0b100],
    "G": [0b011, 0b100, 0b101, 0b101, 0b011],
    "H": [0b101, 0b101, 0b111, 0b101, 0b101],
    "I": [0b111, 0b010, 0b010, 0b010, 0b111],
    "J": [0b111, 0b001, 0b001, 0b101, 0b010],
    "K": [0b101, 0b101, 0b110, 0b101, 0b101],
    "L": [0b100, 0b100, 0b100, 0b100, 0b111],
    "M": [0b101, 0b111, 0b111, 0b101, 0b101],
    "N": [0b101, 0b111, 0b111, 0b101, 0b101],
    "O": [0b010, 0b101, 0b101, 0b101, 0b010],
    "P": [0b110, 0b101, 0b110, 0b100, 0b100],
    "Q": [0b010, 0b101, 0b101, 0b110, 0b011],
    "R": [0b110, 0b101, 0b110, 0b101, 0b101],
    "S": [0b011, 0b100, 0b010, 0b001, 0b110],
    "T": [0b111, 0b010, 0b010, 0b010, 0b010],
    "U": [0b101, 0b101, 0b101, 0b101, 0b011],
    "V": [0b101, 0b101, 0b101, 0b010, 0b010],
    "W": [0b101, 0b101, 0b111, 0b111, 0b101],
    "X": [0b101, 0b101, 0b010, 0b101, 0b101],
    "Y": [0b101, 0b101, 0b010, 0b010, 0b010],
    "Z": [0b111, 0b001, 0b010, 0b100, 0b111],
    "Ö": [0b010, 0b111, 0b101, 0b111, 0b010],
}


def _luminance(hex_color: str) -> float:
    """Relative luminance of a hex color string like '#rrggbb'."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _text_color_for(bg_hex: str) -> str:
    """Return '#000000' or '#ffffff' for best contrast against bg_hex."""
    return "#000000" if _luminance(bg_hex) > 0.179 else "#ffffff"


def _pixel_bg_color(x: int, y: int, colors: tuple | None, x_offset: int) -> str:
    """Return the logo color painted at pixel (x, y) for the given team."""
    if not colors:
        p, s, a = "#444444", "#888888", None
    else:
        p, s, a = colors
    lx = x - x_offset  # local coordinate within 0-7
    if a is None:
        return p if lx <= (7 - y) else s
    else:
        if lx <= min(7, 5 - y):
            return p
        elif lx <= min(7, 7 - y):
            return s
        else:
            return a or p


def _abbr_char_positions(abbr: str, x_off: int):
    """Return list of (char, px, py) anchor positions for the abbreviation."""
    n = len(abbr)
    if n == 2:
        anchors = [(x_off + 0, 0), (x_off + 5, 3)]
    elif n == 3:
        anchors = [(x_off + 0, 0), (x_off + 5, 0), (x_off + 2, 3)]
    else:  # 4
        anchors = [(x_off + 0, 0), (x_off + 4, 0), (x_off + 0, 3), (x_off + 4, 3)]
    return list(zip(abbr, anchors))


def _abbr_pixel_coords(abbr: str, x_off: int) -> set[tuple[int, int]]:
    """Return the set of (x, y) coordinates occupied by abbreviation pixels."""
    coords: set[tuple[int, int]] = set()
    for ch, (px, py) in _abbr_char_positions(abbr, x_off):
        rows = _FONT3X5.get(ch.upper(), _FONT3X5.get("X", [0] * 5))
        for dy, row_bits in enumerate(rows):
            for dx in range(3):
                if row_bits & (0b100 >> dx):
                    coords.add((px + dx, py + dy))
    return coords


def _abbr_pixels(abbr: str, x_off: int, colors: tuple | None) -> list[str]:
    """Return dp draw commands for abbr overlaid on an 8×8 logo at x_off."""
    parts: list[str] = []
    for ch, (px, py) in _abbr_char_positions(abbr, x_off):
        rows = _FONT3X5.get(ch.upper(), _FONT3X5.get("X", [0] * 5))
        for dy, row_bits in enumerate(rows):
            for dx in range(3):
                if row_bits & (0b100 >> dx):
                    bg = _pixel_bg_color(px + dx, py + dy, colors, x_off)
                    text_col = _text_color_for(bg)
                    parts.append(f'{{"dp":[{px + dx},{py + dy},"{text_col}"]}}')
    return parts


_TEAM_COLORS = {
    # SHL
    "brynas_if": ("#2a2a2a", "#ffffff", "#fecc03"),
    "brynas": ("#2a2a2a", "#ffffff", "#fecc03"),
    "djurgardens_if": ("#fbea05", "#db0d1a", "#1261ab"),
    "djurgardens": ("#fbea05", "#db0d1a", "#1261ab"),
    "farjestad_bk": ("#ffffff", "#008e4f", "#e5b843"),
    "farjestad": ("#ffffff", "#008e4f", "#e5b843"),
    "frolunda_hc": ("#ffffff", "#0e5840", None),
    "frolunda": ("#ffffff", "#0e5840", None),
    "hc_frolunda": ("#ffffff", "#0e5840", None),
    "hv71": ("#052e59", "#fdd410", None),
    "linkoping_hc": ("#052d5c", "#ffffff", "#b10c21"),
    "linkoping": ("#052d5c", "#ffffff", "#b10c21"),
    "lhc": ("#052d5c", "#ffffff", "#b10c21"),
    "lulea_hf": ("#2a2a2a", "#d10c12", "#fdcd01"),
    "lulea": ("#2a2a2a", "#d10c12", "#fdcd01"),
    "malmo_redhawks": ("#ffffff", "#bb0b2e", "#2a2a2a"),
    "malmo": ("#ffffff", "#bb0b2e", "#2a2a2a"),
    "modo_hockey": ("#cf1f2d", "#ffffff", "#2c5235"),
    "modo": ("#cf1f2d", "#ffffff", "#2c5235"),
    "rogle_bk": ("#ffffff", "#067b35", None),
    "rogle": ("#ffffff", "#067b35", None),
    "skelleftea_aik": ("#fbba14", "#2a2a2a", None),
    "skelleftea": ("#fbba14", "#2a2a2a", None),
    "saik": ("#fbba14", "#2a2a2a", None),
    "timra_ik": ("#e32f25", "#ffffff", "#104999"),
    "timra": ("#e32f25", "#ffffff", "#104999"),
    "orebro_hk": ("#e3032a", "#ffffff", None),
    "orebro": ("#e3032a", "#ffffff", None),
    "vaxjo_lakers": ("#052f5d", "#eb7229", None),
    "vaxjo": ("#052f5d", "#eb7229", None),
    # HockeyAllsvenskan
    "aik": ("#2a2a2a", "#FFD700", None),
    "almtuna_is": ("#CC0000", "#FFD700", "#ffffff"),
    "almtuna": ("#CC0000", "#FFD700", "#ffffff"),
    "bik_karlskoga": ("#0b2d74", "#ffffff", None),
    "karlskoga": ("#0b2d74", "#ffffff", None),
    "if_bjorkloven": ("#0b5640", "#fdd003", None),
    "bjorkloven": ("#0b5640", "#fdd003", None),
    "ik_oskarshamn": ("#0b2d74", "#c10230", "#ffffff"),
    "oskarshamn": ("#0b2d74", "#c10230", "#ffffff"),
    "karlskrona_hk": ("#003F7F", "#FFFFFF", None),
    "karlskrona": ("#003F7F", "#FFFFFF", None),
    "kristianstad_ik": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "kristianstad": ("#CC0000", "#FFFFFF", "#1C1C1C"),
    "leksands_if": ("#0d3579", "#ffffff", None),
    "leksand": ("#0d3579", "#ffffff", None),
    "lif": ("#0d3579", "#ffffff", None),
    "mora_ik": ("#e42313", "#fcda00", "#007d32"),
    "mora": ("#e42313", "#fcda00", "#007d32"),
    "nybro_vikings_if": ("#e73137", "#2a2a2a", "#fed68f"),
    "nybro": ("#e73137", "#2a2a2a", "#fed68f"),
    "tingsryd_aif": ("#CC0000", "#FFFFFF", None),
    "tingsryd": ("#CC0000", "#FFFFFF", None),
    "vik_vasteras_hk": ("#fdd200", "#2a2a2a", None),
    "vasteras": ("#fdd200", "#2a2a2a", None),
    "vastervik_ik": ("#007755", "#FFFFFF", None),
    "vastervik": ("#007755", "#FFFFFF", None),
    "sodertalje_sk": ("#1264b0", "#fddf00", "#d4b882"),
    "sodertalje": ("#1264b0", "#fddf00", "#d4b882"),
    "huddinge_ik": ("#CC0000", "#FFFFFF", None),
    "huddinge": ("#CC0000", "#FFFFFF", None),
    "kalmar_hc": ("#ac0e09", "#ffffff", "#f1da9e"),
    "kalmar": ("#ac0e09", "#ffffff", "#f1da9e"),
    "troja_ljungby": ("#dc2f34", "#ffffff", None),
    "troja": ("#dc2f34", "#ffffff", None),
    "vimmerby_hockey": ("#fddd01", "#2a2a2a", "#ffffff"),
    "vimmerby": ("#fddd01", "#2a2a2a", "#ffffff"),
    "ostersunds_ik": ("#fded00", "#006633", None),
    "ostersund": ("#fded00", "#006633", None),
}

_TRANSLIT = str.maketrans("åäöÅÄÖéüÜ", "aaoAAOeuu")


def _tslug(name: str) -> str:
    s = name.translate(_TRANSLIT).lower()
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _draw_from_colors(colors: tuple | None, x_offset: int = 0, slug: str = "") -> str:
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
            seg(0, r, min(7, 6 - r), p)
            seg(max(0, 7 - r), r, 7, s)
        else:
            seg(0, r, min(7, 5 - r), p)
            seg(max(0, 6 - r), r, min(7, 8 - r), s)
            seg(max(0, 9 - r), r, 7, a)

    return ",".join(parts)


def _team_draw_fragment(team_name: str) -> str:
    """Return diagonal draw commands for a team at x=0..7 (left logo)."""
    slug = _tslug(team_name)
    colors = _TEAM_COLORS.get(slug)
    if not colors:
        for k, v in _TEAM_COLORS.items():
            if slug.startswith(k) or k.startswith(slug):
                colors = v
                slug = k
                break
    return _draw_from_colors(colors, 0, slug)


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
        l = _draw_from_colors(colors, 0, slug)
        r = _draw_from_colors(colors, 24, slug)
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
      value_template: "{{ states('sensor.hockeylive___SLUG___status') in ['upcoming_far', 'idle'] }}"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {%- set day = (ndt[8:10]|int(0))|string if ndt else '' -%}
          {%- set dx = 26 if day|length == 1 else 24 -%}
          {%- if day -%}{"draw":[__TEAMDRAW__,{"dl":[13,1,16,1,"#FFFFFF"]},{"dp":[12,2,"#FFFFFF"]},{"dp":[17,2,"#FFFFFF"]},{"dp":[12,3,"#FFFFFF"]},{"dl":[14,3,15,3,"#FFFFFF"]},{"dp":[17,3,"#FFFFFF"]},{"dp":[12,4,"#FFFFFF"]},{"dp":[14,4,"#FFFFFF"]},{"dp":[17,4,"#FFFFFF"]},{"dp":[12,5,"#FFFFFF"]},{"dl":[14,5,17,5,"#FFFFFF"]},{"dp":[12,6,"#FFFFFF"]},{"dl":[13,7,16,7,"#FFFFFF"]},{"df":[23,0,9,2,"#CC0000"]},{"df":[23,2,9,6,"#FFFFFF"]},{"dt":[{{ dx }},2,"{{ day }}","#000000"]}],"noScroll":true,"duration":10,"lifetime":600}{%- else -%}{%- endif -%}

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
      value_template: "{{ states('sensor.hockeylive___SLUG___status') == 'upcoming_countdown' and states('binary_sensor.hockeylive___SLUG___live') == 'off' }}"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {%- set mins = [((as_timestamp(ndt) - now().timestamp()) / 60)|int, 0]|max if ndt else 0 -%}
          {%- set hrs = mins // 60 -%}
          {%- set tx = 9 if hrs >= 10 else 14 -%}
          {%- set cdtext = '%d:%02d'|format(hrs, mins % 60) -%}
          {"draw":[__TEAMDRAW__,{"dt":[{{ tx }},1,"{{ cdtext }}","#FFFFFF"]}],"noScroll":true,"duration":10,"lifetime":120}

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
      value_template: "{{ states('sensor.hockeylive___SLUG___status') == 'upcoming_prematch' and states('binary_sensor.hockeylive___SLUG___live') == 'off' }}"
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
          {%- set own_goal = (team|lower|replace('å','a')|replace('ä','a')|replace('ö','o')|replace('é','e')|replace('ü','u')|replace(' ','_')|replace('-','_')|replace('.','')) == '__SLUG__' -%}
          {%- set rtttl = 'goal:d=8,o=5,b=100:c4,p2,d4' if own_goal else 'opp:d=4,o=3,b=100:c2' -%}
          {"draw":[__TEAMDRAW__],"text":[{"t":"     MAL! ","c":"FFD700"},{"t":"{{ sc }}","c":"FFFFFF"},{"t":" {{ score }}","c":"FFD700"}{% if ass %},{"t":" Ass: {{ ass|join(', ') }}","c":"888888"}{% endif %}{% if sit not in ['ES',''] %},{"t":" ({{ sit }})","c":"{{ sitc }}"}{% endif %}]__GOAL_SOUND_RTTTL__,"duration":30,"stack":false,"wakeup":true}

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


# ---------------------------------------------------------------------------
# Global helpers – input_number / input_text written once per generated file.
# ---------------------------------------------------------------------------
_HELPERS_BLOCK = """\
input_number:
  awtrix_goal_idx:
    name: "AWTRIX Mål-index"
    min: 0
    max: 99
    step: 1
    initial: 0

input_text:
  awtrix_goal_app:
    name: "AWTRIX Mål app"
    max: 64
    initial: ""

"""

# ---------------------------------------------------------------------------
# Global automations – track current app + middle-button goal history browser.
# These are appended once (not per watch) and use __PREFIX__ substitution.
# ---------------------------------------------------------------------------
_BUTTON_AUTOMATIONS = """\
- alias: "AWTRIX - Blockera inbyggd knappnavigering"
  id: "awtrix_block_nav_keys"
  trigger:
    - platform: homeassistant
      event: start
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/settings"
        payload: '{"BLOCKN":true}'

- alias: "AWTRIX - Knapp: föregående app"
  id: "awtrix_button_prev"
  mode: single
  max_exceeded: silent
  trigger:
    - platform: state
      entity_id: "binary_sensor.__PREFIX___button_left"
      to: "on"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/previousapp"
        payload: ""

- alias: "AWTRIX - Knapp: nästa app"
  id: "awtrix_button_next"
  mode: single
  max_exceeded: silent
  trigger:
    - platform: state
      entity_id: "binary_sensor.__PREFIX___button_right"
      to: "on"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/nextapp"
        payload: ""

- alias: "AWTRIX - Knapp: visa detaljer"
  id: "awtrix_button_details"
  mode: restart
  trigger:
    - platform: mqtt
      topic: "__PREFIX__/stats/buttonSelect"
      payload: "0"
  variables:
    current_app: "{{ states('sensor.awtrix_current_app') }}"
    last_app: "{{ states('input_text.awtrix_goal_app') }}"
    slug: "{{ (current_app[7:] if current_app.startswith('hockey_') else (last_app[7:] if last_app.startswith('hockey_') else '')) | trim }}"
    is_hockey: "{{ slug != '' }}"
    status: "{{ states('sensor.hockeylive_' ~ slug ~ '_status') if slug != '' else '' }}"
    is_upcoming: "{{ status in ['upcoming_far', 'upcoming_countdown', 'upcoming_prematch'] }}"
    goals: "{{ state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'goals') if slug != '' else [] }}"
    n: "{{ (goals or []) | length }}"
    details_showing: "{{ states('input_number.awtrix_goal_idx') | int == 1 }}"
  action:
    - choose:
        - conditions:
            - "{{ details_showing }}"
          sequence:
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 0
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: '{"duration":1,"text":"","stack":false}'
        - conditions:
            - "{{ is_hockey and is_upcoming }}"
          sequence:
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: >-
                  {%- set _nht = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_home_team') | default('') | string -%}
                  {%- set _nat = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_away_team') | default('') | string -%}
                  {%- set _ndt = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_datetime') | default('') | string -%}
                  {%- set _nt = state_attr('sensor.hockeylive_' ~ slug ~ '_status', 'next_time') | default('?') | string -%}
                  {%- set _months = ['Jan','Feb','Mar','Apr','Maj','Jun','Jul','Aug','Sep','Okt','Nov','Dec'] -%}
                  {%- if _ndt -%}
                  {%- set _d = _ndt[8:10] | int -%}
                  {%- set _m = _ndt[5:7] | int -%}
                  {%- set _suf = 'st' if _d in [1,21,31] else 'nd' if _d in [2,22] else 'rd' if _d in [3,23] else 'th' -%}
                  {%- set _date_str = (_d | string) ~ _suf ~ ' ' ~ _months[_m - 1] -%}
                  {%- else -%}
                  {%- set _date_str = '' -%}
                  {%- endif -%}
                  {"text":"{{ _nht }} - {{ _nat }}{% if _date_str %}, {{ _date_str }}{% endif %} {{ _nt }}","repeat":1,"stack":false}
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 1
            - delay: "00:00:10"
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 0
        - conditions:
            - "{{ is_hockey and (n | int) == 0 }}"
          sequence:
            - service: input_text.set_value
              target:
                entity_id: input_text.awtrix_goal_app
              data:
                value: "{{ 'hockey_' ~ slug }}"
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: '{"text":"Inga m\u00e5l","duration":5,"stack":false}'
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 1
            - delay: "00:00:05"
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 0
        - conditions:
            - "{{ is_hockey }}"
          sequence:
            - service: input_text.set_value
              target:
                entity_id: input_text.awtrix_goal_app
              data:
                value: "{{ 'hockey_' ~ slug }}"
            - service: mqtt.publish
              data:
                topic: "__PREFIX__/notify"
                payload: >-
                  {%- set goal_iter = goals | sort(attribute='game_time_secs') if status == 'finished_today' else goals | sort(attribute='game_time_secs', reverse=true) -%}
                  {%- set asc_goals = goals | sort(attribute='game_time_secs') -%}
                  {%- set n_goals = asc_goals | length -%}
                  {%- set ns_scan = namespace(prev_hs=0, home_flags=[]) -%}
                  {%- for g in asc_goals -%}
                  {%- set hs = g.home_score_after | default(0) | int -%}
                  {%- set ns_scan.home_flags = ns_scan.home_flags + [(hs > ns_scan.prev_hs)] -%}
                  {%- set ns_scan.prev_hs = hs -%}
                  {%- endfor -%}
                  {%- set dur = [20, n_goals * 8] | max -%}
                  {%- set ns = namespace(segs=[]) -%}
                  {%- for g in goal_iter -%}
                  {%- if not loop.first -%}{%- set ns.segs = ns.segs + ['{"t":"   ","c":"FFFFFF"}'] -%}{%- endif -%}
                  {%- set sc = g.scorer | default('?') -%}
                  {%- set hs = g.home_score_after | default(0) | int -%}
                  {%- set as_ = g.away_score_after | default(0) | int -%}
                  {%- set per = g.period | default('') -%}
                  {%- set clk = g.period_clock | default('') -%}
                  {%- set sit = g.situation | default('') -%}
                  {%- set sit_str = ' ' ~ sit if sit not in ['EQ','ES',''] else '' -%}
                  {%- if status == 'finished_today' -%}
                  {%- set home_scored = ns_scan.home_flags[loop.index0] -%}
                  {%- else -%}
                  {%- set home_scored = ns_scan.home_flags[n_goals - loop.index] -%}
                  {%- endif -%}
                  {%- if home_scored -%}
                  {%- set ns.segs = ns.segs + ['{"t":"' ~ hs ~ '","c":"FFD700"}', '{"t":"-","c":"FFFFFF"}', '{"t":"' ~ as_ ~ ' ","c":"FFFFFF"}', '{"t":"' ~ sc ~ ' ' ~ per ~ ' ' ~ clk ~ sit_str ~ '","c":"FFFFFF"}'] -%}
                  {%- else -%}
                  {%- set ns.segs = ns.segs + ['{"t":"' ~ hs ~ '","c":"FFFFFF"}', '{"t":"-","c":"FFFFFF"}', '{"t":"' ~ as_ ~ ' ","c":"FFD700"}', '{"t":"' ~ sc ~ ' ' ~ per ~ ' ' ~ clk ~ sit_str ~ '","c":"FFFFFF"}'] -%}
                  {%- endif -%}
                  {%- endfor -%}
                  {"text":[{{ ns.segs | join(',') }}],"duration":{{ dur }},"stack":false}
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 1
            - delay:
                seconds: "{{ [20, goals | length * 8] | max }}"
            - service: input_number.set_value
              target:
                entity_id: input_number.awtrix_goal_idx
              data:
                value: 0
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
            enabled.append(
                {
                    **w,
                    "awtrix_icon": (ow.get("awtrix_icon") or "").strip(),
                    "goal_sound": bool(ow.get("goal_sound")),
                }
            )
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
        goal_sound_rtttl = ',"rtttl":"{{ rtttl }}"' if w.get("goal_sound") else ""
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
                GOAL_SOUND_RTTTL=goal_sound_rtttl,
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

    # Append global button automations (once per prefix, not per watch)
    automation_blocks.append(
        _sub(_BUTTON_AUTOMATIONS, PREFIX=prefix, AWTRIX_DRAW_HDR=awtrix_draw_hdr)
    )

    # Write as HA Package (works alongside any existing automation: !include setup)
    preamble = "\n".join(
        [
            "# Auto-generated by HockeyLive add-on – do not edit manually",
            "# Re-generated on every add-on restart",
            "#",
            "# Add to configuration.yaml:",
            "#   homeassistant:",
            "#     packages: !include_dir_named packages/",
            "",
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

    OUT_FILE.write_text(
        preamble + _HELPERS_BLOCK + "automation:\n" + "\n".join(indented),
        encoding="utf-8",
    )
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
