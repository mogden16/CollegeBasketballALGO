# Cloudflare Picks App

This Worker app hosts:

- **Date-based picks dashboard** (cached from `cloudflare-app/data/games-by-date.json`, with live ESPN fallback for missing dates).
- A **Quick Predict** form that calculates projected spread and total via `/api/quick-predict`.

## Run locally

```bash
cd cloudflare-app
npm install
npm run dev
```

## Deploy

```bash
cd cloudflare-app
npm run deploy
```

## Data regeneration

Rebuild date-indexed cached game data from `predictions_log.csv`:

```bash
python3 cloudflare-app/scripts/regenerate_games_by_date.py
```

## API endpoints

- `GET /api/picks?date=YYYY-MM-DD`
  - Returns `{ selectedDate, picks, source, reason? }`
  - `source` is `cache` or `live`
  - optional `reason` values: `no_games_scheduled`, `no_cached_data`, `upstream_unavailable`
- `GET /api/dates` – returns available cached dates.
- `GET /api/teams` – returns team options for Quick Predict.
- `POST /api/quick-predict` – accepts JSON:

```json
{
  "homeTeam": "Duke",
  "awayTeam": "North Carolina",
  "neutral": "false"
}
```
