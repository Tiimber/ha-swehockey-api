# HockeyLive API – Documentation

## Configuration options

| Option | Type | Description |
|---|---|---|
| `team` | string | Exact team name as it appears on stats.swehockey.se (see below) |
| `season_ids` | list of int | One or more season IDs to follow (see below) |

---

## How to find season IDs

Season IDs are in the URL on [stats.swehockey.se](https://stats.swehockey.se):

```
https://stats.swehockey.se/ScheduleAndResults/Schedule/18263
                                                            ^^^^^
                                                            season_id
```

Common IDs:

| League | Season | ID |
|---|---|---|
| SHL | 2025/26 regular season | `18263` |
| SHL | 2025/26 playoffs | `19791` |
| HockeyAllsvenskan | 2025/26 regular season | `18266` |
| HockeyAllsvenskan | 2025/26 playoffs | `19979` |
| Champions Hockey League | 2025/26 | `18289` |

## How to find teams and season IDs using the search API

Once the add-on is running, open a browser or use the SSH terminal:

**Search for a team name (returns matching leagues + season IDs):**
```
http://<ha-ip>:8080/search?q=HV
```

Example response:
```json
[
  {"season_id": 18263, "season_name": "SHL 2025/26", "team": "HV 71"},
  {"season_id": 19791, "season_name": "SHL 2025/26 Slutspel", "team": "HV 71"}
]
```

Use the `season_id` values and exact `team` spelling from this response in your configuration.

**Interactive API docs (all endpoints):**
```
http://<ha-ip>:8080/docs
```

---

## Exact team name

The `team` field must match exactly what stats.swehockey.se uses. Use `/search` to confirm spelling. Common gotchas:

- `"HV 71"` — not `"HV71"`
- `"Färjestad BK"` — not `"Färjestads BK"`
- `"IF Malmö Redhawks"` — not `"Malmö Redhawks"`

---

## Multiple leagues

To follow a team across SHL + playoffs + CHL, list all season IDs:

```yaml
team: "Frölunda"
season_ids:
  - 18263   # SHL regular season
  - 19791   # SHL playoffs
  - 18289   # Champions Hockey League
```

---

## REST sensors in Home Assistant

After starting the add-on, register your team as a watch entry to get a stable ID:

```bash
# In SSH terminal:
curl -s -X POST http://localhost:8080/watch \
  -H "Content-Type: application/json" \
  -d '{"team": "HV 71", "season_ids": [18263, 19791]}' | python3 -m json.tool
```

Use the returned `id` in your `configuration.yaml` REST sensors:

```yaml
rest:
  - resource: http://localhost:8080/watch/<id>/status
    scan_interval: 30
    sensor:
      - name: "HV71 Live Score"
        value_template: >-
          {% if value_json.live.is_playing %}
            {{ value_json.live.home_score }}–{{ value_json.live.away_score }}
          {% else %}–{% endif %}
```

See the full sensor YAML in the [README](https://github.com/Tiimber/ha-swehockey-api#steg-5--konfigurera-rest-sensorer-i-ha).
