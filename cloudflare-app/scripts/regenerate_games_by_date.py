#!/usr/bin/env python3
import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
PREDICTIONS = ROOT / "predictions_log.csv"
OUTPUT = ROOT / "cloudflare-app" / "data" / "games-by-date.json"
ODDS_API_BASE = "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds"
PREFERRED_BOOKMAKERS = [
    "fanduel",
    "bovada",
    "draftkings",
    "betmgm",
    "caesars",
    "espnbet",
    "bet365",
]
TEAM_ALIASES = {
    "nc state": "north carolina state",
    "n c state": "north carolina state",
    "st johns": "saint johns",
    "st john": "saint johns",
    "saint johns": "saint johns",
    "saint john": "saint johns",
    "usc": "southern california",
    "usc trojans": "southern california",
    "u conn": "connecticut",
    "uconn": "connecticut",
    "miami fl": "miami",
    "ole miss": "mississippi",
}
SAINT_SCHOOL_SUFFIXES = ("bonaventure", "francis", "johns", "josephs", "louis", "marys", "peters", "thomas")


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
    normalized = (
        str(value or "")
        .strip()
        .lower()
        .replace("’", "")
        .replace("'", "")
        .replace(".", "")
        .replace("&", " ")
    )
    normalized = " ".join(normalized.split())
    normalized = normalized.replace(" university", "")
    normalized = normalized.replace("the ", "")
    normalized = normalized.replace(" st ", " state ")
    normalized = " ".join(normalized.split())
    if normalized.startswith("state ") and any(normalized.startswith(f"state {suffix}") for suffix in SAINT_SCHOOL_SUFFIXES):
        normalized = "saint" + normalized[len("state") :]
    return TEAM_ALIASES.get(normalized, normalized)


def game_key(home_team: str, away_team: str) -> str:
    return f"{normalize_team_name(home_team)}|{normalize_team_name(away_team)}"


def team_keys_match(key_a: str, key_b: str) -> bool:
    if key_a == key_b:
        return True
    shorter, longer = (key_a, key_b) if len(key_a) <= len(key_b) else (key_b, key_a)
    return longer.startswith(shorter) and len(longer) > len(shorter) and longer[len(shorter)] == " "


def line_unavailable() -> dict:
    return {"spread": None, "total": None, "source": None, "status": "unavailable"}


def to_eastern_iso_date(iso_dt: str) -> str | None:
    try:
        dt = datetime.fromisoformat(iso_dt.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()


def fetch_odds_for_date(selected_date: str, api_key: str | None) -> list[dict]:
    if not api_key:
        return []

    start = f"{selected_date}T00:00:00Z"
    end_dt = datetime.fromisoformat(f"{selected_date}T00:00:00+00:00") + timedelta(days=2, hours=6)
    end = end_dt.isoformat().replace("+00:00", "Z")
    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "spreads,totals",
        "bookmakers": ",".join(PREFERRED_BOOKMAKERS),
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


def extract_total(market: dict | None):
    if not market:
        return None
    outcomes = market.get("outcomes") or []
    for outcome in outcomes:
        if normalize_team_name(str(outcome.get("name") or "")) == "over":
            point = outcome.get("point")
            return float(point) if isinstance(point, (int, float)) else None
    for outcome in outcomes:
        point = outcome.get("point")
        if isinstance(point, (int, float)):
            return float(point)
    return None


def extract_spread(event: dict, market: dict | None):
    if not market:
        return None
    home_key = normalize_team_name(str(event.get("home_team") or ""))
    for outcome in market.get("outcomes") or []:
        if normalize_team_name(str(outcome.get("name") or "")) == home_key:
            point = outcome.get("point")
            if isinstance(point, (int, float)):
                # App convention: spread is the home team's line.
                return float(point)
    return None


def extract_preferred_vegas_line(event: dict) -> dict:
    bookmakers = event.get("bookmakers") or []

    for preferred in PREFERRED_BOOKMAKERS:
        bookmaker = next((candidate for candidate in bookmakers if str(candidate.get("key") or "").strip().lower() == preferred), None)
        if not bookmaker:
            print(f"[Vegas] Book {preferred} missing for {event.get('away_team')} @ {event.get('home_team')}")
            continue

        markets = bookmaker.get("markets") or []
        spread_market = next((market for market in markets if market.get("key") == "spreads"), None)
        total_market = next((market for market in markets if market.get("key") == "totals"), None)

        spread = extract_spread(event, spread_market)
        total = extract_total(total_market)

        if spread is None and total is None:
            print(f"[Vegas] Book {preferred} had no usable markets for {event.get('away_team')} @ {event.get('home_team')}")
            continue

        status = "ok" if spread is not None and total is not None else "partial"
        print(
            f"[Vegas] Selected {preferred} for {event.get('away_team')} @ {event.get('home_team')} "
            f"(spread={spread if spread is not None else 'N/A'}, total={total if total is not None else 'N/A'}, status={status})"
        )
        return {"spread": spread, "total": total, "source": preferred, "status": status}

    return line_unavailable()


def match_event_to_game(selected_date: str, game: dict, event: dict) -> bool:
    if to_eastern_iso_date(str(event.get("commence_time") or "")) != selected_date:
        return False
    game_home = normalize_team_name(game["homeTeam"])
    game_away = normalize_team_name(game["awayTeam"])
    event_home = normalize_team_name(str(event.get("home_team") or ""))
    event_away = normalize_team_name(str(event.get("away_team") or ""))
    home_match = team_keys_match(game_home, event_home)
    away_match = team_keys_match(game_away, event_away)
    print(f"[Vegas] Compare game '{game_away} @ {game_home}' vs odds '{event_away} @ {event_home}' => home={home_match} away={away_match}")
    return home_match and away_match


def build_odds_lookup(selected_date: str, games: list[dict], api_key: str | None) -> dict[str, dict]:
    events = fetch_odds_for_date(selected_date, api_key)
    date_events = [event for event in events if to_eastern_iso_date(str(event.get("commence_time") or "")) == selected_date]
    events_by_key: dict[str, dict] = {}
    for event in date_events:
        key = game_key(str(event.get("home_team") or ""), str(event.get("away_team") or ""))
        events_by_key.setdefault(key, event)

    lookup: dict[str, dict] = {}
    for game in games:
        key = game_key(game["homeTeam"], game["awayTeam"])
        event = events_by_key.get(key)
        if not event:
            event = next((candidate for candidate in date_events if match_event_to_game(selected_date, game, candidate)), None)
            if event:
                odds_key = game_key(str(event.get("home_team") or ""), str(event.get("away_team") or ""))
                print(f"[Vegas] Matched '{key}' -> '{odds_key}'")
            else:
                print(f"[Vegas] Unmatched game '{key}' for {selected_date}")
        lookup[key] = extract_preferred_vegas_line(event) if event else line_unavailable()
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
            "vegas": line_unavailable(),
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
            game["vegas"] = lookup.get(key, line_unavailable())

    payload = {"dates": dict(sorted(by_date.items()))}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {sum(len(v) for v in payload['dates'].values())} games across {len(payload['dates'])} dates to {OUTPUT}")


if __name__ == "__main__":
    main()
