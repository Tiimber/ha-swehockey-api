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
- [Home Assistant – REST-sensor (API)](#home-assistant--rest-sensor-api)
- [Home Assistant – Custom Integration (direkt)](#home-assistant--custom-integration-direkt)
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

## Home Assistant – REST-sensor (API)

Används när API:et körs på en separat server eller Raspberry Pi.

### Förutsättningar

- API:et körs och är nåbart från HA (t.ex. `http://192.168.1.100:8080`)
- HA version 2024.1 eller senare

### Steg

**1.** Starta API:et (se [Docker](#docker)).

**2.** Lägg till i `configuration.yaml`:

```yaml
rest:
  - resource: http://192.168.1.100:8080/watch/a1b2c3d4/status
    scan_interval: 30
    sensor:
      - name: "HV71 Status"
        unique_id: hv71_status
        value_template: >-
          {% if value_json.live.is_playing %}
            Spelar – {{ value_json.live.period_label }}
          {% elif value_json.next_match %}
            Nästa: {{ value_json.next_match.datetime_iso[:10] }}
          {% else %}
            Ingen match planerad
          {% endif %}
        json_attributes_path: "$"
        json_attributes:
          - live
          - next_match
          - last_match
          - team

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
          {% if g %}{{ g.scorer }} ({{ g.team }}){% else %}–{% endif %}
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
        json_attributes:
          - opponent
          - venue
          - is_home_game
          - home_team
          - away_team

      - name: "HV71 Senaste resultat"
        unique_id: hv71_last_result
        value_template: >-
          {% set m = value_json.last_match %}
          {% if m and m.home_score is not none %}
            {{ m.home_score }}–{{ m.away_score }}
          {% else %}–{% endif %}
        json_attributes_path: "$.last_match"
        json_attributes:
          - opponent
          - won
          - score_for
          - score_against
          - datetime_iso

binary_sensor:
  - platform: rest
    resource: http://192.168.1.100:8080/watch/a1b2c3d4/status
    name: "HV71 Spelar nu"
    unique_id: hv71_is_live
    value_template: "{{ value_json.live.is_playing }}"
    scan_interval: 30
    device_class: running
```

**3.** Starta om HA eller ladda om konfigurationen (**Inställningar → System → Starta om**).

**4.** Verifiera i **Utvecklarverktyg → Tillstånd** att sensorerna dykt upp.

### Exempel på Lovelace-kort

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

## Home Assistant – Custom Integration (direkt)

Custom-integrationen kommunicerar direkt med swehockey.se – **inget separat API behövs**.

### Förutsättningar

- Home Assistant OS, Supervised eller Core
- HA 2024.1 eller senare
- Internetåtkomst från HA-instansen

### Installation via HACS (rekommenderat)

1. Öppna HACS → **Integrationer → … → Custom repositories**
2. Lägg till repo-URL:en, välj typ **Integration**
3. Sök efter "HockeyLive" och installera
4. Starta om Home Assistant

### Manuell installation

```bash
# Kör på servern/maskinen där HA är installerat
cd <ha-config-katalog>          # t.ex. /homeassistant eller ~/.homeassistant
cp -r /path/to/repo/custom_components/hockeylive custom_components/
```

Starta om HA.

### Konfiguration i UI

1. **Inställningar → Enheter & tjänster → Lägg till integration**
2. Sök på **HockeyLive**
3. **Steg 1** – Ange ett eller flera säsongs-ID:n (kommaseparerade):
   ```
   18263, 19791
   ```
4. **Steg 2** – Välj lag ur listan som hämtas automatiskt från swehockey.se
5. Klicka **Slutför**

Upprepa för att lägga till fler lag (varje lag är en egen config entry).

### Entiteter som skapas per lag

| Entitet | Typ | Exempelvärde |
|---|---|---|
| `sensor.<lag>_nasta_match` | Sensor | `2026-04-05T19:00:00+02:00` |
| `sensor.<lag>_senaste_resultat` | Sensor | `3–1` |
| `sensor.<lag>_live_score` | Sensor | `2–1` |
| `sensor.<lag>_period` | Sensor | `Period 2` |
| `binary_sensor.<lag>_spelar_nu` | Binary sensor | `on` / `off` |

Alla entiteter har utökade attribut (motståndare, arena, målgörare, utvisningar etc.).

### Pollintervall

| Läge | Intervall |
|---|---|
| Live game | 30 s |
| Speldag (ej startat) | 60 min |
| Ingen match idag | 6 h |

---

## Säsongsskifte

1. Hämta nya `season_ids` från `stats.swehockey.se`
2. Uppdatera `config.yaml`
3. Starta om containern: `docker compose restart`

API:et loggar automatiskt nya säsongs-ID:n det hittar på swehockey.se när alla kända matcher är avklarade. Befintliga bevakningar (`watchlist.json`) behåller sina ID:n och behöver inte uppdateras – lägg bara till nya säsonger med `POST /watch`.

---

## Licens

MIT – fri att använda för privat bruk. Data tillhör Svenska Ishockeyförbundet / swehockey.se.


## Funktioner

| Endpoint | Beskrivning |
|---|---|
| `GET /` | API-info och konfigurerat lag |
| `GET /next` | Nästa (eller pågående) match |
| `GET /last` | Senaste avklarade matchresultat |
| `GET /live` | Livescore + period under pågående match (404 om ingen aktiv match) |
| `GET /status` | Kombinerad snapshot – optimerad för Home Assistant |
| `GET /schedule` | Hela schemat för laget |
| `GET /teams` | Alla lagnamn i konfigurerade säsonger |
| `GET /refresh` | Tvinga cachad datahämtning |

---

## Snabbstart (lokalt, utan Docker)

```bash
cd hockeylive-api
pip install -r requirements.txt
# Kopiera och anpassa konfigurationen
cp config.yaml config.yaml   # eller redigera direkt
uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

API tillgängligt på `http://localhost:8080` – öppna `http://localhost:8080/docs` för interaktiv dokumentation.

---

## Konfiguration (`config.yaml`)

```yaml
team: "Färjestad BK"   # Exakt stavning; kontrollera med GET /teams

season_ids:
  - 18263              # SHL 2025/26 grundserie
  - 19791              # SHL 2025/26 SM-slutspel

port: 8080
```

### Hitta rätt säsong-ID

Säsong-ID:t syns i URL:en på `stats.swehockey.se`:

```
https://stats.swehockey.se/ScheduleAndResults/Schedule/18263
                                                            ^^^^^
                                                            Detta är season_id
```

Kända ID:n:

| Liga | Säsong | ID |
|---|---|---|
| SHL | 2025/26 grundserie | `18263` |
| SHL | 2025/26 SM-slutspel | `19791` |
| HockeyAllsvenskan | 2025/26 grundserie | `18266` |
| HockeyAllsvenskan | 2025/26 slutspel | `19979` |

> Uppdatera `season_ids` varje år med de nya ID:na från webbplatsen.

### Viktigt om lagnamn

Stavningen måste matcha exakt vad swehockey.se använder. Notera t.ex:
- `"Färjestad BK"` (inte "Färjestads BK")
- `"HV 71"` (med mellanslag, inte "HV71")
- `"IF Malmö Redhawks"` (inte "Malmö Redhawks")

Kontrollera exakt stavning med `GET /teams`:

```bash
curl http://localhost:8080/teams
```

---

## Docker (rekommenderat för Raspberry Pi och Home Assistant)

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
  --name hockeylive \
  hockeylive-api
```

### Raspberry Pi 3 (arm/v7)

Python-imaget och alla beroenden stödjer arm/v7. Bygg direkt på Pi:n:

```bash
git clone <repo> hockeylive-api
cd hockeylive-api
docker compose up -d --build
```

---

## Home Assistant-integration

### Alternativ A – API på extern server/Pi

Kopiera innehållet i `homeassistant/configuration_example.yaml` till din `configuration.yaml`.
Byt ut `IP_OR_HOSTNAME` mot IP-adressen till din Raspberry Pi (eller annan värd).

```yaml
rest:
  - resource: http://192.168.1.100:8080/status
    scan_interval: 60
    sensor:
      - name: "HV71 Live"
        value_template: "{{ value_json.live.is_playing }}"
        ...
```

### Alternativ B – Home Assistant Add-on (HA OS)

1. I HA: **Inställningar → Tillägg → … (tre punkter) → Repositories**
2. Lägg till mapp-URL:en till det här repot (eller klistra in lokalt)
3. Installera "HockeyLive API"
4. Konfigurera `team` och `season_ids` i Tillägg-konfigurationen

> Se `homeassistant/addon/` för add-on-specifika filer (under arbete om du vill ha fullt add-on-stöd).

### Förslag på HA-kort

```yaml
type: entities
title: HV71
entities:
  - entity: sensor.hv71_live
    name: Live
  - entity: sensor.hv71_live_score
    name: Ställning
  - entity: sensor.hv71_period
    name: Period
  - entity: sensor.hv71_next_match
    name: Nästa match
  - entity: sensor.hv71_next_match_days_away
    name: Om (dagar)
  - entity: sensor.hv71_last_match
    name: Senaste match
```

---

## Live-data: hur det fungerar

1. **Schema** (`/ScheduleAndResults/Schedule/{id}`) pollas var 5:e minut (var 30:e sekund under speldag).
2. Livedetektering: om en match har startat inom de senaste 4 timmarna och saknar avslutat periodresultat klassas den som *live*.
3. Under live-läge hämtas `stats.swehockey.se/Game/Events/{game_id}` för att extrahera exakt period och klocka.
4. Fallback: om händelsesidan saknar data uppskattas period heuristiskt baserat på tid sedan matchstart.

---

## Säsongsskifte

Vid ny säsong:
1. Hämta nya `season_ids` från `stats.swehockey.se`
2. Uppdatera `config.yaml`
3. Starta om containern: `docker compose restart`

---

## Licens

MIT – fri att använda för privat bruk. Data tillhör Svenska Ishockeyförbundet/swehockey.se.
