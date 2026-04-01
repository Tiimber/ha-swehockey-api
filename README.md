# HockeyLive API

Mini-API som hämtar schema och liveresultat för ett valfritt lag (standard: **HV71**) från `stats.swehockey.se`.

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
