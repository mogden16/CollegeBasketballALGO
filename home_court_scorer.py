"""
home_court_scorer.py — March Madness Home Court Advantage Scorer

Estimates crowd-based home court advantage for March Madness games by scoring
each team's proximity to the game site, flagging travel support signals from
Reddit and Google Trends, and outputting an adjusted spread delta.

Usage (standalone):
    python home_court_scorer.py --team_a "Duke" --team_b "Kentucky" --date 2026-03-20 --game_id TEST_001

Integration (import into kenpom_predictor.py):
    from home_court_scorer import get_home_court_adjustment
    result = get_home_court_adjustment("Duke", "Kentucky", "2026-03-20", "MM2026_R1_G01")
"""

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Absolute cap on spread adjustment from home-court signals.
# This limit is NON-NEGOTIABLE — no combination of inputs can exceed +/- 2.0.
MAX_PROXIMITY_ADJUSTMENT = 2.0

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
BRACKET_FILE = DATA_DIR / "bracket_locations.csv"
TEAM_FILE = DATA_DIR / "team_master.csv"
LOG_FILE = SCRIPT_DIR / "home_court_log.csv"
SCORER_LOG = SCRIPT_DIR / "home_court_scorer.log"

KM_TO_MILES = 0.621371

# Proximity scoring rubric: (max_miles, score)
PROXIMITY_RUBRIC = [
    (50, 10),
    (150, 8),
    (300, 6),
    (500, 4),
]
PROXIMITY_DEFAULT_SCORE = 1  # > 500 miles

# Proximity delta -> spread adjustment mapping
DELTA_ADJUSTMENTS = [
    (6, 1.5),   # delta >= 6  -> +1.5 pts
    (3, 0.75),  # delta 3-5   -> +0.75 pts
    (1, 0.25),  # delta 1-2   -> +0.25 pts
]

# Known team subreddits for top 40 programs
TEAM_SUBREDDITS = {
    "Duke": "duke",
    "Kentucky": "uky",
    "Kansas": "KansasJayhawks",
    "North Carolina": "tarheels",
    "Gonzaga": "gonzaga",
    "Houston": "UniversityOfHouston",
    "Alabama": "rolltide",
    "Purdue": "Purdue",
    "Tennessee": "ockytop",
    "Arizona": "UofArizona",
    "Auburn": "wde",
    "Iowa State": "iastate",
    "Marquette": "Marquette",
    "Baylor": "baylor",
    "Connecticut": "UConn",
    "Creighton": "creighton",
    "St. John's": "StJohns",
    "Michigan State": "msu",
    "UCLA": "ucla",
    "Wisconsin": "UWMadison",
    "Florida": "FloridaGators",
    "Texas Tech": "TexasTech",
    "Clemson": "clemson",
    "Illinois": "UIUC",
    "Oregon": "UofO",
    "Texas A&M": "aggies",
    "Memphis": "memphis",
    "BYU": "byu",
    "Mississippi State": "msstate",
    "Missouri": "Mizzou",
    "Louisville": "AllHail",
    "Pittsburgh": "Pitt",
    "Maryland": "UMD",
    "Michigan": "uofm",
    "Villanova": "villanova",
    "Virginia": "UVA",
    "Ohio State": "OhioStateUniversity",
    "Indiana": "IndianaUniversity",
    "Syracuse": "SyracuseU",
    "Arkansas": "razorbacks",
}

# Reddit search keywords for travel signals
TRAVEL_KEYWORDS = ["road trip", "tickets", "going to", "traveling", "bus", "drive"]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger = logging.getLogger("home_court_scorer")
logger.setLevel(logging.DEBUG)

# File handler — all skipped layers and warnings
_fh = logging.FileHandler(SCORER_LOG, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)

