# Cloudflare Picks App

This Worker app hosts:

- **Date-based picks dashboard** (cached from `cloudflare-app/data/games-by-date.json`, with live ESPN schedule fallback for missing dates).
- A **Quick Predict** form that calculates projected spread and total via `/api/quick-predict`.
- **Vegas lines sourced from The Odds API using prioritized sportsbooks** (`spreads` + `totals`, pregame only, with partial-line fallback).

## Environment variables

- `ODDS_API_KEY` (required for The Odds API vegas hydration in live responses and cache regeneration)
- `REDDIT_CLIENT_ID` (required for server-side Reddit OAuth client credentials)
- `REDDIT_CLIENT_SECRET` (required for server-side Reddit OAuth client credentials)
- `REDDIT_USER_AGENT` (recommended custom Reddit API user agent string for this app)

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

Rebuild date-indexed cached game data from `predictions_log.csv` and enrich vegas lines from prioritized sportsbooks:

```bash
ODDS_API_KEY=your_key_here python3 cloudflare-app/scripts/regenerate_games_by_date.py
```

Notes:
- The regeneration script fetches date-level odds from The Odds API (`basketball_ncaab`, `markets=spreads,totals`) and checks bookmakers in this order: FanDuel, Bovada, DraftKings, BetMGM, Caesars, ESPN BET, bet365.
- The first sportsbook with a usable spread and/or total is used; cache entries store `vegas.source` and `vegas.status` (`ok`, `partial`, or `unavailable`).
- If only one market exists, the other market remains `null` and the UI renders `Spread N/A` or `Total N/A` as needed.
- If no preferred sportsbook has a usable line, vegas fields remain unavailable and the UI renders `Vegas: N/A`.

## API endpoints

- `GET /api/picks?date=YYYY-MM-DD`
  - Returns `{ selectedDate, picks, source, reason? }`
  - `source` is `cache` or `live`
  - optional `reason` values: `no_games_scheduled`, `no_cached_data`, `upstream_unavailable`
  - cached picks are returned first; if cached games are missing or unavailable for vegas data, the Worker rehydrates that game from the prioritized sportsbook pipeline before returning.
- `GET /api/dates` – returns available cached dates.
- `GET /api/teams` – returns team options for Quick Predict.
- `POST /api/reddit-sentiment` – accepts JSON `{ "teamA": "Duke", "teamB": "North Carolina" }` and returns a normalized Reddit sentiment summary.
- `POST /api/quick-predict` – accepts JSON:

```json
{
  "homeTeam": "Duke",
  "awayTeam": "North Carolina",
  "neutral": "false"
}
```
