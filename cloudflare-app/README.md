# Cloudflare Picks App

This Worker app hosts:

- **Picks of the day** (from `cloudflare-app/data/picks-of-day.json`).
- A **Quick Predict** form that calculates a projected score, spread, and total via `/api/quick-predict`.

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

## API endpoints

- `GET /api/picks` – returns the picks payload.
- `POST /api/quick-predict` – accepts JSON:

```json
{
  "homeOff": 114.5,
  "homeDef": 98.2,
  "awayOff": 109.7,
  "awayDef": 101.3,
  "pace": 69
}
```
