"""
KenPom + T-Rank NCAA Basketball Predictor
-------------------------------------------
Workflow:
  1. Paste KenPom table into kenpom_raw.txt (weekly refresh)
  2. Paste Barttorvik T-Rank table into barttorvik_raw.txt (weekly refresh)
     (select-all from barttorvik.com/trank.php and paste as-is)
  3. Run this script
  4. Script pulls today's NCAAB matchups from ESPN
  5. Script pulls live lines from The Odds API
  6. Runs possession-based model with both KenPom & T-Rank data,
     applies 3.5-pt home court advantage, flags edges vs Vegas
  7. When both KenPom and T-Rank agree on an edge direction,
     the game is flagged as HIGH CONFIDENCE
  8. Posts results to Discord (set DISCORD_WEBHOOK_URL env var)
  9. Logs all predictions to predictions_log.csv
 10. Run: python kenpom_predictor.py --results  to enter actual scores
 11. Run: python kenpom_predictor.py --report   to see model accuracy

Dependencies:
  pip install requests thefuzz python-Levenshtein
"""

import os
import re
import csv
import sys
import math
import json
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from thefuzz import process
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()


# ══════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════
ODDS_API_KEY    = os.getenv("ODDS_API_KEY")   # Free at the-odds-api.com
ODDS_BOOK       = "draftkings"          # draftkings, fanduel, betmgm, etc.
EDGE_THRESHOLD  = 3.0                   # Flag if model vs line differs >= this
FUZZY_THRESHOLD = 75                    # Min fuzzy match score (0-100)

# Discord webhook (set via environment variable)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Log files
PREDICTIONS_LOG = "predictions_log.csv"
RESULTS_LOG     = "results_log.csv"
BARTTORVIK_FILE = "barttorvik_raw.txt"

# Model constants
AVG_EFFICIENCY     = 100.0
AVG_TEMPO_2026     = 68.4
LAMBDA             = 0.88
TEMPO_EXP          = 0.48
TEMPO_LEAGUE_EXP   = 0.04
HCA                = 3.5
TEAM_ALIASES_FILE  = "team_aliases.txt"

# ══════════════════════════════════════════════════════
# TEAM ALIAS LOADER
# ══════════════════════════════════════════════════════
def load_aliases(path: str = TEAM_ALIASES_FILE) -> dict[str, dict[str, str]]:
    """
    Load team name aliases from a sectioned text file.

    Format:
        [kenpom]
        ESPN Name = KenPom Name

        [barttorvik]
        ESPN Name = Barttorvik Name

        [odds_api]
        ESPN Name = Odds API Name

    Returns {section: {espn_name: source_name}}.
    Run dump_team_names.py to generate the starter file.
    """
    aliases: dict[str, dict[str, str]] = {}
    if not Path(path).exists():
        return aliases
    current: str | None = None
    with open(path) as f:
        for raw in f:
            line = raw.split("#")[0].strip()   # strip inline comments
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].lower()
                aliases.setdefault(current, {})
                continue
            if current and "=" in line:
                espn, _, source = line.partition("=")
                aliases[current][espn.strip()] = source.strip()
    return aliases


TEAM_ALIASES: dict[str, dict[str, str]] = load_aliases()
if TEAM_ALIASES:
    total = sum(len(v) for v in TEAM_ALIASES.values())
    print(f"  [aliases] Loaded {total} alias(es) from {TEAM_ALIASES_FILE}: "
          + ", ".join(f"{k}={len(v)}" for k, v in TEAM_ALIASES.items()))
else:
    print(f"  [aliases] {TEAM_ALIASES_FILE} not found or empty — using fuzzy matching only.")

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

# ══════════════════════════════════════════════════════
# STEP 1b: LOAD BARTTORVIK T-RANK
# ══════════════════════════════════════════════════════
def parse_barttorvik(filepath: str) -> dict[str, Team]:
    """
    Parse raw Barttorvik T-Rank copy-paste from barttorvik.com/trank.php.

    The site produces a staggered multi-line format when copy-pasted, where
    each stat and its rank appear on interleaved lines. Each team block:
      Line +0:  {Rk}\\t{Team}\\t{Conf}\\t{G}\\t{Rec}
      Line +1:  {AdjOE}                      (standalone value)
      Line +2:  {AdjOE_rank}\\t{AdjDE}
      Line +3:  {AdjDE_rank}\\t{Barthag}
      ...
      Line +18: {3PRD_rank}\\t{AdjT}
      Line +19: {AdjT_rank}\\t{WAB}
      Line +20: {WAB_rank}

    Select-all the table on barttorvik.com/trank.php and paste into
    barttorvik_raw.txt. No editing needed.
    """
    teams = {}
    with open(filepath, "r") as f:
        lines = [line.rstrip("\n") for line in f]

    for i, line in enumerate(lines):
        parts = line.strip().split("\t")
        # Team header: integer rank, letter-starting team name, record with dash
        if (len(parts) >= 5
                and parts[0].strip().isdigit()
                and parts[1].strip()
                and parts[1].strip()[0].isalpha()
                and "-" in parts[4].strip()):
            team_name = parts[1].strip()
            try:
                adj_o = float(lines[i + 1].strip())
                adj_d = float(lines[i + 2].strip().split("\t")[1])
                adj_t = float(lines[i + 18].strip().split("\t")[1])
                teams[team_name] = Team(name=team_name, adj_o=adj_o, adj_d=adj_d, adj_t=adj_t)
            except (ValueError, IndexError):
                continue
    return teams


