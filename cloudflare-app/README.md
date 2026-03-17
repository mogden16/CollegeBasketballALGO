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


## Environment variables

Set secrets with Wrangler (do **not** hardcode):

```bash
cd cloudflare-app
npx wrangler secret put ODDS_API_KEY
```

- `ODDS_API_KEY`: The Odds API key used to fetch **FanDuel-only** pregame spreads/totals for `basketball_ncaab`.

## Dashboard data contract

Each game returned by `GET /api/picks` now includes fields used by the compact dashboard:

- `selectedDate`
- `gameTimeUtc`
- `gameTimeEtDisplay`
- `awayTeam`, `homeTeam`
- `awayLogo`, `homeLogo`
- `projectedSpread`, `projectedTotal`
- `fanduelSpread`, `fanduelTotal`
- `edge`, `edgeSummary`
- `neutralSite`, `travelDistanceMiles`

Missing values are rendered gracefully (e.g. `Time TBD`, `FanDuel line unavailable`).
