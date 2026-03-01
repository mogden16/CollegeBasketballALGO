"""
KenPom NCAA Basketball Predictor
---------------------------------
Workflow:
  1. Paste KenPom table into kenpom_raw.txt (weekly refresh)
  2. Run this script
  3. Script pulls today's NCAAB matchups from ESPN
  4. Script pulls live lines from The Odds API
  5. Fuzzy matches teams, runs model, flags edges
  6. Logs all predictions to predictions_log.csv
  7. Run: python kenpom_predictor.py --results  to enter actual scores
  8. Run: python kenpom_predictor.py --report   to see model accuracy

Dependencies:
  pip install requests thefuzz python-Levenshtein
"""

import re
import csv
import sys
import math
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from thefuzz import process
from pathlib import Path

# ══════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════
ODDS_API_KEY    = "YOUR_API_KEY_HERE"   # Free at the-odds-api.com
ODDS_BOOK       = "draftkings"          # draftkings, fanduel, betmgm, etc.
EDGE_THRESHOLD  = 3.0                   # Flag if model vs line differs >= this
FUZZY_THRESHOLD = 75                    # Min fuzzy match score (0-100)

# Log files
PREDICTIONS_LOG = "predictions_log.csv"
RESULTS_LOG     = "results_log.csv"

# Model constants
AVG_EFFICIENCY     = 100.0
AVG_TEMPO_2026     = 68.4
LAMBDA             = 0.88
TEMPO_EXP          = 0.48
TEMPO_LEAGUE_EXP   = 0.04
HCA                = 3.2

# ══════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════
@dataclass
class Team:
    name: str
    adj_o: float
    adj_d: float
    adj_t: float

@dataclass
class Matchup:
    home: str
    away: str
    neutral: bool = False
    vegas_spread: float = None   # Negative = home favored
    vegas_total: float = None

@dataclass
class Prediction:
    home_team: str
    away_team: str
    home_score: float
    away_score: float
    total: float
    model_spread: float          # Negative = home favored
    vegas_spread: float = None
    vegas_total: float = None
    spread_edge: float = None    # model - vegas (positive = home better than market thinks)
    total_edge: float = None

# ══════════════════════════════════════════════════════
# STEP 1: PARSE KENPOM PASTE
# ══════════════════════════════════════════════════════
def parse_kenpom(filepath: str) -> dict[str, Team]:
    """
    Parse raw KenPom copy-paste.

    Expected column order (tab-separated):
    Rk | Team | Conf | W-L | NetRtg | ORtg | ORtg_rank | DRtg | DRtg_rank | AdjT | ...

    Paste the KenPom table directly into kenpom_raw.txt, no editing needed.
    """
    teams = {}
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            # Skip header rows
            if not parts[0].strip().isdigit():
                continue
            if len(parts) < 10:
                continue
            try:
                name  = parts[1].strip()
                adj_o = float(parts[5].strip())
                adj_d = float(parts[7].strip())
                adj_t = float(parts[9].strip())
                teams[name] = Team(name=name, adj_o=adj_o, adj_d=adj_d, adj_t=adj_t)
            except (ValueError, IndexError):
                continue
    return teams

def fuzzy_lookup(query: str, team_dict: dict[str, Team], threshold: int = FUZZY_THRESHOLD) -> Team | None:
    """Fuzzy match a team name string to the KenPom dataset."""
    keys = list(team_dict.keys())
    match, score = process.extractOne(query, keys)
    if score >= threshold:
        return team_dict[match]
    return None