def load_barttorvik() -> dict[str, Team]:
    """Load Barttorvik T-Rank data from barttorvik_raw.txt."""
    if not Path(BARTTORVIK_FILE).exists():
        print(f"  Barttorvik: {BARTTORVIK_FILE} not found -- skipping T-Rank.")
        return {}
    teams = parse_barttorvik(BARTTORVIK_FILE)
    if teams:
        print(f"  Loaded {len(teams)} teams from {BARTTORVIK_FILE}.")
    else:
        print(f"  Barttorvik: no data parsed from {BARTTORVIK_FILE}.")
    return teams


def fuzzy_lookup(
    query: str,
    team_dict: dict[str, Team],
    threshold: int = FUZZY_THRESHOLD,
    section: str | None = None,
) -> Team | None:
    """Look up a team by ESPN name in a KenPom or Barttorvik dict.

    If *section* is given (e.g. 'kenpom' or 'barttorvik') and team_aliases.txt
    contains an alias for *query* in that section, the alias is used as an
    exact lookup before falling back to fuzzy matching.
    """
    resolved = TEAM_ALIASES.get(section, {}).get(query, query) if section else query
    if resolved in team_dict:
        return team_dict[resolved]
    keys = list(team_dict.keys())
    match, score = process.extractOne(resolved, keys)
    if score >= threshold:
        if resolved != query or match.lower() != query.lower():
            # Print whenever an alias was applied OR fuzzy had to fire
            alias_note = f" [alias→{resolved!r}]" if resolved != query else ""
            print(f"  [lookup/{section}] {query!r}{alias_note} → fuzzy matched {match!r} ({score})")
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
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={today}&groups=50"
    
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

        # ESPN returns home/away via homeAway flag.
        # Prefer shortDisplayName (e.g. "IU Indy", "SC State") over displayName
        # which can be ambiguous for schools sharing a state name (e.g. Indiana,
        # South Carolina). Fall back to displayName when shortDisplayName is absent.
        home = away = None
        for c in competitors:
            team = c.get("team", {})
            team_name = team.get("shortDisplayName") or team.get("displayName", "")
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

    # Build odds lookup: (home_team, away_team) -> {spread_home, spread_away, total}
    odds_lookup = {}
    for game in odds_data:
        h = game.get("home_team", "")
        a = game.get("away_team", "")
        spread_home = spread_away = total = None
        for bookie in game.get("bookmakers", []):
            for market in bookie.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == h:
                            spread_home = outcome["point"]
                        elif outcome["name"] == a:
                            spread_away = outcome["point"]
                if market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            total = outcome["point"]
        odds_lookup[(h, a)] = {"spread_home": spread_home, "spread_away": spread_away, "total": total}

    # Build a flat name→game index so we can match each team individually.
    # Matching full "A vs B" strings is unreliable because token-sort fuzzy
    # scoring treats "A vs B" and "B vs A" as nearly identical.
    api_team_names: list[str] = []
    team_to_game: dict[str, tuple[str, str]] = {}
    for (h, a) in odds_lookup:
        api_team_names.append(h)
        api_team_names.append(a)
        team_to_game[h] = (h, a)
        team_to_game[a] = (h, a)

    odds_aliases = TEAM_ALIASES.get("odds_api", {})

    for m in matchups:
        if not api_team_names:
            break

        # Step 1: find the API team that best matches ESPN's home team.
        # If team_aliases.txt has an [odds_api] entry for this name, use it
        # as an exact key into team_to_game before falling back to fuzzy.
        home_query = odds_aliases.get(m.home, m.home)
        if home_query in team_to_game:
            home_match, home_score = home_query, 100
        else:
            home_match, home_score = process.extractOne(home_query, api_team_names)
        if home_score < FUZZY_THRESHOLD:
            continue

        game_key = team_to_game[home_match]
        api_h, api_a = game_key
        entry = odds_lookup[game_key]

        # Step 2: verify the away team also matches the other team in that game.
        # Apply alias for the away team as well before fuzzy-checking.
        away_query = odds_aliases.get(m.away, m.away)
        other_api_team = api_a if home_match == api_h else api_h
        if away_query == other_api_team:
            away_score = 100
        else:
            _, away_score = process.extractOne(away_query, [other_api_team])
        if away_score < FUZZY_THRESHOLD:
            continue

        # Step 3: assign spread from ESPN home team's perspective
        if home_match == api_h:
            # ESPN home == API home → use home spread directly
            m.vegas_spread = entry["spread_home"]
        else:
            # ESPN home == API away (teams swapped) → use away spread
            m.vegas_spread = entry["spread_away"]

        m.vegas_total = entry["total"]

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
    print(f"\n{'═'*70}")
    print(f"  KenPom + T-Rank NCAA Predictor  |  {datetime.now().strftime('%A %b %d, %Y')}")
    print(f"{'═'*70}")

    # Load KenPom
    kp_teams = parse_kenpom(kenpom_file)
    print(f"  Loaded {len(kp_teams)} teams from KenPom data.")

    # Load Barttorvik T-Rank
    bt_teams = load_barttorvik()

    print()

    # Today's games
    matchups = get_todays_matchups()
    if not matchups:
        print("  No games found for today.")
        return

    # Attach lines
    matchups = get_odds(matchups)

    entries = []
    no_data = []

    for m in matchups:
        kp_home = fuzzy_lookup(m.home, kp_teams, section="kenpom")
        kp_away = fuzzy_lookup(m.away, kp_teams, section="kenpom")

        if not kp_home or not kp_away:
            no_data.append(f"  NO KENPOM DATA: {m.away} @ {m.home}")
            continue

        # ── KenPom prediction ──
        kp_result = predict_game(kp_home, kp_away, neutral=m.neutral)

        kp_spread_edge = None
        kp_total_edge  = None
        if m.vegas_spread is not None:
            kp_spread_edge = round(m.vegas_spread - kp_result["spread"], 1)
        if m.vegas_total is not None:
            kp_total_edge = round(kp_result["total"] - m.vegas_total, 1)

        # ── Barttorvik prediction (if available) ──
        bt_result = None
        bt_spread_edge = None
        bt_total_edge  = None
        if bt_teams:
            bt_home = fuzzy_lookup(m.home, bt_teams, section="barttorvik")
            bt_away = fuzzy_lookup(m.away, bt_teams, section="barttorvik")
            if bt_home and bt_away:
                bt_result = predict_game(bt_home, bt_away, neutral=m.neutral)
                if m.vegas_spread is not None:
                    bt_spread_edge = round(m.vegas_spread - bt_result["spread"], 1)
                if m.vegas_total is not None:
                    bt_total_edge = round(bt_result["total"] - m.vegas_total, 1)

        # Determine favorites (KenPom is primary)
        model_fav  = kp_home.name if kp_result["spread"] < 0 else kp_away.name
        model_line = abs(kp_result["spread"])
        vegas_fav  = kp_home.name if (m.vegas_spread or 0) < 0 else kp_away.name
        vegas_line = abs(m.vegas_spread) if m.vegas_spread is not None else None

        # Flag edge games — model's predicted spread must be >= the disagreement
        # with Vegas (i.e. high-conviction plays only: abs(model) >= abs(edge)).
        # Either model qualifying is enough to trigger an alert.
        kp_is_spread_edge = (
            kp_spread_edge is not None
            and kp_result["spread"] != 0
            and abs(kp_result["spread"]) >= abs(kp_spread_edge)
            and abs(kp_spread_edge) > 0
        )
        kp_is_total_edge = (
            kp_total_edge is not None
            and kp_result["total"] > 0
            and abs(kp_total_edge) > 0
            and abs(kp_result["total"]) >= abs(kp_total_edge)
        )
        bt_is_spread_edge = (
            bt_result is not None
            and bt_spread_edge is not None
            and bt_result["spread"] != 0
            and abs(bt_result["spread"]) >= abs(bt_spread_edge)
            and abs(bt_spread_edge) > 0
        )
        bt_is_total_edge = (
            bt_result is not None
            and bt_total_edge is not None
            and bt_result["total"] > 0
            and abs(bt_total_edge) > 0
            and abs(bt_result["total"]) >= abs(bt_total_edge)
        )

        is_spread_edge = kp_is_spread_edge or bt_is_spread_edge
        is_total_edge  = kp_is_total_edge  or bt_is_total_edge

        # Confidence: HIGH when both models agree on spread edge direction
        confidence = ""
        if kp_is_spread_edge and bt_is_spread_edge:
            if (kp_spread_edge > 0) == (bt_spread_edge > 0):
                confidence = "HIGH"

        entry = {
            "home": kp_home.name, "away": kp_away.name,
            "neutral": m.neutral,
            "result": kp_result,
            "bt_result": bt_result,
            "model_fav": model_fav, "model_line": model_line,
            "vegas_fav": vegas_fav, "vegas_line": vegas_line,
            "vegas_spread": m.vegas_spread, "vegas_total": m.vegas_total,
            "spread_edge": kp_spread_edge, "total_edge": kp_total_edge,
            "bt_spread_edge": bt_spread_edge, "bt_total_edge": bt_total_edge,
            "kp_is_spread_edge": kp_is_spread_edge, "kp_is_total_edge": kp_is_total_edge,
            "bt_is_spread_edge": bt_is_spread_edge, "bt_is_total_edge": bt_is_total_edge,
            "is_edge": is_spread_edge or is_total_edge,
            "is_spread_edge": is_spread_edge,
            "is_total_edge": is_total_edge,
            "confidence": confidence,
        }

        entries.append(entry)

    # ── Print all games ──
    has_bt = any(e["bt_result"] for e in entries)
    print(f"  TODAY'S GAMES ({len(entries)} with data)\n")
    if has_bt:
        print(f"  {'Matchup':<35} {'KP Spread':>10} {'BT Spread':>10} {'Vegas':>10} {'Edge':>8}")
        print(f"  {'─'*35} {'─'*10} {'─'*10} {'─'*10} {'─'*8}")
    else:
        print(f"  {'Matchup':<35} {'Model':>10} {'Vegas':>10} {'Edge':>8}")
        print(f"  {'─'*35} {'─'*10} {'─'*10} {'─'*8}")

    for e in entries:
        venue = " [N]" if e["neutral"] else ""
        matchup = f"{e['away']} @ {e['home']}{venue}"
        kp_str = f"{e['model_fav']} -{e['model_line']:.1f}"
        vegas_str = f"{e['vegas_fav']} -{e['vegas_line']:.1f}" if e["vegas_line"] else "N/A"
        edge_str  = f"{e['spread_edge']:+.1f}" if e["spread_edge"] is not None else "N/A"
        flag = ""
        if e["confidence"] == "HIGH":
            flag = " ◄◄◄ HIGH"
        elif e["is_edge"]:
            flag = " ◄◄◄"

        if has_bt:
            bt_r = e["bt_result"]
            if bt_r:
                bt_fav  = e["home"] if bt_r["spread"] < 0 else e["away"]
                bt_line = abs(bt_r["spread"])
                bt_str = f"{bt_fav} -{bt_line:.1f}"
            else:
                bt_str = "N/A"
            print(f"  {matchup:<35} {kp_str:>10} {bt_str:>10} {vegas_str:>10} {edge_str:>8}{flag}")
        else:
            print(f"  {matchup:<35} {kp_str:>10} {vegas_str:>10} {edge_str:>8}{flag}")

    # ── Print edge games ──
    edge_games = [e for e in entries if e["is_edge"]]
    if edge_games:
        print(f"\n{'═'*70}")
        print(f"  EDGE GAMES (model vs line >= {EDGE_THRESHOLD} pts)")
        print(f"{'═'*70}")
        for e in edge_games:
            r = e["result"]
            conf_tag = "  *** HIGH CONFIDENCE ***" if e["confidence"] == "HIGH" else ""
            print(f"\n  {e['away']} @ {e['home']}{conf_tag}")
            print(f"  KenPom: {e['home']} {r['home_score']}  |  {e['away']} {r['away_score']}")
            print(f"          Total {r['total']}  |  Spread {e['model_fav']} -{e['model_line']:.1f}")
            if e["bt_result"]:
                bt = e["bt_result"]
                bt_fav  = e["home"] if bt["spread"] < 0 else e["away"]
                bt_line = abs(bt["spread"])
                print(f"  T-Rank: {e['home']} {bt['home_score']}  |  {e['away']} {bt['away_score']}")
                print(f"          Total {bt['total']}  |  Spread {bt_fav} -{bt_line:.1f}")
            if e["vegas_spread"] is not None:
                print(f"  Vegas : {e['vegas_fav']} -{e['vegas_line']:.1f}  |  Total {e['vegas_total']}")
            if e["is_spread_edge"]:
                direction = "home" if e["spread_edge"] > 0 else "away"
                print(f"  KP SPREAD EDGE: model likes {direction} team by {abs(e['spread_edge']):.1f} pts vs market")
            if e["bt_spread_edge"] is not None and abs(e["bt_spread_edge"]) >= EDGE_THRESHOLD:
                direction = "home" if e["bt_spread_edge"] > 0 else "away"
                print(f"  BT SPREAD EDGE: T-Rank likes {direction} team by {abs(e['bt_spread_edge']):.1f} pts vs market")
            if e["is_total_edge"]:
                direction = "OVER" if e["total_edge"] > 0 else "UNDER"
                print(f"  TOTAL EDGE    : model says {direction} by {abs(e['total_edge']):.1f} pts")

    if no_data:
        print(f"\n  SKIPPED (not in KenPom data):")
        for nd in no_data:
            print(nd)

    # Log all predictions
    if entries:
        log_predictions(entries)
        send_discord_message(entries)

    print(f"\n{'═'*70}\n")

