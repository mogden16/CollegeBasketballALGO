# Cloudflare Picks App

This Worker app hosts:

- **Date-based picks dashboard** (cached from `cloudflare-app/data/games-by-date.json`, with live ESPN schedule fallback for missing dates).
- A **Quick Predict** form that calculates projected spread and total via `/api/quick-predict`.
- **Vegas lines sourced from FanDuel via The Odds API** (`spreads` + `totals`, pregame only).

## Environment variables

- `ODDS_API_KEY` (required for FanDuel vegas hydration in live responses and cache regeneration)

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

## Safe pre-deploy checks

Run this before deploying to catch bundle/parsing/API regressions:

```bash
cd cloudflare-app
npm run check:deploy
```

If it passes, deploy in one command:

```bash
npm run deploy:safe
```

Optional env for date smoke check:

```bash
DATE_CHECK=2026-03-17 npm run check:deploy
```

## Data regeneration

Rebuild date-indexed cached game data from `predictions_log.csv` and enrich vegas lines from FanDuel:

```bash
ODDS_API_KEY=your_key_here python3 cloudflare-app/scripts/regenerate_games_by_date.py
```

Notes:
- The regeneration script fetches date-level FanDuel odds from The Odds API (`basketball_ncaab`, `bookmakers=fanduel`, `markets=spreads,totals`).
- If an odds match cannot be found, vegas fields stay nullable and are labeled unavailable.

## API endpoints

- `GET /api/picks?date=YYYY-MM-DD`
  - Returns `{ selectedDate, picks, source, reason? }`
  - `source` is `cache` or `live`
  - optional `reason` values: `no_games_scheduled`, `no_cached_data`, `upstream_unavailable`
  - cached picks are returned first; if cached games are missing vegas spread/total, the Worker hydrates missing values from FanDuel before returning.
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