# ══════════════════════════════════════════════════════
# STEP 2: PULL TODAY'S MATCHUPS FROM ESPN
# ══════════════════════════════════════════════════════
def get_todays_matchups() -> list[Matchup]:
    """
    Pull today's NCAAB schedule from ESPN's unofficial API.
    No API key required.
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={today}&limit=100"
    
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR: Could not fetch ESPN schedule -- {e}")
        return []

    matchups = []
    for event in data.get("events", []):
        competitors = event.get("competitions", [{}])[0].get("competitors", [])
        if len(competitors) < 2:
            continue

        # ESPN returns home/away via homeAway flag
        home = away = None
        for c in competitors:
            team_name = c.get("team", {}).get("displayName", "")
            if c.get("homeAway") == "home":
                home = team_name
            else:
                away = team_name

        neutral_venue = event.get("competitions", [{}])[0].get("neutralSite", False)

        if home and away:
            matchups.append(Matchup(home=home, away=away, neutral=neutral_venue))

    return matchups

# ══════════════════════════════════════════════════════
# STEP 3: PULL LINES FROM THE ODDS API
# ══════════════════════════════════════════════════════
def get_odds(matchups: list[Matchup]) -> list[Matchup]:
    """
    Fetch live NCAAB lines from The Odds API (free tier).
    Attaches vegas_spread and vegas_total to each matchup via fuzzy team match.
    Free tier: 500 requests/month. This uses 1 request.
    """
    if ODDS_API_KEY == "YOUR_API_KEY_HERE":
        print("WARNING: No Odds API key set. Skipping lines -- set ODDS_API_KEY in config.")
        return matchups

    url = (
        "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=us&markets=spreads,totals&bookmakers={ODDS_BOOK}"
    )

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        odds_data = resp.json()
    except Exception as e:
        print(f"ERROR: Could not fetch odds -- {e}")
        return matchups

    # Build odds lookup: (home_team, away_team) -> {spread, total}
    odds_lookup = {}
    for game in odds_data:
        h = game.get("home_team", "")
        a = game.get("away_team", "")
        spread = total = None
        for bookie in game.get("bookmakers", []):
            for market in bookie.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == h:
                            spread = outcome["point"]
                if market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total = outcome["point"]
        odds_lookup[(h, a)] = {"spread": spread, "total": total}

    # Fuzzy match matchups to odds
    odds_teams = [f"{h} vs {a}" for h, a in odds_lookup.keys()]
    for m in matchups:
        query = f"{m.home} vs {m.away}"
        match, score = process.extractOne(query, odds_teams) if odds_teams else ("", 0)
        if score >= FUZZY_THRESHOLD:
            parts = match.split(" vs ")
            key = (parts[0], parts[1])
            m.vegas_spread = odds_lookup[key]["spread"]
            m.vegas_total  = odds_lookup[key]["total"]

    return matchups

# ══════════════════════════════════════════════════════
# STEP 4: CORE PREDICTION MODEL
# ══════════════════════════════════════════════════════
def predict_game(home: Team, away: Team, neutral: bool = False) -> dict:
    hca = 0.0 if neutral else HCA
    tempo    = (home.adj_t * away.adj_t) ** TEMPO_EXP * (AVG_TEMPO_2026 ** TEMPO_LEAGUE_EXP)
    eff_home = home.adj_o + LAMBDA * (away.adj_d - AVG_EFFICIENCY)
    eff_away = away.adj_o + LAMBDA * (home.adj_d - AVG_EFFICIENCY)
    pts_home = tempo * eff_home / 100
    pts_away = tempo * eff_away / 100
    total    = pts_home + pts_away
    spread   = -((pts_home - pts_away) + hca)  # Negative = home favored
    return {
        "home_score": round(pts_home, 1),
        "away_score": round(pts_away, 1),
        "total":      round(total, 1),
        "spread":     round(spread, 1),
        "tempo":      round(tempo, 1),
    }

# ══════════════════════════════════════════════════════
# STEP 5: RUN FULL SLATE + FLAG EDGES
# ══════════════════════════════════════════════════════
def run_slate(kenpom_file: str = "kenpom_raw.txt"):
    print(f"\n{'═'*65}")
    print(f"  KenPom NCAA Predictor  |  {datetime.now().strftime('%A %b %d, %Y')}")
    print(f"{'═'*65}")

    # Load KenPom
    teams = parse_kenpom(kenpom_file)
    print(f"  Loaded {len(teams)} teams from KenPom data.\n")

    # Today's games
    matchups = get_todays_matchups()
    if not matchups:
        print("  No games found for today.")
        return

    # Attach lines
    matchups = get_odds(matchups)

    edges = []
    no_data = []

    for m in matchups:
        home_team = fuzzy_lookup(m.home, teams)
        away_team = fuzzy_lookup(m.away, teams)

        if not home_team or not away_team:
            no_data.append(f"  NO KENPOM DATA: {m.away} @ {m.home}")
            continue

        result = predict_game(home_team, away_team, neutral=m.neutral)

        spread_edge = None
        total_edge  = None
        if m.vegas_spread is not None:
            spread_edge = round(m.vegas_spread - result["spread"], 1)
        if m.vegas_total is not None:
            total_edge = round(result["total"] - m.vegas_total, 1)

        # Determine favorites
        model_fav  = home_team.name if result["spread"] < 0 else away_team.name
        model_line = abs(result["spread"])
        vegas_fav  = home_team.name if (m.vegas_spread or 0) < 0 else away_team.name
        vegas_line = abs(m.vegas_spread) if m.vegas_spread is not None else None

        # Flag edge games
        is_spread_edge = spread_edge is not None and abs(spread_edge) >= EDGE_THRESHOLD
        is_total_edge  = total_edge  is not None and abs(total_edge)  >= EDGE_THRESHOLD

        entry = {
            "home": home_team.name, "away": away_team.name,
            "neutral": m.neutral,
            "result": result,
            "model_fav": model_fav, "model_line": model_line,
            "vegas_fav": vegas_fav, "vegas_line": vegas_line,
            "vegas_spread": m.vegas_spread, "vegas_total": m.vegas_total,
            "spread_edge": spread_edge, "total_edge": total_edge,
            "is_edge": is_spread_edge or is_total_edge,
            "is_spread_edge": is_spread_edge,
            "is_total_edge": is_total_edge,
        }

        edges.append(entry)

    # ── Print all games ──
    print(f"  TODAY'S GAMES ({len(edges)} with KenPom data)\n")
    print(f"  {'Matchup':<35} {'Model':>10} {'Vegas':>10} {'Edge':>8}")
    print(f"  {'─'*35} {'─'*10} {'─'*10} {'─'*8}")

    for e in edges:
        venue = " [N]" if e["neutral"] else ""
        matchup = f"{e['away']} @ {e['home']}{venue}"
        model_str = f"{e['model_fav']} -{e['model_line']:.1f}"
        vegas_str = f"{e['vegas_fav']} -{e['vegas_line']:.1f}" if e["vegas_line"] else "N/A"
        edge_str  = f"{e['spread_edge']:+.1f}" if e["spread_edge"] is not None else "N/A"
        flag      = " ◄◄◄" if e["is_edge"] else ""
        print(f"  {matchup:<35} {model_str:>10} {vegas_str:>10} {edge_str:>8}{flag}")

    # ── Print edge games ──
    edge_games = [e for e in edges if e["is_edge"]]
    if edge_games:
        print(f"\n{'═'*65}")
        print(f"  EDGE GAMES (model vs line >= {EDGE_THRESHOLD} pts)")
        print(f"{'═'*65}")
        for e in edge_games:
            r = e["result"]
            print(f"\n  {e['away']} @ {e['home']}")
            print(f"  Model : {e['home']} {r['home_score']}  |  {e['away']} {r['away_score']}")
            print(f"          Total {r['total']}  |  Spread {e['model_fav']} -{e['model_line']:.1f}")
            if e["vegas_spread"] is not None:
                print(f"  Vegas : {e['vegas_fav']} -{e['vegas_line']:.1f}  |  Total {e['vegas_total']}")
            if e["is_spread_edge"]:
                direction = "home" if e["spread_edge"] > 0 else "away"
                print(f"  SPREAD EDGE: model likes {direction} team by {abs(e['spread_edge']):.1f} pts vs market")
            if e["is_total_edge"]:
                direction = "OVER" if e["total_edge"] > 0 else "UNDER"
                print(f"  TOTAL EDGE : model says {direction} by {abs(e['total_edge']):.1f} pts")

    if no_data:
        print(f"\n  SKIPPED (not in KenPom top 25 -- lower ranked teams):")
        for nd in no_data:
            print(nd)

    # Log all predictions
    if edges:
        log_predictions(edges)

    print(f"\n{'═'*65}\n")

# ══════════════════════════════════════════════════════
# STEP 6: LOG PREDICTIONS TO CSV
# ══════════════════════════════════════════════════════
PREDICTIONS_HEADERS = [
    "date", "home_team", "away_team", "neutral",
    "model_home_score", "model_away_score", "model_total", "model_spread",
    "vegas_spread", "vegas_total", "spread_edge", "total_edge", "is_edge"
]

def log_predictions(entries: list[dict]):
    """Append today's predictions to predictions_log.csv."""
    log_path = Path(PREDICTIONS_LOG)
    write_header = not log_path.exists()

    today = datetime.now().strftime("%Y-%m-%d")

    with open(log_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PREDICTIONS_HEADERS)
        if write_header:
            writer.writeheader()
        for e in entries:
            r = e["result"]
            writer.writerow({
                "date":             today,
                "home_team":        e["home"],
                "away_team":        e["away"],
                "neutral":          e["neutral"],
                "model_home_score": r["home_score"],
                "model_away_score": r["away_score"],
                "model_total":      r["total"],
                "model_spread":     r["spread"],
                "vegas_spread":     e["vegas_spread"] if e["vegas_spread"] is not None else "",
                "vegas_total":      e["vegas_total"]  if e["vegas_total"]  is not None else "",
                "spread_edge":      e["spread_edge"]  if e["spread_edge"]  is not None else "",
                "total_edge":       e["total_edge"]   if e["total_edge"]   is not None else "",
                "is_edge":          e["is_edge"],
            })

    print(f"  Logged {len(entries)} predictions to {PREDICTIONS_LOG}")

