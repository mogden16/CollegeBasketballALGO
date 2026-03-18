# Ratings Refresh Automation

Use `scripts/refresh_ratings.py` to refresh the raw ratings files consumed by `kenpom_predictor.py`.

## What it updates

- `kenpom_raw.txt`
- `barttorvik_raw.txt`

## Requirements

Install Python dependencies first:

```bash
pip install -r requirements.txt
```

## KenPom authentication

KenPom typically requires a paid account plus an authenticated session.
The refresh script supports two authentication methods:

1. `KENPOM_COOKIE` (recommended): paste your browser cookie header for an active KenPom session.
2. `KENPOM_USERNAME` and `KENPOM_PASSWORD`: the script will attempt a form login.

Example:

```bash
export KENPOM_COOKIE='PHPSESSID=...; rememberme=...'
python3 scripts/refresh_ratings.py
```

## BartTorvik refresh

BartTorvik is fetched from `https://barttorvik.com/trank.php?year=<season-year>`.
The season year defaults to the current UTC year, and can be overridden:

```bash
python3 scripts/refresh_ratings.py --year 2026
```

## Cron example

Run every day at 6:00 AM UTC:

```cron
0 6 * * * cd /workspace/CollegeBasketballALGO && /usr/bin/env python3 scripts/refresh_ratings.py >> refresh_ratings.log 2>&1
```

If you only want one source:

```bash
python3 scripts/refresh_ratings.py --skip-kenpom
python3 scripts/refresh_ratings.py --skip-barttorvik
```