# Console handler — warnings and above
_ch = logging.StreamHandler()
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter("[home_court_scorer] %(message)s"))
logger.addHandler(_ch)


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_bracket_locations():
    """Load bracket_locations.csv into a list of dicts."""
    if not BRACKET_FILE.exists():
        msg = (
            f"bracket_locations.csv not found at {BRACKET_FILE}.\n"
            "Please create it with columns: game_id,round,game_date,arena_name,"
            "city,state,arena_lat,arena_lon\n"
            "Or run this module to attempt fetching from NCAA.com."
        )
        logger.error(msg)
        print(msg)
        return []
    with open(BRACKET_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row for row in reader]


def _load_team_master():
    """Load team_master.csv into a dict keyed by team_name."""
    if not TEAM_FILE.exists():
        msg = (
            f"team_master.csv not found at {TEAM_FILE}.\n"
            "Please create it with columns: team_name,campus_city,campus_state,"
            "campus_lat,campus_lon,enrollment\n"
            "Seed with the 68 tournament teams once the bracket is set."
        )
        logger.error(msg)
        print(msg)
        return {}
    teams = {}
    with open(TEAM_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            teams[row["team_name"].strip()] = row
    return teams


def _find_game(game_id, locations):
    """Find a game row in bracket_locations by game_id."""
    for row in locations:
        if row["game_id"].strip() == game_id.strip():
            return row
    return None


def _geocode_team(team_name):
    """Attempt to geocode a team campus using geopy Nominatim."""
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="home_court_scorer")
        location = geolocator.geocode(f"{team_name} university campus")
        if location:
            logger.info(f"Geocoded {team_name}: {location.latitude}, {location.longitude}")
            return location.latitude, location.longitude
    except Exception as e:
        logger.warning(f"Geocoding failed for {team_name}: {e}")
    return None, None


# ---------------------------------------------------------------------------
# Step 1: Distance Scoring
# ---------------------------------------------------------------------------

def compute_distance_miles(lat1, lon1, lat2, lon2):
    """Compute geodesic distance in miles between two lat/lon points."""
    try:
        from geopy.distance import geodesic
        dist_km = geodesic((lat1, lon1), (lat2, lon2)).km
        return dist_km * KM_TO_MILES
    except Exception as e:
        logger.warning(f"Distance calculation failed: {e}")
        return None


def proximity_score(distance_miles):
    """Convert distance in miles to a proximity score (1-10)."""
    if distance_miles is None:
        return PROXIMITY_DEFAULT_SCORE
    for max_miles, score in PROXIMITY_RUBRIC:
        if distance_miles < max_miles:
            return score
    return PROXIMITY_DEFAULT_SCORE


def proximity_spread_adjustment(delta):
    """Convert proximity delta to a spread adjustment in points."""
    abs_delta = abs(delta)
    adjustment = 0.0
    for threshold, adj in DELTA_ADJUSTMENTS:
        if abs_delta >= threshold:
            adjustment = adj
            break
    # Preserve sign: positive delta means team_a is closer
    if delta < 0:
        adjustment = -adjustment
    return adjustment


# ---------------------------------------------------------------------------
# Step 2: Reddit Signal (PRAW)
# ---------------------------------------------------------------------------