# ══════════════════════════════════════════════════════
# STEP 7: ENTER ACTUAL RESULTS
# ══════════════════════════════════════════════════════
RESULTS_HEADERS = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "model_home_score", "model_away_score", "model_total", "model_spread",
    "vegas_spread", "vegas_total",
    "spread_error", "total_error",
    "spread_vs_vegas_error", "model_beat_vegas"
]

def enter_results():
    """
    Interactive CLI to enter actual game scores.
    Reads unresolved predictions from predictions_log.csv,
    lets you enter final scores, and saves to results_log.csv.
    """
    if not Path(PREDICTIONS_LOG).exists():
        print("No predictions log found. Run the predictor first.")
        return

    # Load all predictions
    with open(PREDICTIONS_LOG, newline="") as f:
        predictions = list(csv.DictReader(f))

    # Load already-resolved results to skip
    resolved = set()
    if Path(RESULTS_LOG).exists():
        with open(RESULTS_LOG, newline="") as f:
            for row in csv.DictReader(f):
                resolved.add((row["date"], row["home_team"], row["away_team"]))

    pending = [
        p for p in predictions
        if (p["date"], p["home_team"], p["away_team"]) not in resolved
    ]

    if not pending:
        print("No pending predictions to resolve. All caught up.")
        return

    print(f"\n{'═'*60}")
    print(f"  Enter Actual Results ({len(pending)} pending)")
    print(f"  Type 'skip' to skip a game, 'quit' to stop.")
    print(f"{'═'*60}\n")

    results_path = Path(RESULTS_LOG)
    write_header = not results_path.exists()

    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_HEADERS)
        if write_header:
            writer.writeheader()

        for p in pending:
            print(f"  {p['date']}  |  {p['away_team']} @ {p['home_team']}")
            print(f"  Model: {p['home_team']} {p['model_home_score']} - {p['away_team']} {p['model_away_score']}")
            if p["vegas_spread"]:
                print(f"  Vegas spread: {p['vegas_spread']}  |  Vegas total: {p['vegas_total']}")

            home_in = input(f"  Actual {p['home_team']} score (or skip/quit): ").strip()
            if home_in.lower() == "quit":
                break
            if home_in.lower() == "skip":
                print()
                continue

            away_in = input(f"  Actual {p['away_team']} score: ").strip()
            if away_in.lower() in ("quit", "skip"):
                break

            try:
                actual_home = float(home_in)
                actual_away = float(away_in)
            except ValueError:
                print("  Invalid input, skipping.\n")
                continue

            actual_total  = actual_home + actual_away
            actual_spread = -(actual_home - actual_away)  # Neg = home won
            spread_error  = round(float(p["model_spread"]) - actual_spread, 1)
            total_error   = round(float(p["model_total"]) - actual_total, 1)

            spread_vs_vegas = ""
            model_beat_vegas = ""
            if p["vegas_spread"]:
                vegas_spread_error = round(float(p["vegas_spread"]) - actual_spread, 1)
                spread_vs_vegas    = round(abs(float(p["spread_error"])) - abs(vegas_spread_error), 1) if p["spread_error"] else ""
                # Negative = model was closer than Vegas
                model_beat_vegas   = "YES" if spread_vs_vegas != "" and spread_vs_vegas < 0 else "NO"

            writer.writerow({
                "date":                 p["date"],
                "home_team":            p["home_team"],
                "away_team":            p["away_team"],
                "actual_home_score":    actual_home,
                "actual_away_score":    actual_away,
                "actual_total":         actual_total,
                "actual_spread":        actual_spread,
                "model_home_score":     p["model_home_score"],
                "model_away_score":     p["model_away_score"],
                "model_total":          p["model_total"],
                "model_spread":         p["model_spread"],
                "vegas_spread":         p["vegas_spread"],
                "vegas_total":          p["vegas_total"],
                "spread_error":         spread_error,
                "total_error":          total_error,
                "spread_vs_vegas_error": spread_vs_vegas,
                "model_beat_vegas":     model_beat_vegas,
            })

            print(f"  Saved. Spread error: {spread_error:+.1f} pts  |  Total error: {total_error:+.1f} pts\n")

    print(f"Results saved to {RESULTS_LOG}\n")

