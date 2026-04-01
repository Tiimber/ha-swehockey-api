# HockeyLive API

FastAPI-tjänst som hämtar schema, liveresultat, målstatistik och utvisningar för svenska ishockeylag från `stats.swehockey.se`. Stödjer flera lag och ligor samtidigt via ett bevakningssystem med unika ID:n.

---

## Innehåll

- [Snabbstart](#snabbstart)
- [Konfiguration](#konfiguration)
- [Docker](#docker)
- [Endpoints – översikt](#endpoints--översikt)
- [Bevakningssystem](#bevakningssystem--watch)
- [Livematchar](#livematchar)
- [Home Assistant Green (och HAOS generellt)](#home-assistant-green-och-haos-generellt)
- [Säsongsskifte](#säsongsskifte)
- [Licens](#licens)

---

## Snabbstart

```bash
git clone <repo> hockeylive-api
cd hockeylive-api
pip install -r requirements.txt
# Anpassa konfigurationen (se nedan)
cp config.yaml config.yaml
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Interaktiv API-dokumentation: `http://localhost:8080/docs`

---

## Konfiguration

`config.yaml` styr standardlaget som alltid är tillgängligt via `/next`, `/last`, `/live` etc.

```yaml
team: "Färjestad BK"   # Exakt stavning – kontrollera med GET /teams

season_ids:
  - 18263              # SHL 2025/26 grundserie
  - 19791              # SHL 2025/26 SM-slutspel

port: 8080
```

Miljövariabeln `HOCKEY_CONFIG` kan peka på en annan sökväg.  
Standardsökning (i prioritetsordning): `$HOCKEY_CONFIG` → `/data/config.yaml` → `/config/hockey.yaml` → `./config.yaml`.

### Kända säsongs-ID:n

Säsong-ID:t syns i URL:en på `stats.swehockey.se`:

```
https://stats.swehockey.se/ScheduleAndResults/Schedule/18263
                                                            ^^^^^
```

| Liga | Säsong | ID |
|---|---|---|
| SHL | 2025/26 grundserie | `18263` |
| SHL | 2025/26 SM-slutspel | `19791` |
| HockeyAllsvenskan | 2025/26 grundserie | `18266` |
| HockeyAllsvenskan | 2025/26 slutspel | `19979` |

> Nya ID:n varje år – API:et loggar förslag automatiskt när alla kända matcher är avklarade.

### Viktigt om lagnamn

Stavningen måste matcha exakt vad swehockey.se använder:
- `"Färjestad BK"` (inte "Färjestads BK")
- `"HV 71"` (med mellanslag)
- `"IF Malmö Redhawks"`

Kontrollera med:
```bash
curl http://localhost:8080/teams
# eller sök:
curl "http://localhost:8080/search?q=HV"
```

---

## Docker

### Bygg och starta

```bash
docker compose up -d --build
```

### Köra direkt med `docker run`

```bash
docker build -t hockeylive-api .
docker run -d \
  --restart unless-stopped \
  -p 8080:8080 \
  -v $(pwd)/config.yaml:/app/config.yaml:ro \
  -v $(pwd)/watchlist.json:/app/watchlist.json \
  --name hockeylive \
  hockeylive-api
```

> Montera `watchlist.json` som volym så att bevakningar överlever omstarter. Filen skapas automatiskt om den inte finns.

### Raspberry Pi (arm/v7)

Bygg direkt på Pi:n – alla beroenden stödjer arm/v7:

```bash
git clone <repo> hockeylive-api && cd hockeylive-api
docker compose up -d --build
```

---

## Endpoints – översikt

### Standardlag (från `config.yaml`)

| Metod | Endpoint | Beskrivning |
|---|---|---|
| `GET` | `/` | API-info, konfigurerat lag, alla cachade säsonger |
| `GET` | `/next` | Nästa (eller pågående) match |
| `GET` | `/last` | Senaste avklarade match |
| `GET` | `/live` | Livedata – 404 om ingen aktiv match |
| `GET` | `/status` | Kombinerad snapshot (live + nästa + senaste) |
| `GET` | `/summary` | Detaljerad vy: previous / current / next |
| `GET` | `/schedule` | Hela schemat för konfigurerat lag |
| `GET` | `/teams` | Alla lagnamn i konfigurerade säsonger |
| `GET` | `/refresh` | Tvinga omhämtning av alla cachade säsonger |

### Bevakningssystem

| Metod | Endpoint | Beskrivning |
|---|---|---|
| `GET` | `/search?q=...` | Sök lagnamn i alla kända säsonger |
| `GET` | `/watches` | Lista alla bevakningar med ID:n |
| `POST` | `/watch` | Lägg till bevakning, returnerar unikt ID |
| `DELETE` | `/watch/{id}` | Ta bort bevakning |
| `GET` | `/watch/{id}/status` | Kombinerad snapshot för en bevakning |
| `GET` | `/watch/{id}/next` | Nästa match |
| `GET` | `/watch/{id}/last` | Senaste resultat |
| `GET` | `/watch/{id}/live` | Livedata – 404 om ingen aktiv match |
| `GET` | `/watch/{id}/schedule` | Hela schemat |

### Per-lag (URL-encodat lagnamn)

| Metod | Endpoint | Beskrivning |
|---|---|---|
| `GET` | `/team/{team}/status` | Combinerad snapshot – sammanslår alla bevakningar för laget |
| `GET` | `/team/{team}/next` | Nästa match |
| `GET` | `/team/{team}/last` | Senaste resultat |
| `GET` | `/team/{team}/live` | Livedata |
| `GET` | `/team/{team}/schedule` | Hela schemat |

Mellanslag i lagnamnet URL-enkodas: `HV%2071`.

---

## Bevakningssystem – `/watch`

Bevakningar låter dig följa ett lag i en eller flera ligor/säsonger via ett **permanent unikt ID**. ID:t är deterministiskt – samma lag + säsongslista ger alltid samma ID.

### Typiskt flöde

**1. Hitta exakt lagnamn**
```bash
curl "http://localhost:8080/search?q=HV"
```
```json
{
  "results": [
    {"team": "HV 71", "season_id": 18263, "season_name": "SHL 2025/26", "is_watched": false}
  ]
}
```

**2. Lägg till bevakning – ett eller flera säsongs-ID:n**
```bash
curl -X POST http://localhost:8080/watch \
  -H "Content-Type: application/json" \
  -d '{"team": "HV 71", "season_ids": [18263, 19791]}'
```
```json
{
  "id": "a1b2c3d4",
  "created": true,
  "team": "HV 71",
  "seasons": [
    {"season_id": 18263, "season_name": "SHL 2025/26 grundserie"},
    {"season_id": 19791, "season_name": "SHL 2025/26 SM-slutspel"}
  ],
  "links": {
    "status":   "/watch/a1b2c3d4/status",
    "next":     "/watch/a1b2c3d4/next",
    "last":     "/watch/a1b2c3d4/last",
    "live":     "/watch/a1b2c3d4/live",
    "schedule": "/watch/a1b2c3d4/schedule"
  }
}
```

**Samma lag i SHL + CHL – sammanslaget:**
```bash
curl -X POST http://localhost:8080/watch \
  -d '{"team": "Frölunda HC", "season_ids": [18263, 32100]}'
# Returnerar ett ID som täcker båda ligorna
```

**3. Använd ID:t**
```bash
curl http://localhost:8080/watch/a1b2c3d4/status
curl http://localhost:8080/watch/a1b2c3d4/live
curl http://localhost:8080/watch/a1b2c3d4/next
```

**4. Ta bort bevakning**
```bash
curl -X DELETE http://localhost:8080/watch/a1b2c3d4
```

**Lista alla bevakningar:**
```bash
curl http://localhost:8080/watches
```

> Bevakningar sparas i `watchlist.json` och överlever omstarter.

---

## Livematchar

Under en pågående match returnerar `/live`-endpointerna (och `/status`) utökad data:

```json
{
  "status": "live",
  "game": {
    "home_team": "HV 71",
    "away_team": "Färjestad BK",
    "home_score": 2,
    "away_score": 1,
    "period": "P2",
    "period_label": "Period 2",
    "period_clock": "12:34",
    "is_overtime": false,
    "is_shootout": false,

    "last_goal": {
      "game_time": "32:14",
      "period": "P2",
      "period_clock": "12:14",
      "team": "HV 71",
      "scorer": "Eriksson, Erik",
      "assists": ["Andersson, Anders", "Larsson, Lars"],
      "situation": "PP1",
      "home_score_after": 2,
      "away_score_after": 1,
      "secs_since": 312
    },

    "goals": [ ... ],

    "active_penalties": [
      {
        "game_time": "28:03",
        "period": "P2",
        "period_clock": "08:03",
        "team": "Färjestad BK",
        "player": "Johansson, Johan",
        "duration_min": 2,
        "offense": "Holding",
        "is_active": true,
        "elapsed_secs": 251,
        "elapsed_mmss": "04:11",
        "remaining_secs": 69,
        "remaining_mmss": "01:09"
      }
    ],

    "penalties": [ ... ]
  }
}
```

### Fält förklarade

| Fält | Beskrivning |
|---|---|
| `period_clock` | Tid *inom* aktuell period (`mm:ss`) |
| `situation` | `EQ` / `PP1` / `PP2` / `SH` / `SH2` / `EN` / `PS` |
| `secs_since` | Sekunder sedan händelsen (speltid) |
| `elapsed_mmss` | Tid avverkad av utvisning |
| `remaining_mmss` | Tid kvar av utvisning |
| `is_active` | `true` om utvisningen fortfarande pågår |

---

## Home Assistant Green (och HAOS generellt)

HA Green kör Home Assistant OS (HAOS). Du kan inte starta godtyckliga processer direkt i skalet – allt körs som supervisade add-ons. Guiden nedan installerar HockeyLive API som ett **lokalt add-on** via SSH & Web Terminal. Du får hela REST API:et med watchlist, search och watch-IDs, samt HA-sensorer via REST-integration.

---

### Steg 1 – Installera SSH & Web Terminal

Det här add-onet ger dig ett skal direkt i HA-webbläsaren, och används för att kopiera filer och hantera add-ons.

1. In HA: **Settings → Add-ons → Add-on Store** (bottom right: blue button)
2. Search for **SSH & Web Terminal** → click it → **Install**
3. Go to the **Configuration** tab before starting. Set a password (or paste a public key under `authorized_keys`):
   ```yaml
   ssh:
     username: root
     password: "your-password-here"
     authorized_keys: []
     sftp: false
   ```
4. **Save** → go to the **Info** tab → toggle **Show in sidebar** → **Start**
5. Click **Terminal** in the sidebar – you now have a root shell on HA Green

> **Tip:** You can also SSH from your PC: `ssh root@<ha-ip>` (default port 22). Find your HA Green's IP under **Settings → System → Network**.

---

### Steg 2 – Kopiera add-on-filerna till HA Green

Kör följande i terminalen (antingen via webbläsaren eller ssh från din PC). Byt ut `<repo-url>` mot URL:en till det här repot.

```bash
# In the SSH terminal (browser or ssh root@<ha-ip>):
cd /addons
git clone <repo-url> hockeylive-src

mkdir -p hockeylive
cp hockeylive-src/homeassistant/addon/config.yaml       hockeylive/
cp hockeylive-src/homeassistant/addon/Dockerfile         hockeylive/
cp hockeylive-src/homeassistant/addon/run.sh             hockeylive/
cp hockeylive-src/homeassistant/addon/generate_config.py hockeylive/
cp hockeylive-src/app.py \
   hockeylive-src/scraper.py \
   hockeylive-src/config.py \
   hockeylive-src/watchlist.py \
   hockeylive-src/requirements.txt \
   hockeylive/

# Verify the directory looks right:
ls /addons/hockeylive/
```

Du ska se: `config.yaml  Dockerfile  run.sh  generate_config.py  app.py  scraper.py  config.py  watchlist.py  requirements.txt`

---

### Steg 3 – Installera add-onet i HA

1. **Settings → Add-ons → Add-on Store → ⋮ (three dots, top right) → Check for updates**
2. Scroll down to **Local add-ons** – **HockeyLive API** appears there
3. Click it → **Install** (takes a few minutes – Python and lxml are compiled for aarch64)
4. When done, go to the **Configuration** tab and set your team and season IDs:
   ```yaml
   team: "HV 71"
   season_ids:
     - 18263
     - 19791
   ```
5. **Save** → **Info** tab → **Start**
6. Check the **Log** tab to confirm it started – you should see:
   ```
   INFO:     Application startup complete.
   INFO:     Uvicorn running on http://0.0.0.0:8080
   ```

API:et är nu nåbart på `http://<ha-ip>:8080`.  
Öppna `http://<ha-ip>:8080/docs` för interaktiv dokumentation.

---

### Steg 4 – Lägg till ett lag i watchlist och hämta dess ID

Öppna terminalen igen (SSH & Web Terminal):

```bash
# Add a watch entry and get its ID:
curl -s -X POST http://localhost:8080/watch \
  -H "Content-Type: application/json" \
  -d '{"team": "HV 71", "season_ids": [18263, 19791]}' | python3 -m json.tool
```

Svaret ser ut så här – notera `"id"`:

```json
{
  "id": "a1b2c3d4",
  "team": "HV 71",
  "season_ids": [18263, 19791],
  "created": true
}
```

Testa att endpointen fungerar:

```bash
curl -s http://localhost:8080/watch/a1b2c3d4/status | python3 -m json.tool
```

---

### Steg 5 – Konfigurera REST-sensorer i HA

Redigera `/config/configuration.yaml` – enklast via **File Editor** add-onet eller SSH-terminalen:

```bash
# In the SSH terminal:
vi /config/configuration.yaml
# (or: nano /config/configuration.yaml)
```

Lägg till (byt `a1b2c3d4` mot ditt faktiska watch-ID):

```yaml
rest:
  - resource: http://localhost:8080/watch/a1b2c3d4/status
    scan_interval: 30
    sensor:
      - name: "HV71 Status"
        unique_id: hv71_status
        value_template: >-
          {% if value_json.live.is_playing %}
            Playing – {{ value_json.live.period_label }}
          {% elif value_json.next_match %}
            Next: {{ value_json.next_match.datetime_iso[:10] }}
          {% else %}No match scheduled{% endif %}
        json_attributes_path: "$"
        json_attributes: [live, next_match, last_match]

      - name: "HV71 Live Score"
        unique_id: hv71_live_score
        value_template: >-
          {% if value_json.live.is_playing %}
            {{ value_json.live.home_score }}–{{ value_json.live.away_score }}
          {% else %}–{% endif %}

      - name: "HV71 Period"
        unique_id: hv71_period
        value_template: >-
          {{ value_json.live.period_label if value_json.live.is_playing else '–' }}

      - name: "HV71 Last Goal"
        unique_id: hv71_last_goal
        value_template: >-
          {% set g = value_json.live.last_goal %}
          {% if g %}{{ g.scorer }} ({{ g.situation }}, {{ g.period }}){% else %}–{% endif %}
        json_attributes_path: "$.live.last_goal"
        json_attributes:
          - scorer
          - assists
          - situation
          - period
          - period_clock
          - home_score_after
          - away_score_after
          - secs_since

      - name: "HV71 Next Match"
        unique_id: hv71_next_match
        value_template: >-
          {{ value_json.next_match.datetime_iso[:16].replace('T',' ') if value_json.next_match else '–' }}
        json_attributes_path: "$.next_match"
        json_attributes: [opponent, venue, is_home_game, home_team, away_team]

      - name: "HV71 Last Result"
        unique_id: hv71_last_result
        value_template: >-
          {% set m = value_json.last_match %}
          {% if m and m.home_score is not none %}{{ m.home_score }}–{{ m.away_score }}{% else %}–{% endif %}
        json_attributes_path: "$.last_match"
        json_attributes: [opponent, won, score_for, score_against, datetime_iso]

binary_sensor:
  - platform: rest
    resource: http://localhost:8080/watch/a1b2c3d4/status
    name: "HV71 Playing Now"
    unique_id: hv71_is_live
    value_template: "{{ value_json.live.is_playing }}"
    scan_interval: 30
    device_class: running
```

Ladda om konfigurationen: **Settings → System → Restart → Quick reload** (or full restart).  
Kontrollera att sensorerna dykt upp under **Settings → Devices & Services → Entities**.

> **Tip:** Use `http://localhost:8080` (not the HA IP) – it works even if your DHCP address changes and avoids the external network hop.

---

### Dashboard-kort

```yaml
type: entities
title: HV 71
entities:
  - entity: binary_sensor.hv71_playing_now
    name: Playing now
  - entity: sensor.hv71_live_score
    name: Score
  - entity: sensor.hv71_period
    name: Period
  - entity: sensor.hv71_last_goal
    name: Last goal
  - entity: sensor.hv71_next_match
    name: Next match
  - entity: sensor.hv71_last_result
    name: Last result
```

### Uppdatera add-onet

Vid ny version av koden:

```bash
# In the SSH terminal:
cd /addons/hockeylive-src
git pull
cp app.py scraper.py config.py watchlist.py ../hockeylive/
```

Gå sedan till **Settings → Add-ons → HockeyLive API → ⋮ → Restart**.  
`watchlist.json` (dina bevakningar) sparas i `/data/` och berörs inte av uppdateringen.

---

## Säsongsskifte

1. Hämta nya `season_ids` från `stats.swehockey.se`
2. Uppdatera `config.yaml` (eller add-on-konfigurationen)
3. Starta om containern / add-on: `docker compose restart`

API:et loggar automatiskt nya säsongs-ID:n det hittar på swehockey.se när alla kända matcher är avklarade. Befintliga bevakningar (`watchlist.json`) behåller sina ID:n och behöver inte uppdateras – lägg bara till nya säsonger med `POST /watch`.

---

## Licens

MIT – fri att använda för privat bruk. Data tillhör Svenska Ishockeyförbundet / swehockey.se.
