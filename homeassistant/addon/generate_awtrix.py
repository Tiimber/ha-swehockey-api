#!/usr/bin/env python3
"""
generate_awtrix.py – Auto-generates HA automation YAML for AWTRIX Hockey Scoreboard.

Reads  : /data/options.json   (awtrix_prefix, per-watch awtrix / awtrix_icon flags)
         /data/watchlist.json  (watch IDs and names)
Writes : /config/automations/hockeylive_awtrix.yaml

Requires in configuration.yaml:
    automation: !include_dir_merge_list automations/

Per watch (when awtrix: true) the following automations are created:
  1. No match today      – show team icon + date of next match
  2. Countdown           – match is today but not started, countdown in minutes
  3. Live scoreboard     – H letter | score | A letter + 5 period dots
  4. Goal notification   – 30-second notify with scorer / assists / situation
  5. Match finished      – final score + period dots coloured green/red
  6. Midnight clear      – removes the custom app at 00:00:30
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

OPTIONS   = Path("/data/options.json")
WATCHLIST = Path("/data/watchlist.json")
OUT_FILE  = Path("/config/automations/hockeylive_awtrix.yaml")


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
  condition:
    - condition: template
      value_template: >
        {{ states('sensor.hockeylive___SLUG___status') in ['upcoming','idle']
           and not state_attr('sensor.hockeylive___SLUG___status', 'next_today') }}
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {"icon":"__ICON__","text":[{"t":"Nasta: ","c":"888888"},{"t":"{{ (state_attr('sensor.hockeylive___SLUG___status','next_datetime') or '')[:10]|replace('-','/') }}","c":"FFFFFF"}],"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Nedrakning till match"
  id: "awtrix___WID___countdown"
  trigger:
    - platform: time_pattern
      minutes: "/1"
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
  condition:
    - condition: template
      value_template: >
        {{ states('sensor.hockeylive___SLUG___status') == 'upcoming'
           and state_attr('sensor.hockeylive___SLUG___status', 'next_today') == true
           and states('binary_sensor.hockeylive___SLUG___live') == 'off' }}
  action:
    - variables:
        mins_left: >
          {%- set ndt = state_attr('sensor.hockeylive___SLUG___status','next_datetime') -%}
          {{ [((as_timestamp(ndt) - now().timestamp()) / 60)|int, 0]|max if ndt else 0 }}
        cdtext: >
          {%- set m = mins_left|int -%}
          {%- if m >= 60 -%}{{ (m // 60)|string }}h {{ (m % 60)|string }}min
          {%- elif m > 0 -%}{{ m }}min
          {%- else -%}Nu!{%- endif -%}
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {"icon":"__ICON__","text":[{"t":"{{ state_attr('sensor.hockeylive___SLUG___status','next_time') }} ","c":"FFD700"},{"t":"{{ cdtext }}","c":"FFFFFF"}],"duration":10,"lifetime":120}

- alias: "AWTRIX __NAME__ - Live scoreboard"
  id: "awtrix___WID___live"
  trigger:
    - platform: state
      entity_id: sensor.hockeylive___SLUG___status
    - platform: state
      entity_id: binary_sensor.hockeylive___SLUG___live
  condition:
    - condition: state
      entity_id: binary_sensor.hockeylive___SLUG___live
      state: "on"
  action:
    - service: mqtt.publish
      data:
        topic: "__PREFIX__/custom/__APP__"
        payload: >-
          {%- set p = state_attr('sensor.hockeylive___SLUG___status','period') -%}
          {%- set hs = state_attr('sensor.hockeylive___SLUG___status','home_score')|default(0)|int -%}
          {%- set as_ = state_attr('sensor.hockeylive___SLUG___status','away_score')|default(0)|int -%}
          {%- set hl = (state_attr('sensor.hockeylive___SLUG___status','home_team') or '?')[0]|upper -%}
          {%- set al = (state_attr('sensor.hockeylive___SLUG___status','away_team') or '?')[0]|upper -%}
          {%- set p1c = '#1E3A5F' if p in ['P2','P3','OT','SO'] else '#FFD700' if p == 'P1' else '#404040' -%}
          {%- set p2c = '#1E3A5F' if p in ['P3','OT','SO'] else '#FFD700' if p == 'P2' else '#404040' -%}
          {%- set p3c = '#1E3A5F' if p in ['OT','SO'] else '#FFD700' if p == 'P3' else '#404040' -%}
          {%- set otc = '#1E3A5F' if p == 'SO' else '#FFD700' if p == 'OT' else '#404040' -%}
          {%- set soc = '#FFD700' if p == 'SO' else '#404040' -%}
          {"draw":[{"dt":[0,1,"{{ hl }}","#FFFFFF"]},{"dt":[9,1,"{{ hs }}-{{ as_ }}","#FFD700"]},{"dt":[25,1,"{{ al }}","#FFFFFF"]},{"dp":[4,7,"{{ p1c }}"]},{"dp":[10,7,"{{ p2c }}"]},{"dp":[16,7,"{{ p3c }}"]},{"dp":[22,7,"{{ otc }}"]},{"dp":[28,7,"{{ soc }}"]}],"noScroll":true,"lifetime":120}

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
          {"icon":"{{ team[0]|upper }}","text":[{"t":"MAL! ","c":"FFD700"},{"t":"{{ sc }}","c":"FFFFFF"},{"t":" {{ score }}","c":"FFD700"}{% if ass %},{"t":" Ass: {{ ass|join(', ') }}","c":"888888"}{% endif %}{% if sit not in ['ES',''] %},{"t":" ({{ sit }})","c":"{{ sitc }}"}{% endif %}],"duration":30,"stack":false,"wakeup":true}

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
          {"icon":"__ICON__","text":[{"t":"{{ ls }}","c":"{{ dc }}"},{"t":" {{ '+' if won else 'x' }}","c":"{{ dc }}"}],"draw":[{"dp":[8,7,"{{ dc }}"]},{"dp":[13,7,"{{ dc }}"]},{"dp":[18,7,"{{ dc }}"]},{"dp":[23,7,"{{ oc }}"]},{"dp":[28,7,"#404040"]}],"duration":30,"lifetime":3600}

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
        print("[generate_awtrix] watchlist.json not found – run generate_config.py first")
        return

    raw = json.loads(WATCHLIST.read_text(encoding="utf-8"))
    watches = (raw.get("watches") or raw) if isinstance(raw, dict) else {}

    # Map display name → options entry (for awtrix flag + icon)
    opt_map = {(w.get("name") or w.get("team", "")): w for w in options.get("watches", [])}

    enabled = []
    for wid, w in watches.items():
        name = w.get("name") or w.get("team", "")
        ow = opt_map.get(name, {})
        if ow.get("awtrix"):
            enabled.append({**w, "awtrix_icon": (ow.get("awtrix_icon") or "").strip()})

    if not enabled:
        print("[generate_awtrix] No watches with awtrix: true – skipping")
        return

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Auto-generated by HockeyLive add-on – do not edit manually",
        "# Re-generated on every add-on restart",
        "# Requires: automation: !include_dir_merge_list automations/",
        "",
    ]
    for w in enabled:
        name = w.get("name") or w["team"]
        slug = _slugify(name)
        icon = w.get("awtrix_icon") or name[0].upper()
        lines.append(_sub(
            _T,
            NAME=name,
            WID=w["id"],
            SLUG=slug,
            ICON=icon,
            APP=f"hockey_{slug}",
            PREFIX=prefix,
        ))

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"[generate_awtrix] {len(enabled)} watch(es) → {OUT_FILE}")
    print(f"[generate_awtrix] Prefix: {prefix}, Watches: {[w.get('name') or w['team'] for w in enabled]}")


if __name__ == "__main__":
    main()