# ══════════════════════════════════════════════════════
# STEP 8: PERFORMANCE REPORT
# ══════════════════════════════════════════════════════
def performance_report():
    """Print model accuracy summary from results_log.csv."""
    if not Path(RESULTS_LOG).exists():
        print("No results log found. Enter some actual scores first with --results.")
        return

    with open(RESULTS_LOG, newline="") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("Results log is empty.")
        return

    spread_errors = [abs(float(r["spread_error"])) for r in rows if r["spread_error"]]
    total_errors  = [abs(float(r["total_error"]))  for r in rows if r["total_error"]]
    beat_vegas    = [r for r in rows if r.get("model_beat_vegas") == "YES"]
    vs_vegas_rows = [r for r in rows if r.get("model_beat_vegas") in ("YES", "NO")]

    n = len(rows)
    mae_spread = sum(spread_errors) / len(spread_errors) if spread_errors else None
    mae_total  = sum(total_errors)  / len(total_errors)  if total_errors  else None

    # Direction accuracy (did model pick right winner against the spread direction)
    correct_direction = sum(
        1 for r in rows
        if r["spread_error"] and
        (float(r["model_spread"]) < 0) == (float(r["actual_spread"]) < 0)
    )

    print(f"\n{'═'*60}")
    print(f"  MODEL PERFORMANCE REPORT  |  {n} games resolved")
    print(f"{'═'*60}")
    print(f"  Spread MAE         : {mae_spread:.2f} pts" if mae_spread else "  Spread MAE: N/A")
    print(f"  Total MAE          : {mae_total:.2f} pts"  if mae_total  else "  Total MAE : N/A")
    print(f"  Direction accuracy : {correct_direction}/{n} ({100*correct_direction/n:.1f}%)")
    if vs_vegas_rows:
        pct = 100 * len(beat_vegas) / len(vs_vegas_rows)
        print(f"  Model beat Vegas   : {len(beat_vegas)}/{len(vs_vegas_rows)} ({pct:.1f}%)")

    # Edge game performance
    edge_rows = [r for r in rows if r.get("is_edge", "").lower() == "true"]
    if edge_rows:
        edge_spread_errors = [abs(float(r["spread_error"])) for r in edge_rows if r["spread_error"]]
        edge_mae = sum(edge_spread_errors) / len(edge_spread_errors) if edge_spread_errors else None
        print(f"\n  EDGE GAMES ({len(edge_rows)} games flagged)")
        print(f"  Edge spread MAE    : {edge_mae:.2f} pts" if edge_mae else "  N/A")

    # Best and worst predictions
    sorted_by_error = sorted(rows, key=lambda r: abs(float(r["spread_error"])) if r["spread_error"] else 999)
    print(f"\n  BEST PREDICTIONS (smallest spread error):")
    for r in sorted_by_error[:3]:
        print(f"    {r['date']}  {r['away_team']} @ {r['home_team']}  error: {float(r['spread_error']):+.1f} pts")
    print(f"\n  WORST PREDICTIONS (largest spread error):")
    for r in sorted_by_error[-3:]:
        print(f"    {r['date']}  {r['away_team']} @ {r['home_team']}  error: {float(r['spread_error']):+.1f} pts")

    print(f"\n{'═'*60}\n")

# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--results" in sys.argv:
        enter_results()
    elif "--report" in sys.argv:
        performance_report()
    else:
        run_slate("kenpom_raw.txt")
