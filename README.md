# HockeyLive – Swedish Hockey Live Scores for Home Assistant

Swedish hockey live scores in Home Assistant via a HACS custom integration backed by a FastAPI scraper.

Two components:
- **API backend** – Docker container (Proxmox or local), port 8080, scrapes `stats.swehockey.se`
- **HACS integration** – polls the API, creates HA sensors + binary sensors per team

Supports **multiple teams** (one integration entry per team). Includes a **demo team** for testing without a real game.

---

## Architecture

```
stats.swehockey.se
        │ scrape
        ▼
┌─────────────────────┐
│  hockeylive-api     │  Docker on Proxmox (or local)
│  FastAPI :8080      │
└─────────┬───────────┘
          │ HTTP poll /team/{team}/now
          ▼
┌─────────────────────┐
│  Home Assistant     │
│  HACS integration   │  one entry per team
│  sensors + binary   │
└─────────┬───────────┘
          │ state changes / automations
          ▼
   Awtrix display / dashboards / notifications
```

---

## 1. API Setup (Docker)

### Prerequisites

- Docker + Docker Compose

### Install

```bash
git clone https://github.com/Tiimber/ha-swehockey-api hockeylive-api
cd hockeylive-api
docker compose up -d
```

The `team` field in `config.yaml` is **optional** — the HACS integration manages teams via the UI. You only need `season_ids`.

```yaml
# config.yaml
season_ids:
  - 18263   # SHL 2025/26 regular season
  - 19791   # SHL 2025/26 playoffs

port: 8080
```

### Finding season IDs

Go to `https://stats.swehockey.se/ScheduleAndResults/Schedule` — the number at the end of the URL is the season ID.

| League | Season | ID |
|--------|--------|----|
| SHL | 2025/26 regular | `18263` |
| SHL | 2025/26 playoffs | `19791` |
| HockeyAllsvenskan | 2025/26 regular | `18266` |
| HockeyAllsvenskan | 2025/26 playoffs | `19979` |

> New IDs appear every season — check the URL on swehockey.se.

### Verify

```bash
curl http://localhost:8080/
```

---

## 2. Proxmox Deployment

1. Create an LXC container or VM with Docker installed
2. Clone the repo and start:
   ```bash
   git clone https://github.com/Tiimber/ha-swehockey-api hockeylive-api
   cd hockeylive-api
   docker compose up -d
   ```
3. Note the LAN IP (e.g. `192.168.68.50`) — you'll enter this in the HACS integration

---

## 3. HACS Integration Setup

### Add the custom repository

1. In HA: **HACS → ⋮ → Custom repositories**
2. URL: `https://github.com/Tiimber/ha-swehockey-api`
3. Category: **Integration**
4. Click **Add**

### Install

1. Search for **HockeyLive (swehockey.se)** in HACS → **Download**
2. Restart Home Assistant

### Add a team

**Settings → Devices & Services → Add Integration → HockeyLive**

**Step 1 – API connection:**
- API URL: `http://192.168.68.50:8080` (your Docker host IP)
- Season IDs: comma-separated, e.g. `18263,19791`

**Step 2 – Pick team:**
- Select your team from the dropdown (populated from the API)
- Click **Submit**

Repeat for each team you want to follow.

### Demo team

Enter any valid season IDs, then pick **"demo"** from the team dropdown. The demo team simulates a cycling live game — useful for testing dashboards and automations without waiting for a real match.

---

## 4. Entities Created per Team

The slug is the team name lowercased with spaces replaced by underscores, e.g. `Färjestad BK` → `farjestad_bk`, `HV 71` → `hv_71`.

| Entity | State | Key attributes |
|--------|-------|----------------|
| `sensor.{slug}_next_match` | ISO datetime or `–` | `opponent`, `venue`, `is_home`, `home_team`, `away_team`, `round` |
| `sensor.{slug}_last_result` | `"3–1"` or `–` | `home_team`, `away_team`, `home_score`, `away_score`, `score_for`, `score_against`, `won`, `overtime`, `shootout`, `round` |
| `sensor.{slug}_live_score` | `"2–1"` or `–` | `home_team`, `away_team`, `home_score`, `away_score`, `period`, `period_label`, `period_clock`, `is_overtime`, `is_shootout`, `venue`, `goals`, `last_goal`, `active_penalties` |
| `sensor.{slug}_period` | `"Period 2"` / `"Övertid"` / `"Straffar"` / `–` | — |
| `binary_sensor.{slug}_game_live` | `on` when live | — |

---

## 5. Awtrix Display

Copy [`homeassistant/awtrix_hockey.yaml`](homeassistant/awtrix_hockey.yaml) into your HA automations config (or import via the UI).

Replace the two placeholders at the top of the file:

```yaml
x-slug:  &slug  YOUR_TEAM_SLUG    # e.g. farjestad_bk
x-topic: &topic YOUR_AWTRIX_TOPIC # e.g. awtrix_ab12cd/custom/hockey
```

Then reload automations: **Settings → Automations & Scenes → ⋮ → Reload automations**

### What it does

| Automation | Trigger | Payload |
|------------|---------|---------|
| **Live scoreboard** | Score/period change or every 30 s (while live) | `"FBK 2-1 HV71 \| P2 14:32"` — green/red/yellow |
| **Final score** | Game goes from live → off | `"FT: FBK 3-1 HV71"` for 60 s — green (won) / red (lost) |

---

## 6. Development / Local Testing

```bash
# Start API locally
docker compose up -d

# Point the HACS integration at:
http://localhost:8080
# or from within a HA container:
http://host.docker.internal:8080
```

Use the **demo** team to test the live scoreboard without waiting for a real game — it cycles through periods and goals automatically.

---

## 7. API Endpoints Reference

| Endpoint | Description |
|----------|-------------|
| `GET /` | Health check + config info |
| `GET /team/{team}/now` | Full "right now" snapshot (used by HACS integration) |
| `GET /teams?season_ids=...` | List all teams in the given seasons |
| `POST /watch` | Subscribe a team (called automatically by the integration) |
| `DELETE /watch/{id}` | Unsubscribe (called on integration removal) |
| `GET /watches` | List all active subscriptions |
| `GET /search?q=...` | Search team names across all known seasons |

Full interactive docs: `http://<api-host>:8080/docs`

---

## License

MIT – free for personal use. Data belongs to Svenska Ishockeyförbundet / swehockey.se.