# ══════════════════════════════════════════════════════
# STEP 6: LOG PREDICTIONS TO CSV
# ══════════════════════════════════════════════════════
PREDICTIONS_HEADERS = [
    "date", "home_team", "away_team", "neutral",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
    "bt_home_score", "bt_away_score", "bt_total", "bt_spread",
    "vegas_spread", "vegas_total",
    "kp_spread_edge", "kp_total_edge", "bt_spread_edge", "bt_total_edge",
    "is_edge", "confidence"
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
            kp = e["result"]
            bt = e["bt_result"]
            writer.writerow({
                "date":             today,
                "home_team":        e["home"],
                "away_team":        e["away"],
                "neutral":          e["neutral"],
                "kp_home_score":    kp["home_score"],
                "kp_away_score":    kp["away_score"],
                "kp_total":         kp["total"],
                "kp_spread":        kp["spread"],
                "bt_home_score":    bt["home_score"] if bt else "",
                "bt_away_score":    bt["away_score"] if bt else "",
                "bt_total":         bt["total"]      if bt else "",
                "bt_spread":        bt["spread"]     if bt else "",
                "vegas_spread":     e["vegas_spread"] if e["vegas_spread"] is not None else "",
                "vegas_total":      e["vegas_total"]  if e["vegas_total"]  is not None else "",
                "kp_spread_edge":   e["spread_edge"]    if e["spread_edge"]    is not None else "",
                "kp_total_edge":    e["total_edge"]     if e["total_edge"]     is not None else "",
                "bt_spread_edge":   e["bt_spread_edge"] if e["bt_spread_edge"] is not None else "",
                "bt_total_edge":    e["bt_total_edge"]  if e["bt_total_edge"]  is not None else "",
                "is_edge":          e["is_edge"],
                "confidence":       e["confidence"],
            })

    print(f"  Logged {len(entries)} predictions to {PREDICTIONS_LOG}")

# ══════════════════════════════════════════════════════
# DISCORD WEBHOOK
# ══════════════════════════════════════════════════════
def send_discord_message(entries: list[dict]):
    """Format predictions and send to Discord via webhook."""
    if not DISCORD_WEBHOOK_URL:
        return

    today = datetime.now().strftime("%A %b %d, %Y")
    edge_games = [e for e in entries if e["is_edge"]]
    high_conf  = [e for e in entries if e["confidence"] == "HIGH"]

    # ── Helper: post one payload ──
    def _post(payload: dict, label: str) -> None:
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 204:
                print(f"  Discord: {label} posted successfully.")
            else:
                print(f"  Discord: {label} returned status {resp.status_code} -- {resp.text}")
        except Exception as exc:
            print(f"  Discord: failed to send {label} -- {exc}")

    # ── Build all-games as inline embed fields (renders as a card grid in Discord) ──
    all_fields = []
    for e in entries:
        venue = " `[N]`" if e["neutral"] else ""
        if e["confidence"] == "HIGH":
            prefix = "⚡⚡ "
        elif e["is_edge"]:
            prefix = "⚡ "
        else:
            prefix = ""
        name      = f"{prefix}**{e['away']} @ {e['home']}**{venue}"
        kp_str    = f"{e['model_fav']} -{e['model_line']:.1f}"
        vegas_str = f"{e['vegas_fav']} -{e['vegas_line']:.1f}" if e["vegas_line"] else "N/A"
        edge_str  = f"{e['spread_edge']:+.1f}" if e["spread_edge"] is not None else "N/A"
        value     = f"KP: `{kp_str}`\nVegas: `{vegas_str}`\nEdge: `{edge_str}`"
        all_fields.append({"name": name, "value": value, "inline": True})

    footer_parts = [f"{len(entries)} games analyzed", f"{len(edge_games)} edge games"]
    if high_conf:
        footer_parts.append(f"{len(high_conf)} high-confidence")
    footer_text = " | ".join(footer_parts)

    # Send all-games in chunks of 20 fields (Discord max is 25; stay under 6000-char limit)
    CHUNK = 20
    field_chunks = [all_fields[i:i + CHUNK] for i in range(0, len(all_fields), CHUNK)] or [[]]
    for idx, chunk in enumerate(field_chunks):
        title = f"KenPom + T-Rank Predictor | {today}"
        if len(field_chunks) > 1:
            title += f"  ({idx + 1}/{len(field_chunks)})"
        embed: dict = {
            "title":  title,
            "color":  0x1E90FF,
            "fields": chunk or [{"name": "No games today", "value": "\u200b", "inline": False}],
        }
        if idx == len(field_chunks) - 1:
            embed["footer"] = {"text": footer_text}
        _post({"embeds": [embed]}, f"all-games ({idx + 1}/{len(field_chunks)})")

    # ── Edge-games alert embed ────────────────────────────────────────────────
    if edge_games:
        edge_fields = []
        for e in edge_games:
            kp  = e["result"]
            bt  = e["bt_result"]
            is_high = e["confidence"] == "HIGH"

            title_tag = "⚡⚡ HIGH CONFIDENCE" if is_high else "⚡ EDGE PLAY"
            name = f"{title_tag} — **{e['away']} @ {e['home']}**"

            lines = []

            # KenPom line
            lines.append(f"**KP:**  {e['model_fav']} -{e['model_line']:.1f}")

            # Barttorvik line (if available)
            if bt:
                bt_fav  = e["home"] if bt["spread"] < 0 else e["away"]
                bt_line = abs(bt["spread"])
                lines.append(f"**BT:**  {bt_fav} -{bt_line:.1f}")

            # Vegas line
            if e["vegas_spread"] is not None:
                lines.append(f"**Vegas:** {e['vegas_fav']} -{e['vegas_line']:.1f}  |  O/U {e['vegas_total']}")

            # Spread edge — show both models, flag which one(s) triggered
            if e["spread_edge"] is not None:
                kp_tag = " 🔺" if e["kp_is_spread_edge"] else ""
                lines.append(f"**KP Spread Edge:** {e['spread_edge']:+.1f}{kp_tag}")
            if e["bt_spread_edge"] is not None:
                bt_tag = " 🔺" if e["bt_is_spread_edge"] else ""
                lines.append(f"**BT Spread Edge:** {e['bt_spread_edge']:+.1f}{bt_tag}")

            # Total edge — show both models, flag which one(s) triggered
            if e["total_edge"] is not None:
                kp_tag = " 🔺" if e["kp_is_total_edge"] else ""
                lines.append(f"**KP Total Edge:** {e['total_edge']:+.1f}{kp_tag}")
            if e["bt_total_edge"] is not None:
                bt_tag = " 🔺" if e["bt_is_total_edge"] else ""
                lines.append(f"**BT Total Edge:** {e['bt_total_edge']:+.1f}{bt_tag}")

            edge_fields.append({"name": name, "value": "\n".join(lines), "inline": False})

        conf_count = len([e for e in edge_games if e["confidence"] == "HIGH"])
        edge_footer = f"{len(edge_games)} edge play(s)"
        if conf_count:
            edge_footer += f"  |  {conf_count} high-confidence"

        _post(
            {"embeds": [{
                "title":  f"🎯 Edge Plays | {today}",
                "color":  0xFFD700,
                "fields": edge_fields,
                "footer": {"text": edge_footer},
            }]},
            f"edge-plays ({len(edge_games)} game(s))",
        )


# ══════════════════════════════════════════════════════
# STEP 7: ENTER ACTUAL RESULTS
# ══════════════════════════════════════════════════════
RESULTS_HEADERS = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
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
            print(f"  KenPom: {p['home_team']} {p['kp_home_score']} - {p['away_team']} {p['kp_away_score']}")
            if p.get("bt_home_score"):
                print(f"  T-Rank: {p['home_team']} {p['bt_home_score']} - {p['away_team']} {p['bt_away_score']}")
            if p["vegas_spread"]:
                print(f"  Vegas spread: {p['vegas_spread']}  |  Vegas total: {p['vegas_total']}")
            if p.get("confidence"):
                print(f"  Confidence: {p['confidence']}")

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
            spread_error  = round(float(p["kp_spread"]) - actual_spread, 1)
            total_error   = round(float(p["kp_total"]) - actual_total, 1)

            spread_vs_vegas = ""
            model_beat_vegas = ""
            if p["vegas_spread"]:
                vegas_spread_error = round(float(p["vegas_spread"]) - actual_spread, 1)
                spread_vs_vegas    = round(abs(spread_error) - abs(vegas_spread_error), 1)
                # Negative = model was closer than Vegas
                model_beat_vegas   = "YES" if spread_vs_vegas < 0 else "NO"

            writer.writerow({
                "date":                 p["date"],
                "home_team":            p["home_team"],
                "away_team":            p["away_team"],
                "actual_home_score":    actual_home,
                "actual_away_score":    actual_away,
                "actual_total":         actual_total,
                "actual_spread":        actual_spread,
                "kp_home_score":        p["kp_home_score"],
                "kp_away_score":        p["kp_away_score"],
                "kp_total":             p["kp_total"],
                "kp_spread":            p["kp_spread"],
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
        (float(r["kp_spread"]) < 0) == (float(r["actual_spread"]) < 0)
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
# STEP 9: AUTO-FETCH YESTERDAY'S SCORES + DAILY RECAP
# ══════════════════════════════════════════════════════

def _fetch_espn_scores(date_str: str) -> dict[str, tuple[float, float]]:
    """
    Fetch final scores from ESPN for *date_str* (YYYYMMDD).
    Returns {(home_display_name, away_display_name): (home_score, away_score)}
    for completed games only.
    """
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/"
        f"mens-college-basketball/scoreboard?dates={date_str}&groups=50"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ERROR fetching ESPN scores for {date_str}: {e}")
        return {}

    results = {}
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {}).get("completed", False)
        if not status:
            continue
        home = away = None
        home_score = away_score = None
        for c in comp.get("competitors", []):
            name  = c.get("team", {}).get("displayName", "")
            score = c.get("score", "")
            try:
                score = float(score)
            except (ValueError, TypeError):
                score = None
            if c.get("homeAway") == "home":
                home, home_score = name, score
            else:
                away, away_score = name, score
        if home and away and home_score is not None and away_score is not None:
            results[(home, away)] = (home_score, away_score)
    return results


def auto_results(target_date: str | None = None) -> None:
    """
    Automatically resolve yesterday's predictions against ESPN final scores,
    append to results_log.csv, then post a daily recap to Discord.

    target_date: 'YYYY-MM-DD' (defaults to yesterday)
    """
    if target_date is None:
        from datetime import timedelta
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    espn_date = target_date.replace("-", "")

    print(f"\n{'═'*70}")
    print(f"  Auto-Results  |  {target_date}")
    print(f"{'═'*70}")

    # ── Load yesterday's predictions ──────────────────────────────────────
    if not Path(PREDICTIONS_LOG).exists():
        print("  No predictions log found.")
        return

    with open(PREDICTIONS_LOG, newline="") as f:
        all_preds = list(csv.DictReader(f))

    preds = [p for p in all_preds if p["date"] == target_date]
    if not preds:
        print(f"  No predictions found for {target_date}.")
        return
    print(f"  {len(preds)} prediction(s) found for {target_date}.")

    # ── Skip already-resolved games ────────────────────────────────────────
    resolved = set()
    if Path(RESULTS_LOG).exists():
        with open(RESULTS_LOG, newline="") as f:
            for row in csv.DictReader(f):
                resolved.add((row["date"], row["home_team"], row["away_team"]))

    preds = [p for p in preds if (p["date"], p["home_team"], p["away_team"]) not in resolved]
    if not preds:
        print("  All games already resolved.")
        return

    # ── Fetch ESPN final scores ─────────────────────────────────────────────
    print(f"  Fetching ESPN scores for {target_date}...")
    espn_scores = _fetch_espn_scores(espn_date)
    if not espn_scores:
        print("  No completed games found on ESPN.")
        return
    print(f"  {len(espn_scores)} completed game(s) from ESPN.")

    espn_team_names = [name for pair in espn_scores for name in pair]

    # ── Match and write results ────────────────────────────────────────────
    results_path = Path(RESULTS_LOG)
    write_header  = not results_path.exists()
    resolved_rows = []

    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_HEADERS)
        if write_header:
            writer.writeheader()

        for p in preds:
            # Fuzzy-match the logged home team name to ESPN team names
            home_match, home_score_val = process.extractOne(p["home_team"], espn_team_names)
            if home_score_val < FUZZY_THRESHOLD:
                print(f"  SKIP (no ESPN match): {p['away_team']} @ {p['home_team']}")
                continue

            # Find the game that contains this home team
            game_key = next(
                (k for k in espn_scores if home_match in k),
                None,
            )
            if game_key is None:
                print(f"  SKIP (key lookup failed): {p['away_team']} @ {p['home_team']}")
                continue

            actual_home_score, actual_away_score = espn_scores[game_key]
            actual_total  = actual_home_score + actual_away_score
            actual_spread = -(actual_home_score - actual_away_score)  # negative = home won

            kp_spread = float(p["kp_spread"]) if p["kp_spread"] else None
            kp_total  = float(p["kp_total"])  if p["kp_total"]  else None
            vegas_spread = float(p["vegas_spread"]) if p["vegas_spread"] else None
            vegas_total  = float(p["vegas_total"])  if p["vegas_total"]  else None

            spread_error = round(kp_spread - actual_spread, 1) if kp_spread is not None else None
            total_error  = round(kp_total  - actual_total,  1) if kp_total  is not None else None

            spread_vs_vegas  = ""
            model_beat_vegas = ""
            if spread_error is not None and vegas_spread is not None:
                vegas_err        = abs(vegas_spread - actual_spread)
                spread_vs_vegas  = round(abs(spread_error) - vegas_err, 1)
                model_beat_vegas = "YES" if spread_vs_vegas < 0 else "NO"

            row = {
                "date":                  target_date,
                "home_team":             p["home_team"],
                "away_team":             p["away_team"],
                "actual_home_score":     actual_home_score,
                "actual_away_score":     actual_away_score,
                "actual_total":          actual_total,
                "actual_spread":         actual_spread,
                "kp_home_score":         p["kp_home_score"],
                "kp_away_score":         p["kp_away_score"],
                "kp_total":              kp_total,
                "kp_spread":             kp_spread,
                "vegas_spread":          vegas_spread or "",
                "vegas_total":           vegas_total  or "",
                "spread_error":          spread_error if spread_error is not None else "",
                "total_error":           total_error  if total_error  is not None else "",
                "spread_vs_vegas_error": spread_vs_vegas,
                "model_beat_vegas":      model_beat_vegas,
            }
            writer.writerow(row)
            resolved_rows.append({**row, "is_edge": p.get("is_edge", ""), "confidence": p.get("confidence", "")})
            print(f"  Resolved: {p['away_team']} @ {p['home_team']}  "
                  f"actual {actual_home_score:.0f}-{actual_away_score:.0f}  "
                  f"spread err {spread_error:+.1f}" if spread_error is not None else
                  f"  Resolved: {p['away_team']} @ {p['home_team']}")

    if not resolved_rows:
        print("  No games could be matched to ESPN scores.")
        return

    print(f"\n  Wrote {len(resolved_rows)} result(s) to {RESULTS_LOG}")

    # ── Daily recap to Discord ─────────────────────────────────────────────
    _send_daily_recap(target_date, resolved_rows)


def _send_daily_recap(date_str: str, rows: list[dict]) -> None:
    """Post a daily results recap embed to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return

    spread_errors  = [abs(float(r["spread_error"])) for r in rows if r["spread_error"] != ""]
    total_errors   = [abs(float(r["total_error"]))  for r in rows if r["total_error"]  != ""]
    beat_vegas     = [r for r in rows if r.get("model_beat_vegas") == "YES"]
    vs_vegas_rows  = [r for r in rows if r.get("model_beat_vegas") in ("YES", "NO")]
    edge_rows      = [r for r in rows if str(r.get("is_edge", "")).lower() == "true"]

    mae_spread = round(sum(spread_errors) / len(spread_errors), 2) if spread_errors else None
    mae_total  = round(sum(total_errors)  / len(total_errors),  2) if total_errors  else None

    correct_dir = sum(
        1 for r in rows
        if r["spread_error"] != "" and r.get("kp_spread") not in (None, "")
        and (float(r["kp_spread"]) < 0) == (float(r["actual_spread"]) < 0)
    )
    n = len(rows)

    fields = []

    # ── Per-game results ───────────────────────────────────────────────────
    for r in rows:
        matchup = f"{r['away_team']} @ {r['home_team']}"
        ah, aa  = float(r["actual_home_score"]), float(r["actual_away_score"])
        lines   = [f"**Score:** {r['home_team']} {ah:.0f} – {r['away_team']} {aa:.0f}"]
        if r["spread_error"] != "":
            err = float(r["spread_error"])
            lines.append(f"**KP Spread err:** {err:+.1f} pts")
        if r["total_error"] != "":
            err = float(r["total_error"])
            lines.append(f"**KP Total err:** {err:+.1f} pts")
        if r.get("model_beat_vegas") in ("YES", "NO"):
            tag = "✅ closer than Vegas" if r["model_beat_vegas"] == "YES" else "❌ Vegas was closer"
            lines.append(tag)
        conf = r.get("confidence", "")
        edge = str(r.get("is_edge", "")).lower() == "true"
        if conf == "HIGH":
            prefix = "⚡⚡ "
        elif edge:
            prefix = "⚡ "
        else:
            prefix = ""
        fields.append({"name": f"{prefix}**{matchup}**", "value": "\n".join(lines), "inline": True})

    # ── Summary field ──────────────────────────────────────────────────────
    summary_lines = [f"**Games resolved:** {n}"]
    if mae_spread is not None:
        summary_lines.append(f"**Spread MAE:** {mae_spread} pts")
    if mae_total is not None:
        summary_lines.append(f"**Total MAE:** {mae_total} pts")
    if n:
        summary_lines.append(f"**Direction accuracy:** {correct_dir}/{n} ({100*correct_dir/n:.0f}%)")
    if vs_vegas_rows:
        pct = 100 * len(beat_vegas) / len(vs_vegas_rows)
        summary_lines.append(f"**Beat Vegas:** {len(beat_vegas)}/{len(vs_vegas_rows)} ({pct:.0f}%)")
    if edge_rows:
        summary_lines.append(f"**Edge games resolved:** {len(edge_rows)}")

    fields.append({"name": "📊 Summary", "value": "\n".join(summary_lines), "inline": False})

    display_date = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A %b %d, %Y")

    # chunk fields to stay under Discord's 25-field / 6000-char limits
    CHUNK = 24
    chunks = [fields[i:i + CHUNK] for i in range(0, len(fields), CHUNK)] or [[]]
    for idx, chunk in enumerate(chunks):
        title = f"📅 Daily Results | {display_date}"
        if len(chunks) > 1:
            title += f" ({idx + 1}/{len(chunks)})"
        payload = {"embeds": [{"title": title, "color": 0x2ECC71, "fields": chunk}]}
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 204:
                print(f"  Discord: daily recap ({idx + 1}/{len(chunks)}) posted.")
            else:
                print(f"  Discord: daily recap returned {resp.status_code} -- {resp.text}")
        except Exception as e:
            print(f"  Discord: failed to post daily recap -- {e}")


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--results" in sys.argv:
        enter_results()
    elif "--auto-results" in sys.argv:
        # Optional: pass a specific date as the next arg (YYYY-MM-DD)
        date_arg = next((a for a in sys.argv if re.match(r"\d{4}-\d{2}-\d{2}", a)), None)
        auto_results(date_arg)
    elif "--report" in sys.argv:
        performance_report()
    else:
        run_slate("kenpom_raw.txt")
