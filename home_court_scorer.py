"""Home court advantage scorer for March Madness matchups."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from team_name_utils import normalize_team_name

from dotenv import load_dotenv
from geopy.distance import geodesic
from geopy.geocoders import Nominatim

try:
    import praw
except Exception:  # pragma: no cover - guarded runtime dependency
    praw = None

try:
    from pytrends.request import TrendReq
except Exception:  # pragma: no cover - guarded runtime dependency
    TrendReq = None

try:
    from googlesearch import search as google_search
except Exception:  # pragma: no cover - guarded runtime dependency
    google_search = None

try:
    import requests
except Exception:  # pragma: no cover - guarded runtime dependency
    requests = None

load_dotenv()

DATA_DIR = Path("data")
BRACKET_PATH = DATA_DIR / "bracket_locations.csv"
TEAM_MASTER_PATH = DATA_DIR / "team_master.csv"
HOME_COURT_LOG_PATH = Path("home_court_log.csv")
HOME_COURT_SCORER_LOG = Path("home_court_scorer.log")

# Required cap: do not allow home-court proximity-driven movement beyond +/- 2.0 points.
MAX_PROXIMITY_ADJUSTMENT = 2.0

REDDIT_KEYWORDS = ["road trip", "tickets", "going to", "traveling", "bus", "drive"]
TRANSPORT_KEYWORDS = ["official", "athletic", "university", "student government"]

TEAM_SUBREDDITS = {
    "Duke": "duke",
    "Kentucky": "uky",
    "Kansas": "KansasJayhawks",
    "North Carolina": "UNC",
    "UConn": "UConn",
    "Connecticut": "UConn",
    "Gonzaga": "zag",
    "Arizona": "UofArizona",
    "Baylor": "baylor",
    "Houston": "UniversityOfHouston",
    "Purdue": "Purdue",
    "Tennessee": "ockytop",
    "Alabama": "rolltide",
    "Auburn": "wde",
    "UCLA": "ucla",
    "Marquette": "MUBB",
    "Wisconsin": "wisconsin",
    "Illinois": "fightingillini",
    "Michigan State": "MSUSpartans",
    "Michigan": "MichiganWolverines",
    "Indiana": "HoosiersBasketball",
    "Ohio State": "OhioStateBasketball",
    "Texas": "LonghornNation",
    "Texas A&M": "aggies",
    "BYU": "byu",
    "Saint Mary's": "Gaels",
    "San Diego State": "AztecNation",
    "Creighton": "Creighton",
    "Iowa State": "cyclONEnation",
    "Florida": "FloridaGators",
    "Arkansas": "razorbacks",
    "Memphis": "memphis",
    "Villanova": "wildcats",
    "Virginia": "Virginia",
    "Miami": "miamihurricanes",
    "Xavier": "xavieruniversity",
    "Seton Hall": "SetonHall",
    "Syracuse": "SyracuseOrange",
    "Nebraska": "Huskers",
    "Oregon": "ducks",
    "USC": "USC",
}

logging.basicConfig(
    filename=HOME_COURT_SCORER_LOG,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class LayerResult:
    score: float
    ran: bool


def _warn(msg: str) -> None:
    print(f"[home_court_scorer] WARNING: {msg}")
    logger.warning(msg)


def _parse_date(game_date: str) -> datetime:
    return datetime.strptime(game_date, "%Y-%m-%d")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _confirm_write(prompt: str) -> bool:
    if not os.isatty(0):
        print(f"{prompt} [y/N]: non-interactive session detected, skipping write.")
        return False
    answer = input(f"{prompt} [y/N]: ").strip().lower()
    return answer in {"y", "yes"}


def _ensure_bracket_locations() -> bool:
    if BRACKET_PATH.exists():
        return True

    message = (
        f"{BRACKET_PATH} is missing. Populate with schema: "
        "game_id,round,game_date,arena_name,city,state,arena_lat,arena_lon. "
        "Optional fetch source: https://www.ncaa.com/news/basketball-men/article/2025-11-18/2026-ncaa-tournament-sites"
    )
    _warn(message)
    if _confirm_write("Attempt fetch + write bracket_locations.csv now?"):
        _warn("Automatic NCAA bracket scraping is not implemented in this environment; please add the CSV manually.")
    return False


def _ensure_team_master() -> bool:
    if TEAM_MASTER_PATH.exists():
        return True
    _warn(
        f"{TEAM_MASTER_PATH} is missing. Please create with schema: "
        "team_name,campus_city,campus_state,campus_lat,campus_lon,enrollment"
    )
    return False


def _lookup_game_location(game_id: str, game_date: str) -> dict[str, str] | None:
    if not _ensure_bracket_locations():
        return None
    rows = _read_csv(BRACKET_PATH)
    for row in rows:
        if row.get("game_id") == game_id:
            return row
    for row in rows:
        if row.get("game_date") == game_date:
            return row
    _warn(f"No game site found for game_id={game_id} or date={game_date}.")
    return None


def _get_team_row(team_name: str) -> dict[str, str] | None:
    if not _ensure_team_master():
        return None
    rows = _read_csv(TEAM_MASTER_PATH)
    team_key = normalize_team_name(team_name)
    for row in rows:
        if normalize_team_name(row.get("team_name", "")) == team_key:
            return row

    _warn(f"Team '{team_name}' missing from {TEAM_MASTER_PATH}.")
    try:
        geocoder = Nominatim(user_agent="home_court_scorer")
        loc = geocoder.geocode(f"{team_name} university campus", timeout=10)
    except Exception as exc:
        _warn(f"Geocoding failed for {team_name}: {exc}")
        return None

    if not loc:
        _warn(f"Geocoding returned no result for {team_name}.")
        return None

    if not _confirm_write(
        f"Append guessed campus for {team_name}: lat={loc.latitude:.4f}, lon={loc.longitude:.4f}?"
    ):
        return None

    with TEAM_MASTER_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([team_name, "UNKNOWN", "UNKNOWN", f"{loc.latitude:.6f}", f"{loc.longitude:.6f}", ""])
    return {
        "team_name": team_name,
        "campus_city": "UNKNOWN",
        "campus_state": "UNKNOWN",
        "campus_lat": str(loc.latitude),
        "campus_lon": str(loc.longitude),
        "enrollment": "",
    }


def _distance_miles(team_row: dict[str, str], site_row: dict[str, str]) -> float:
    team_coords = (float(team_row["campus_lat"]), float(team_row["campus_lon"]))
    site_coords = (float(site_row["arena_lat"]), float(site_row["arena_lon"]))
    km = geodesic(team_coords, site_coords).km
    return km * 0.621371


def _proximity_score(distance_mi: float) -> int:
    if distance_mi < 50:
        return 10
    if distance_mi < 150:
        return 8
    if distance_mi < 300:
        return 6
    if distance_mi < 500:
        return 4
    return 1


def _delta_to_spread(delta: int) -> float:
    if delta >= 6:
        adj = 1.5
    elif delta >= 3:
        adj = 0.75
    elif delta >= 1:
        adj = 0.25
    elif delta <= -6:
        adj = -1.5
    elif delta <= -3:
        adj = -0.75
    elif delta <= -1:
        adj = -0.25
    else:
        adj = 0.0
    return max(-MAX_PROXIMITY_ADJUSTMENT, min(MAX_PROXIMITY_ADJUSTMENT, adj))


def _init_reddit() -> Any | None:
    if praw is None:
        _warn("praw is not importable; skipping Reddit layer.")
        return None

    cid = os.getenv("REDDIT_CLIENT_ID")
    sec = os.getenv("REDDIT_CLIENT_SECRET")
    ua = os.getenv("REDDIT_USER_AGENT")
    if not all([cid, sec, ua]):
        _warn("Missing Reddit env vars; skipping Reddit layer.")
        return None

    try:
        return praw.Reddit(client_id=cid, client_secret=sec, user_agent=ua)
    except Exception as exc:
        _warn(f"Failed to initialize Reddit client: {exc}")
        return None


def _score_reddit_for_team(reddit: Any, team_name: str, game_date: str) -> LayerResult:
    if reddit is None:
        return LayerResult(0.0, False)

    end_dt = _parse_date(game_date)
    start_dt = end_dt - timedelta(days=7)
    query = f'"{team_name}" (' + " OR ".join(f'"{k}"' for k in REDDIT_KEYWORDS) + ")"

    posts = 0
    upvotes = 0
    try:
        subreddit = reddit.subreddit("CollegeBasketball")
        for submission in subreddit.search(query, sort="new", time_filter="month", limit=60):
            created = datetime.utcfromtimestamp(submission.created_utc)
            if start_dt <= created <= end_dt:
                posts += 1
                upvotes += int(getattr(submission, "score", 0) or 0)
        time.sleep(1)

        dedicated = TEAM_SUBREDDITS.get(team_name)
        if dedicated:
            for submission in reddit.subreddit(dedicated).hot(limit=15):
                if getattr(submission, "stickied", False):
                    title = (submission.title or "").lower()
                    if "travel" in title or "ticket" in title:
                        posts += 2
                        upvotes += int(getattr(submission, "score", 0) or 0)
                        break
            time.sleep(1)
    except Exception as exc:
        _warn(f"Reddit search failed for {team_name}: {exc}")
        return LayerResult(0.0, False)

    avg_upvotes = (upvotes / posts) if posts else 0
    if posts >= 10 and avg_upvotes >= 50:
        return LayerResult(0.5, True)
    if posts >= 5:
        return LayerResult(0.25, True)
    return LayerResult(0.0, True)


def _score_trends_for_team(team_name: str, game_date: str) -> LayerResult:
    if TrendReq is None:
        _warn("pytrends is not importable; skipping Trends layer.")
        return LayerResult(0.0, False)

    try:
        pytrends = TrendReq(hl="en-US", tz=360)
        end_dt = _parse_date(game_date)
        start_dt = end_dt - timedelta(days=7)
        timeframe = f"{start_dt.strftime('%Y-%m-%d')} {end_dt.strftime('%Y-%m-%d')}"
        kw = [f"{team_name} tickets", f"{team_name} March Madness"]
        pytrends.build_payload(kw, cat=0, timeframe=timeframe, geo="US", gprop="")
        data = pytrends.interest_over_time()
        time.sleep(5)
        if data is None or data.empty:
            return LayerResult(0.0, True)
        peak_interest = max(int(data[k].max()) for k in kw if k in data.columns)
    except Exception as exc:
        _warn(f"Google Trends failed for {team_name}: {exc}")
        return LayerResult(0.0, False)

    if peak_interest >= 75:
        return LayerResult(0.25, True)
    if peak_interest >= 40:
        return LayerResult(0.10, True)
    return LayerResult(0.0, True)


def _score_transport_for_team(team_name: str) -> LayerResult:
    query = f'"{team_name}" (bus OR charter OR transportation) "March Madness" site:*.edu'

    titles: list[str] = []
    serp_key = os.getenv("SERPAPI_KEY")
    try:
        if serp_key and requests is not None:
            resp = requests.get(
                "https://serpapi.com/search.json",
                params={"q": query, "api_key": serp_key, "num": 10},
                timeout=20,
            )
            resp.raise_for_status()
            payload = resp.json()
            for item in payload.get("organic_results", [])[:10]:
                titles.append((item.get("title") or "").lower())
        elif google_search is not None:
            for result in google_search(query, num_results=10):
                titles.append(str(result).lower())
        else:
            _warn("No transport search client available (googlesearch/requests).")
            return LayerResult(0.0, False)
    except Exception as exc:
        _warn(f"Transport search failed for {team_name}: {exc}")
        return LayerResult(0.0, False)

    found = any(any(k in t for k in TRANSPORT_KEYWORDS) for t in titles)
    return LayerResult(0.5 if found else 0.0, True)


def _ensure_output_log() -> None:
    if HOME_COURT_LOG_PATH.exists():
        return
    headers = [
        "game_id",
        "game_date",
        "round",
        "team_a",
        "team_b",
        "arena_name",
        "city",
        "state",
        "team_a_distance_mi",
        "team_b_distance_mi",
        "team_a_proximity_score",
        "team_b_proximity_score",
        "proximity_delta",
        "proximity_spread_adjustment",
        "team_a_reddit_boost",
        "team_b_reddit_boost",
        "team_a_trends_boost",
        "team_b_trends_boost",
        "team_a_transport_boost",
        "team_b_transport_boost",
        "net_home_court_adjustment",
        "favored_team",
        "adjustment_direction",
        "run_timestamp",
    ]
    with HOME_COURT_LOG_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)


def _append_output(row: list[Any]) -> None:
    _ensure_output_log()
    with HOME_COURT_LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def get_home_court_adjustment(team_a: str, team_b: str, game_date: str, game_id: str) -> dict:
    """
    Returns a dict with keys:
      - net_adjustment: float (positive favors team_a, negative favors team_b)
      - favored_team: str
      - confidence: str ("HIGH" | "MEDIUM" | "LOW") based on how many signal layers ran
      - detail: dict with per-layer scores
    Returns net_adjustment=0.0 and confidence="NONE" if scoring fails entirely.
    """
    try:
        site = _lookup_game_location(game_id, game_date)
        team_a_row = _get_team_row(team_a)
        team_b_row = _get_team_row(team_b)
        if not site or not team_a_row or not team_b_row:
            return {
                "net_adjustment": 0.0,
                "favored_team": "NEUTRAL",
                "confidence": "NONE",
                "detail": {"reason": "missing location/team data"},
            }

        team_a_dist = _distance_miles(team_a_row, site)
        team_b_dist = _distance_miles(team_b_row, site)
        team_a_prox = _proximity_score(team_a_dist)
        team_b_prox = _proximity_score(team_b_dist)
        prox_delta = team_a_prox - team_b_prox
        prox_adj = _delta_to_spread(prox_delta)
        proximity_ran = True

        reddit_client = _init_reddit()
        team_a_reddit = _score_reddit_for_team(reddit_client, team_a, game_date)
        team_b_reddit = _score_reddit_for_team(reddit_client, team_b, game_date)

        team_a_trends = _score_trends_for_team(team_a, game_date)
        team_b_trends = _score_trends_for_team(team_b, game_date)

        team_a_transport = _score_transport_for_team(team_a)
        team_b_transport = _score_transport_for_team(team_b)

        net_adjustment = (
            prox_adj
            + team_a_reddit.score - team_b_reddit.score
            + team_a_trends.score - team_b_trends.score
            + team_a_transport.score - team_b_transport.score
        )
        net_adjustment = max(-MAX_PROXIMITY_ADJUSTMENT, min(MAX_PROXIMITY_ADJUSTMENT, net_adjustment))

        direction = "NEUTRAL"
        favored_team = "NEUTRAL"
        if net_adjustment > 0:
            direction = "TEAM_A"
            favored_team = team_a
        elif net_adjustment < 0:
            direction = "TEAM_B"
            favored_team = team_b

        signal_layers_ran = [
            team_a_reddit.ran and team_b_reddit.ran,
            team_a_trends.ran and team_b_trends.ran,
            team_a_transport.ran and team_b_transport.ran,
        ]
        ran_count = 1 + sum(1 for x in signal_layers_ran if x)
        if not proximity_ran:
            confidence = "NONE"
        elif ran_count == 4:
            confidence = "HIGH"
        elif ran_count >= 2:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        detail = {
            "proximity": {
                "team_a_distance_mi": round(team_a_dist, 2),
                "team_b_distance_mi": round(team_b_dist, 2),
                "team_a_score": team_a_prox,
                "team_b_score": team_b_prox,
                "delta": prox_delta,
                "spread_adjustment": prox_adj,
            },
            "reddit": {"team_a": team_a_reddit.score, "team_b": team_b_reddit.score, "ran": signal_layers_ran[0]},
            "trends": {"team_a": team_a_trends.score, "team_b": team_b_trends.score, "ran": signal_layers_ran[1]},
            "transport": {
                "team_a": team_a_transport.score,
                "team_b": team_b_transport.score,
                "ran": signal_layers_ran[2],
            },
        }

        _append_output(
            [
                game_id,
                game_date,
                site.get("round", ""),
                team_a,
                team_b,
                site.get("arena_name", ""),
                site.get("city", ""),
                site.get("state", ""),
                f"{team_a_dist:.2f}",
                f"{team_b_dist:.2f}",
                team_a_prox,
                team_b_prox,
                prox_delta,
                prox_adj,
                team_a_reddit.score,
                team_b_reddit.score,
                team_a_trends.score,
                team_b_trends.score,
                team_a_transport.score,
                team_b_transport.score,
                net_adjustment,
                favored_team,
                direction,
                datetime.now(timezone.utc).isoformat(),
            ]
        )

        return {
            "net_adjustment": round(net_adjustment, 3),
            "favored_team": favored_team,
            "confidence": confidence,
            "detail": detail,
        }
    except Exception as exc:
        _warn(f"Uncaught scoring error for {team_a} vs {team_b}: {exc}")
        return {
            "net_adjustment": 0.0,
            "favored_team": "NEUTRAL",
            "confidence": "NONE",
            "detail": {"reason": str(exc)},
        }


def _print_summary(result: dict, team_a: str, team_b: str) -> None:
    detail = result.get("detail", {})
    print("\n=== Home Court Scorer Summary ===")
    print(f"Matchup: {team_a} vs {team_b}")
    print(f"Net Adjustment: {result.get('net_adjustment', 0.0):+.2f}")
    print(f"Favored Team: {result.get('favored_team')}")
    print(f"Confidence: {result.get('confidence')}")
    if isinstance(detail, dict):
        prox = detail.get("proximity", {})
        print(
            f"Proximity: {prox.get('team_a_score')} - {prox.get('team_b_score')} "
            f"(spread {prox.get('spread_adjustment', 0.0):+})"
        )
        for layer in ("reddit", "trends", "transport"):
            layer_d = detail.get(layer, {})
            print(
                f"{layer.title()}: team_a={layer_d.get('team_a', 0):+} "
                f"team_b={layer_d.get('team_b', 0):+} ran={layer_d.get('ran', False)}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="March Madness home-court scorer")
    parser.add_argument("--team_a", required=True)
    parser.add_argument("--team_b", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--game_id", required=True)
    args = parser.parse_args()

    result = get_home_court_adjustment(args.team_a, args.team_b, args.date, args.game_id)
    _print_summary(result, args.team_a, args.team_b)


if __name__ == "__main__":
    main()


# --- HOME COURT INTEGRATION (paste into kenpom_predictor.py run_slate()) ---
# try:
#     from home_court_scorer import get_home_court_adjustment
#     hc = get_home_court_adjustment(home_team, away_team, game_date, game_id)
#     hc_adj = hc["net_adjustment"]  # positive = home team benefits
#     kp_spread_adjusted = kp_spread + hc_adj
#     bt_spread_adjusted = bt_spread + hc_adj
#     # Log hc["confidence"] and hc["detail"] to predictions_log if desired
# except Exception as e:
#     hc_adj = 0.0
#     print(f"[home_court_scorer] skipped: {e}")
# --- END HOME COURT INTEGRATION ---
