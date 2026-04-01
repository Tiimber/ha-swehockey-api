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

HA Green kör Home Assistant OS (HAOS). Du **kan inte** starta godtyckliga processer direkt i skalet – allt körs som supervisade add-ons. Det finns två alternativ:

| | **Alternativ A – Custom integration** | **Alternativ B – Lokal add-on** |
|---|---|---|
| Vad du får | HA-entiteter (sensorer) direkt | Hela REST API:et med watchlist, search, ID:n |
| Extra server | Nej – pratar direkt med swehockey.se | Nej – körs som add-on på HA Green |
| Kräver | Fil-åtkomst till HA-config | Fil-åtkomst + Add-on Store-reload |
| Bäst om | Du bara vill ha HA-sensorer | Du vill använda `/watch`, `/search` etc. |

---

### Förutsättningar (båda alternativen)

Du behöver fil-åtkomst till HA Green. Installera **ett** av:

- **Samba share** – monterar HA:s filsystem på din PC som en nätverksdelning (`\\<ha-ip>\`)
- **SSH & Web Terminal** – ger dig ett shall direkt i webbläsaren (eller via `ssh root@<ha-ip>`)
- **Studio Code Server** – VSCode i webbläsaren med full fil-åtkomst

Installeras via: **Inställningar → Tillägg → Lägg till tillägg → sök på respektive namn**

---

### Alternativ A – Custom Integration (enklest)

Integrationen kommunicerar direkt med swehockey.se och skapar HA-entiteter. Ingen separat server behövs.

#### Installation

**Alternativ A1 – Via HACS (rekommenderat)**

1. Installera [HACS](https://hacs.xyz/docs/setup/download/) om du inte redan har det
2. HACS → **Integrationer → ⋮ → Custom repositories**
3. Lägg till repo-URL:en, välj typ **Integration** → Lägg till
4. Sök på **HockeyLive** → Installera
5. Starta om HA: **Inställningar → System → Starta om**

**Alternativ A2 – Manuell kopia (Samba)**

1. Montera Samba-delningen på din PC: `\\<ha-ip>\config`
2. Skapa mappen `custom_components\hockeylive` om den inte finns
3. Kopiera innehållet från `custom_components/hockeylive/` i det här repot dit
4. Starta om HA

**Alternativ A2b – Manuell kopia (SSH)**

```bash
# Kör på din PC (repo måste vara klonat lokalt)
scp -r custom_components/hockeylive root@<ha-ip>:/config/custom_components/
```

Starta sedan om HA.

#### Konfigurera i UI

1. **Inställningar → Enheter & tjänster → Lägg till integration**
2. Sök på **HockeyLive**
3. **Steg 1** – Ange säsongs-ID:n (kommaseparerade):
   ```
   18263, 19791
   ```
4. **Steg 2** – Välj lag ur listan → **Slutför**

Varje lag är en egen config entry. Upprepa för fler lag.

#### Entiteter per lag

| Entitet | Typ | Exempelvärde |
|---|---|---|
| `sensor.<lag>_nasta_match` | Sensor | `2026-04-05T19:00:00+02:00` |
| `sensor.<lag>_senaste_resultat` | Sensor | `3–1` |
| `sensor.<lag>_live_score` | Sensor | `2–1` |
| `sensor.<lag>_period` | Sensor | `Period 2` |
| `binary_sensor.<lag>_spelar_nu` | Binary sensor | `on` / `off` |

---

### Alternativ B – Lokal Add-on (full REST API)

Paketerar FastAPI-servern som en supervisad Docker-container på HA Green. Ger tillgång till hela REST API:et med watchlist, search, watch-ID:n m.m.

#### Bygg add-on-katalogen (på din PC)

```bash
git clone <repo> hockeylive-api
cd hockeylive-api
./scripts/build_addon.sh      # skapar /tmp/hockeylive-addon/
```

#### Kopiera till HA Green

**Via Samba:**

1. Montera `\\<ha-ip>\addons` på din PC
2. Skapa mappen `hockeylive` där
3. Kopiera allt från `/tmp/hockeylive-addon/` dit

**Via SCP:**

```bash
scp -r /tmp/hockeylive-addon/ root@<ha-ip>:/addons/hockeylive
```

**Via SSH direkt på HA:**

```bash
# I SSH & Web Terminal-shallet på HA Green:
cd /addons
git clone <repo> hockeylive-src
mkdir -p hockeylive
cp hockeylive-src/homeassistant/addon/config.yaml    hockeylive/
cp hockeylive-src/homeassistant/addon/Dockerfile      hockeylive/
cp hockeylive-src/homeassistant/addon/run.sh          hockeylive/
cp hockeylive-src/homeassistant/addon/generate_config.py hockeylive/
cp hockeylive-src/app.py hockeylive-src/scraper.py \
   hockeylive-src/config.py hockeylive-src/watchlist.py \
   hockeylive-src/requirements.txt hockeylive/
```

#### Installera add-on i HA

1. **Inställningar → Tillägg → Tilläggsbutik → ⋮ → Sök efter uppdateringar**
2. Scrolla ner – **HockeyLive API** dyker upp under *Lokala tillägg*
3. Klicka på det → **Installera** (tar några minuter, Python + lxml byggs)
4. Gå till **Konfiguration**-fliken:
   ```
   team: "HV 71"
   season_ids:
     - 18263
     - 19791
   ```
5. **Starta**

API:et är nu nåbart på `http://<ha-ip>:8080`.  
Öppna `http://<ha-ip>:8080/docs` för interaktiv dokumentation.

#### Konfigurera REST-sensorer i HA

Lägg till laget som en bevakning via API:et, ta upp watch-ID:t, och använd det i `configuration.yaml`:

```bash
# Från din PC eller SSH-terminalen:
curl -X POST http://<ha-ip>:8080/watch \
  -H "Content-Type: application/json" \
  -d '{"team": "HV 71", "season_ids": [18263, 19791]}'
# Notera "id" i svaret, t.ex. "a1b2c3d4"
```

```yaml
# configuration.yaml
rest:
  - resource: http://localhost:8080/watch/a1b2c3d4/status
    scan_interval: 30
    sensor:
      - name: "HV71 Status"
        unique_id: hv71_status
        value_template: >-
          {% if value_json.live.is_playing %}
            Spelar – {{ value_json.live.period_label }}
          {% elif value_json.next_match %}
            Nästa: {{ value_json.next_match.datetime_iso[:10] }}
          {% else %}Ingen match planerad{% endif %}
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

      - name: "HV71 Senaste mål"
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

      - name: "HV71 Nästa match"
        unique_id: hv71_next_match
        value_template: >-
          {{ value_json.next_match.datetime_iso[:16].replace('T',' ') if value_json.next_match else '–' }}
        json_attributes_path: "$.next_match"
        json_attributes: [opponent, venue, is_home_game, home_team, away_team]

      - name: "HV71 Senaste resultat"
        unique_id: hv71_last_result
        value_template: >-
          {% set m = value_json.last_match %}
          {% if m and m.home_score is not none %}{{ m.home_score }}–{{ m.away_score }}{% else %}–{% endif %}
        json_attributes_path: "$.last_match"
        json_attributes: [opponent, won, score_for, score_against, datetime_iso]

binary_sensor:
  - platform: rest
    resource: http://localhost:8080/watch/a1b2c3d4/status
    name: "HV71 Spelar nu"
    unique_id: hv71_is_live
    value_template: "{{ value_json.live.is_playing }}"
    scan_interval: 30
    device_class: running
```

> **Tips:** Använd `http://localhost:8080` (inte HA-IP:n) när add-on och HA körs på samma enhet – det är snabbare och fungerar även om DHCP-IP:n ändras.

#### Lovelace-kort

```yaml
type: entities
title: HV 71
entities:
  - entity: binary_sensor.hv71_spelar_nu
    name: Spelar nu
  - entity: sensor.hv71_live_score
    name: Ställning
  - entity: sensor.hv71_period
    name: Period
  - entity: sensor.hv71_senaste_mal
    name: Senaste mål
  - entity: sensor.hv71_nasta_match
    name: Nästa match
  - entity: sensor.hv71_senaste_resultat
    name: Senaste resultat
```

---

## Säsongsskifte

1. Hämta nya `season_ids` från `stats.swehockey.se`
2. Uppdatera `config.yaml` (eller add-on-konfigurationen)
3. Starta om containern / add-on: `docker compose restart`

API:et loggar automatiskt nya säsongs-ID:n det hittar på swehockey.se när alla kända matcher är avklarade. Befintliga bevakningar (`watchlist.json`) behåller sina ID:n och behöver inte uppdateras – lägg bara till nya säsonger med `POST /watch`.

---

## Licens

MIT – fri att använda för privat bruk. Data tillhör Svenska Ishockeyförbundet / swehockey.se.