def _reddit_signal(team_name, game_date_str):
    """
    Search r/CollegeBasketball for travel-related posts about a team
    in the 7 days before game_date. Returns reddit_boost (float).
    """
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT")

    if not all([client_id, client_secret, user_agent]):
        logger.warning("Reddit credentials missing (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, "
                        "REDDIT_USER_AGENT). Skipping Reddit signal.")
        return None

    try:
        import praw

        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
        )

        game_date = datetime.strptime(game_date_str, "%Y-%m-%d")
        after_ts = int((game_date - timedelta(days=7)).timestamp())

        subreddit = reddit.subreddit("CollegeBasketball")
        total_posts = 0
        total_upvotes = 0

        for keyword in TRAVEL_KEYWORDS:
            query = f"{team_name} {keyword}"
            try:
                results = subreddit.search(query, sort="new", time_filter="week", limit=25)
                for post in results:
                    if post.created_utc >= after_ts:
                        total_posts += 1
                        total_upvotes += post.score
            except Exception as e:
                logger.warning(f"Reddit search error for query '{query}': {e}")
            time.sleep(1)  # Rate limit between PRAW requests

        # Check team-specific subreddit for pinned travel/ticket threads
        team_sub = TEAM_SUBREDDITS.get(team_name)
        if team_sub:
            try:
                sub = reddit.subreddit(team_sub)
                for post in sub.hot(limit=5):
                    if post.stickied:
                        title_lower = post.title.lower()
                        if any(kw in title_lower for kw in ["travel", "ticket", "road trip",
                                                             "march madness", "tournament"]):
                            total_posts += 3  # Weighted boost for pinned threads
                            total_upvotes += post.score
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Error checking team subreddit r/{team_sub}: {e}")

        # Score
        avg_upvotes = total_upvotes / total_posts if total_posts > 0 else 0
        if total_posts >= 10 and avg_upvotes >= 50:
            return 0.5
        elif total_posts >= 5:
            return 0.25
        else:
            return 0.0

    except Exception as e:
        logger.warning(f"Reddit signal failed for {team_name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 3: Google Trends Signal
# ---------------------------------------------------------------------------

def _trends_signal(team_name, game_date_str):
    """
    Pull Google Trends interest for "[Team] tickets" and "[Team] March Madness"
    in the 7 days before game_date. Returns trends_boost (float).
    """
    try:
        from pytrends.request import TrendReq

        game_date = datetime.strptime(game_date_str, "%Y-%m-%d")
        start_date = game_date - timedelta(days=7)
        timeframe = f"{start_date.strftime('%Y-%m-%d')} {game_date.strftime('%Y-%m-%d')}"

        pytrends = TrendReq(hl="en-US", tz=360)
        keywords = [f"{team_name} tickets", f"{team_name} March Madness"]

        peak_interest = 0
        for kw in keywords:
            try:
                pytrends.build_payload([kw], cat=0, timeframe=timeframe, geo="US")
                df = pytrends.interest_over_time()
                if not df.empty and kw in df.columns:
                    peak_interest = max(peak_interest, df[kw].max())
            except Exception as e:
                logger.warning(f"Trends query failed for '{kw}': {e}")
            time.sleep(5)  # Rate limit between Trends requests to avoid 429s

        if peak_interest >= 75:
            return 0.25
        elif peak_interest >= 40:
            return 0.10
        else:
            return 0.0

    except Exception as e:
        logger.warning(f"Google Trends signal failed for {team_name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 4: University Transportation Signal
# ---------------------------------------------------------------------------

def _transport_signal(team_name):
    """
    Search for university-organized transportation to March Madness.
    Returns transport_boost (float).
    """
    query = f'"{team_name}" "bus" OR "charter" OR "transportation" "March Madness" site:*.edu'

    # Try SerpAPI if key is available
    serpapi_key = os.environ.get("SERPAPI_KEY")
    if serpapi_key:
        return _transport_via_serpapi(query, serpapi_key)

    # Fall back to googlesearch-python
    return _transport_via_googlesearch(query)


def _transport_via_serpapi(query, api_key):
    """Use SerpAPI for the transport search."""
    try:
        import requests as req
        params = {
            "q": query,
            "api_key": api_key,
            "engine": "google",
            "num": 5,
        }
        resp = req.get("https://serpapi.com/search", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("organic_results", [])
        for r in results:
            title = r.get("title", "").lower()
            if any(kw in title for kw in ["official", "athletic", "university",
                                           "student government"]):
                return 0.5
        return 0.0
    except Exception as e:
        logger.warning(f"SerpAPI transport search failed: {e}")
        return None


def _transport_via_googlesearch(query):
    """Use googlesearch-python for the transport search."""
    try:
        from googlesearch import search as gsearch
        results = list(gsearch(query, num_results=5))
        # googlesearch-python returns URLs; check for .edu domains with keywords
        for url in results:
            url_lower = url.lower()
            if ".edu" in url_lower and any(kw in url_lower for kw in
                                            ["official", "athletic", "university",
                                             "student"]):
                return 0.5
        return 0.0
    except Exception as e:
        logger.warning(f"Google search transport signal failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 5: Final Score Assembly & Output
# ---------------------------------------------------------------------------

def _compute_confidence(layers_ran):
    """
    Determine confidence based on how many signal layers produced scores.
    layers_ran: dict with keys 'proximity', 'reddit', 'trends', 'transport'
                and bool values indicating if they produced a score.
    """
    prox = layers_ran.get("proximity", False)
    reddit = layers_ran.get("reddit", False)
    trends = layers_ran.get("trends", False)
    transport = layers_ran.get("transport", False)

    if not prox:
        return "NONE"
    signal_count = sum([reddit, trends, transport])
    if signal_count == 3:
        return "HIGH"
    elif signal_count >= 1:
        return "MEDIUM"
    else:
        return "LOW"


def _append_log(row_dict):
    """Append a row to home_court_log.csv, creating the file if needed."""
    fieldnames = [
        "game_id", "game_date", "round", "team_a", "team_b",
        "arena_name", "city", "state",
        "team_a_distance_mi", "team_b_distance_mi",
        "team_a_proximity_score", "team_b_proximity_score",
        "proximity_delta", "proximity_spread_adjustment",
        "team_a_reddit_boost", "team_b_reddit_boost",
        "team_a_trends_boost", "team_b_trends_boost",
        "team_a_transport_boost", "team_b_transport_boost",
        "net_home_court_adjustment", "favored_team", "adjustment_direction",
        "run_timestamp",
    ]
    write_header = not LOG_FILE.exists()
    try:
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            writer.writerow(row_dict)
    except Exception as e:
        logger.warning(f"Failed to write to log: {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_home_court_adjustment(team_a: str, team_b: str, game_date: str,
                               game_id: str) -> dict:
    """
    Returns a dict with keys:
      - net_adjustment: float (positive favors team_a, negative favors team_b)
      - favored_team: str
      - confidence: str ("HIGH" | "MEDIUM" | "LOW") based on how many signal layers ran
      - detail: dict with per-layer scores
    Returns net_adjustment=0.0 and confidence="NONE" if scoring fails entirely.
    """
    fail_result = {
        "net_adjustment": 0.0,
        "favored_team": "NEUTRAL",
        "confidence": "NONE",
        "detail": {},
    }

    layers_ran = {"proximity": False, "reddit": False, "trends": False, "transport": False}

    # --- Load data ---
    locations = _load_bracket_locations()
    teams = _load_team_master()

    game = _find_game(game_id, locations) if locations else None

    team_a_data = teams.get(team_a)
    team_b_data = teams.get(team_b)

    # --- Step 1: Distance scoring ---
    team_a_dist = None
    team_b_dist = None
    team_a_prox = PROXIMITY_DEFAULT_SCORE
    team_b_prox = PROXIMITY_DEFAULT_SCORE

    if game and team_a_data and team_b_data:
        try:
            arena_lat = float(game["arena_lat"])
            arena_lon = float(game["arena_lon"])
            a_lat = float(team_a_data["campus_lat"])
            a_lon = float(team_a_data["campus_lon"])
            b_lat = float(team_b_data["campus_lat"])
            b_lon = float(team_b_data["campus_lon"])

            team_a_dist = compute_distance_miles(a_lat, a_lon, arena_lat, arena_lon)
            team_b_dist = compute_distance_miles(b_lat, b_lon, arena_lat, arena_lon)
            team_a_prox = proximity_score(team_a_dist)
            team_b_prox = proximity_score(team_b_dist)
            layers_ran["proximity"] = True
        except Exception as e:
            logger.warning(f"Distance scoring failed: {e}")
    elif not game:
        logger.warning(f"Game {game_id} not found in bracket_locations.csv. "
                        "Using default proximity scores.")
        # Still allow other layers to run
        if team_a_data and team_b_data:
            layers_ran["proximity"] = True  # defaults, but layer "ran"
    else:
        missing = []
        if not team_a_data:
            missing.append(team_a)
        if not team_b_data:
            missing.append(team_b)
        logger.warning(f"Team(s) not found in team_master.csv: {missing}. "
                        "Add them to data/team_master.csv.")

    prox_delta = team_a_prox - team_b_prox
    prox_adj = proximity_spread_adjustment(prox_delta)

    if not layers_ran["proximity"]:
        return fail_result

    # --- Step 2: Reddit signal ---
    a_reddit = _reddit_signal(team_a, game_date)
    b_reddit = _reddit_signal(team_b, game_date)

    a_reddit_boost = a_reddit if a_reddit is not None else 0.0
    b_reddit_boost = b_reddit if b_reddit is not None else 0.0
    if a_reddit is not None or b_reddit is not None:
        layers_ran["reddit"] = True

    # --- Step 3: Google Trends signal ---
    a_trends = _trends_signal(team_a, game_date)
    b_trends = _trends_signal(team_b, game_date)

    a_trends_boost = a_trends if a_trends is not None else 0.0
    b_trends_boost = b_trends if b_trends is not None else 0.0
    if a_trends is not None or b_trends is not None:
        layers_ran["trends"] = True

    # --- Step 4: University transport signal ---
    a_transport = _transport_signal(team_a)
    b_transport = _transport_signal(team_b)

    a_transport_boost = a_transport if a_transport is not None else 0.0
    b_transport_boost = b_transport if b_transport is not None else 0.0
    if a_transport is not None or b_transport is not None:
        layers_ran["transport"] = True

    # --- Step 5: Final assembly ---
    # Net boosts: positive values for team_a signals, negative for team_b
    net_reddit = a_reddit_boost - b_reddit_boost
    net_trends = a_trends_boost - b_trends_boost
    net_transport = a_transport_boost - b_transport_boost

    total_adjustment = prox_adj + net_reddit + net_trends + net_transport

    # Enforce the non-negotiable cap of +/- MAX_PROXIMITY_ADJUSTMENT (2.0 pts)
    total_adjustment = max(-MAX_PROXIMITY_ADJUSTMENT,
                           min(MAX_PROXIMITY_ADJUSTMENT, total_adjustment))

    # Determine favored team
    if total_adjustment > 0:
        favored = team_a
        direction = "TEAM_A"
    elif total_adjustment < 0:
        favored = team_b
        direction = "TEAM_B"
    else:
        favored = "NEUTRAL"
        direction = "NEUTRAL"

    confidence = _compute_confidence(layers_ran)

    detail = {
        "team_a_distance_mi": round(team_a_dist, 1) if team_a_dist else None,
        "team_b_distance_mi": round(team_b_dist, 1) if team_b_dist else None,
        "team_a_proximity_score": team_a_prox,
        "team_b_proximity_score": team_b_prox,
        "proximity_delta": prox_delta,
        "proximity_spread_adjustment": prox_adj,
        "team_a_reddit_boost": a_reddit_boost,
        "team_b_reddit_boost": b_reddit_boost,
        "team_a_trends_boost": a_trends_boost,
        "team_b_trends_boost": b_trends_boost,
        "team_a_transport_boost": a_transport_boost,
        "team_b_transport_boost": b_transport_boost,
        "layers_ran": layers_ran,
    }

    # --- Log to CSV ---
    arena_name = game["arena_name"] if game else "UNKNOWN"
    city = game["city"] if game else "UNKNOWN"
    state = game["state"] if game else "UNKNOWN"
    game_round = game["round"] if game else "UNKNOWN"

    _append_log({
        "game_id": game_id,
        "game_date": game_date,
        "round": game_round,
        "team_a": team_a,
        "team_b": team_b,
        "arena_name": arena_name,
        "city": city,
        "state": state,
        "team_a_distance_mi": detail["team_a_distance_mi"],
        "team_b_distance_mi": detail["team_b_distance_mi"],
        "team_a_proximity_score": team_a_prox,
        "team_b_proximity_score": team_b_prox,
        "proximity_delta": prox_delta,
        "proximity_spread_adjustment": prox_adj,
        "team_a_reddit_boost": a_reddit_boost,
        "team_b_reddit_boost": b_reddit_boost,
        "team_a_trends_boost": a_trends_boost,
        "team_b_trends_boost": b_trends_boost,
        "team_a_transport_boost": a_transport_boost,
        "team_b_transport_boost": b_transport_boost,
        "net_home_court_adjustment": total_adjustment,
        "favored_team": favored,
        "adjustment_direction": direction,
        "run_timestamp": datetime.now().isoformat(),
    })

    return {
        "net_adjustment": round(total_adjustment, 2),
        "favored_team": favored,
        "confidence": confidence,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _print_summary(result, team_a, team_b):
    """Print a formatted summary of the home court analysis."""
    d = result["detail"]
    print("\n" + "=" * 60)
    print("  MARCH MADNESS HOME COURT ADVANTAGE REPORT")
    print("=" * 60)
    print(f"  {team_a}  vs  {team_b}")
    print("-" * 60)

    # Proximity
    a_dist = d.get("team_a_distance_mi")
    b_dist = d.get("team_b_distance_mi")
    print(f"\n  PROXIMITY TO ARENA:")
    print(f"    {team_a:20s}  {a_dist or '???':>8} mi  (score: {d['team_a_proximity_score']})")
    print(f"    {team_b:20s}  {b_dist or '???':>8} mi  (score: {d['team_b_proximity_score']})")
    print(f"    Delta: {d['proximity_delta']:+d}  ->  Spread adj: {d['proximity_spread_adjustment']:+.2f} pts")

    # Reddit
    print(f"\n  REDDIT SIGNAL:")
    print(f"    {team_a:20s}  boost: {d['team_a_reddit_boost']:+.2f}")
    print(f"    {team_b:20s}  boost: {d['team_b_reddit_boost']:+.2f}")

    # Trends
    print(f"\n  GOOGLE TRENDS SIGNAL:")
    print(f"    {team_a:20s}  boost: {d['team_a_trends_boost']:+.2f}")
    print(f"    {team_b:20s}  boost: {d['team_b_trends_boost']:+.2f}")

    # Transport
    print(f"\n  UNIVERSITY TRANSPORT SIGNAL:")
    print(f"    {team_a:20s}  boost: {d['team_a_transport_boost']:+.2f}")
    print(f"    {team_b:20s}  boost: {d['team_b_transport_boost']:+.2f}")

    # Final
    print("\n" + "-" * 60)
    print(f"  NET ADJUSTMENT:  {result['net_adjustment']:+.2f} pts")
    print(f"  FAVORED TEAM:    {result['favored_team']}")
    print(f"  CONFIDENCE:      {result['confidence']}")

    layers = d.get("layers_ran", {})
    ran = [k for k, v in layers.items() if v]
    skipped = [k for k, v in layers.items() if not v]
    print(f"  LAYERS RAN:      {', '.join(ran) if ran else 'none'}")
    if skipped:
        print(f"  LAYERS SKIPPED:  {', '.join(skipped)}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="March Madness Home Court Advantage Scorer"
    )
    parser.add_argument("--team_a", required=True, help="Team A name")
    parser.add_argument("--team_b", required=True, help="Team B name")
    parser.add_argument("--date", required=True, help="Game date (YYYY-MM-DD)")
    parser.add_argument("--game_id", required=True, help="Game ID (e.g. MM2026_R1_G01)")
    args = parser.parse_args()

    # Load dotenv if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    print(f"Scoring home court advantage: {args.team_a} vs {args.team_b}")
    print(f"Game: {args.game_id} on {args.date}")

    result = get_home_court_adjustment(args.team_a, args.team_b, args.date, args.game_id)
    _print_summary(result, args.team_a, args.team_b)


if __name__ == "__main__":
    main()


# --- HOME COURT INTEGRATION ---
# This module is automatically imported and called by kenpom_predictor.py
# for neutral-site games. The integration is live in run_slate().
# If the module or its dependencies are missing, it degrades gracefully.
#
# Manual usage example:
#   from home_court_scorer import get_home_court_adjustment
#   hc = get_home_court_adjustment("Duke", "Kentucky", "2026-03-20", "MM2026_R1_G01")
#   hc_adj = hc["net_adjustment"]  # positive = team_a benefits
# --- END HOME COURT INTEGRATION ---
