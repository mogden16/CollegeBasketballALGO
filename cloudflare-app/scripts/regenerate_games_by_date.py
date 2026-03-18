#!/usr/bin/env python3
import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDICTIONS = ROOT / "predictions_log.csv"
OUTPUT = ROOT / "cloudflare-app" / "data" / "games-by-date.json"
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"

TEAM_ALIASES = {
    "nc state": "north carolina state",
    "n c state": "north carolina state",
    "st johns": "saint johns",
    "st john": "saint johns",
    "usc": "southern california",
    "u conn": "connecticut",
    "uconn": "connecticut",
    "ole miss": "mississippi",
}


def to_float(value: str):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_bool(value: str):
    return str(value or "").strip().lower() == "true"


def projection(row, prefix):
    return {
        "homeScore": to_float(row.get(f"{prefix}_home_score")),
        "awayScore": to_float(row.get(f"{prefix}_away_score")),
        "total": to_float(row.get(f"{prefix}_total")),
        "spread": to_float(row.get(f"{prefix}_spread")),
        "spreadEdge": to_float(row.get(f"{prefix}_spread_edge")),
        "totalEdge": to_float(row.get(f"{prefix}_total_edge")),
    }


def normalize_team_name(value: str) -> str:
    cleaned = (
        str(value or "")
        .strip()
        .lower()
        .replace("’", "")
        .replace("'", "")
        .replace(".", "")
        .replace("&", " ")
    )
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.replace(" st ", " saint ")
    return TEAM_ALIASES.get(cleaned, cleaned)


def game_key(home_team: str, away_team: str) -> str:
    return f"{normalize_team_name(home_team)}|{normalize_team_name(away_team)}"


def to_eastern_iso_date(iso_dt: str) -> str | None:
    try:
        dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def fetch_fanduel_events_for_date(selected_date: str, api_key: str | None) -> list[dict]:
    if not api_key:
        return []

    start = f"{selected_date}T00:00:00Z"
    end_dt = datetime.fromisoformat(f"{selected_date}T00:00:00+00:00") + timedelta(days=2, hours=6)
    end = end_dt.isoformat().replace("+00:00", "Z")
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads,totals",
        "bookmakers": "fanduel",
        "oddsFormat": "american",
        "dateFormat": "iso",
        "commenceTimeFrom": start,
        "commenceTimeTo": end,
    }
    url = f"{ODDS_API_BASE}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []
    except Exception as exc:
        print(f"WARN: failed to fetch Odds API for {selected_date}: {exc}")
        return []


def extract_fanduel_line(event: dict) -> dict:
    bookmakers = event.get("bookmakers") or []
    fanduel = next((b for b in bookmakers if normalize_team_name(str(b.get("key") or b.get("title") or "")) == "fanduel"), None)
    if not fanduel:
        return {"spread": None, "total": None, "vegasSource": "fanduel", "vegasStatus": "unavailable"}

    markets = fanduel.get("markets") or []
    spread_market = next((m for m in markets if m.get("key") == "spreads"), None)
    total_market = next((m for m in markets if m.get("key") == "totals"), None)

    home_key = normalize_team_name(str(event.get("home_team") or ""))
    spread = None
    for outcome in (spread_market or {}).get("outcomes") or []:
        if normalize_team_name(str(outcome.get("name") or "")) == home_key:
            point = outcome.get("point")
            spread = float(point) if isinstance(point, (int, float)) else None
            break

    total = None
    for outcome in (total_market or {}).get("outcomes") or []:
        if normalize_team_name(str(outcome.get("name") or "")) == "over":
            point = outcome.get("point")
            total = float(point) if isinstance(point, (int, float)) else None
            break
    if total is None:
        for outcome in (total_market or {}).get("outcomes") or []:
            point = outcome.get("point")
            if isinstance(point, (int, float)):
                total = float(point)
                break

    return {
        "spread": spread,
        "total": total,
        "vegasSource": "fanduel",
        "vegasStatus": "available" if spread is not None and total is not None else "unavailable",
    }


def build_odds_lookup(selected_date: str, games: list[dict], api_key: str | None) -> dict[str, dict]:
    events = fetch_fanduel_events_for_date(selected_date, api_key)
    events_by_key: dict[str, dict] = {}
    for event in events:
        event_date = to_eastern_iso_date(str(event.get("commence_time") or ""))
        if event_date != selected_date:
            continue
        key = game_key(str(event.get("home_team") or ""), str(event.get("away_team") or ""))
        events_by_key.setdefault(key, event)

    lookup: dict[str, dict] = {}
    for game in games:
        key = game_key(game["homeTeam"], game["awayTeam"])
        event = events_by_key.get(key)
        if not event:
            lookup[key] = {"spread": None, "total": None, "vegasSource": "fanduel", "vegasStatus": "unavailable"}
            continue
        lookup[key] = extract_fanduel_line(event)
    return lookup


def main():
    rows = list(csv.DictReader(PREDICTIONS.open()))
    by_date: dict[str, list[dict]] = {}

    for row in rows:
        game = {
            "homeTeam": row["home_team"],
            "awayTeam": row["away_team"],
            "neutral": to_bool(row.get("neutral")),
            "kenpom": projection(row, "kp"),
            "trank": projection(row, "bt"),
            "vegas": {
                "spread": None,
                "total": None,
                "vegasSource": "fanduel",
                "vegasStatus": "unavailable",
            },
            "isEdge": to_bool(row.get("is_edge")),
            "confidence": row.get("confidence") or None,
        }
        by_date.setdefault(row["date"], []).append(game)

    odds_api_key = os.getenv("ODDS_API_KEY")
    if not odds_api_key:
        print("WARN: ODDS_API_KEY is not set; vegas lines will remain unavailable in cache output.")

    for date, games in by_date.items():
        lookup = build_odds_lookup(date, games, odds_api_key)
        for game in games:
            key = game_key(game["homeTeam"], game["awayTeam"])
            game["vegas"] = lookup.get(key, {"spread": None, "total": None, "vegasSource": "fanduel", "vegasStatus": "unavailable"})

    payload = {"dates": dict(sorted(by_date.items()))}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {sum(len(v) for v in payload['dates'].values())} games across {len(payload['dates'])} dates to {OUTPUT}")


if __name__ == "__main__":
    main()
