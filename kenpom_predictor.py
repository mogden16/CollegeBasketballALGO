"""
KenPom + T-Rank NCAA Basketball Predictor
-------------------------------------------
Workflow:
  1. Paste KenPom table into kenpom_raw.txt (weekly refresh)
  2. Paste Barttorvik T-Rank table into barttorvik_raw.txt (weekly refresh)
     (select-all from barttorvik.com/trank.php and paste as-is)
  3. Run this script to generate today's predictions + Discord alerts
  4. Use slate_results.py for results entry, checking, and reports

Dependencies:
  pip install requests thefuzz python-Levenshtein python-dotenv
"""

import os
import csv
import re
import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from thefuzz import process
from pathlib import Path
from dotenv import load_dotenv
from team_name_utils import normalize_team_name

load_dotenv()


# ══════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════
ODDS_API_KEY    = os.getenv("ODDS_API_KEY")   # Free at the-odds-api.com
ODDS_BOOK       = "draftkings"          # draftkings, fanduel, betmgm, etc.
EDGE_THRESHOLD  = 3.0                   # Flag if model vs line differs >= this
FUZZY_THRESHOLD = 75                    # Min fuzzy match score (0-100)

TEAM_ALIASES = {
    "uconn": "Connecticut",
    "u conn": "Connecticut",
    "unc": "North Carolina",
    "st johns": "St. John's",
    "saint johns": "St. John's",
    "iowa state": "Iowa St.",
    "michigan state": "Michigan St.",
}

# ── Tournament mode ────────────────────────────────────
# When True, fuzzy matching is constrained to TOURNAMENT_2026_TEAMS only,
# which prevents wrong-team matches across the full ~360-team KenPom dataset.
TOURNAMENT_MODE = True

# Maps ESPN shortDisplayName (lowercased) → KenPom canonical team name.
# Covers all 68 teams in the 2026 NCAA Tournament (64 bracket + 4 First Four).
# VERIFY these KenPom names match your kenpom_raw.txt exactly — common gotchas
# are period style (e.g. "Iowa St." vs "Iowa State") and FL/OH suffixes.
TOURNAMENT_2026_TEAMS: dict[str, str] = {
    # ── EAST (1-seed: Duke) ──────────────────────────────
    "duke":                 "Duke",
    "uconn":                "Connecticut",
    "connecticut":          "Connecticut",
    "michigan st":          "Michigan St.",
    "michigan state":       "Michigan St.",
    "kansas":               "Kansas",
    "st. john's":           "St. John's",
    "st. johns":            "St. John's",
    "louisville":           "Louisville",
    "ucla":                 "UCLA",
    "ohio st":              "Ohio St.",
    "ohio state":           "Ohio St.",
    "tcu":                  "TCU",
    "ucf":                  "UCF",
    "south florida":        "South Florida",
    "northern iowa":        "Northern Iowa",
    "n. iowa":              "Northern Iowa",
    "cal baptist":          "Cal Baptist",
    "california baptist":   "Cal Baptist",
    "n. dakota st":         "N. Dakota St.",
    "north dakota st":      "N. Dakota St.",
    "north dakota state":   "N. Dakota St.",
    "furman":               "Furman",
    "siena":                "Siena",

    # ── WEST (1-seed: Arizona) ───────────────────────────
    "arizona":              "Arizona",
    "purdue":               "Purdue",
    "gonzaga":              "Gonzaga",
    "arkansas":             "Arkansas",
    "wisconsin":            "Wisconsin",
    "byu":                  "BYU",
    "miami":                "Miami FL",   # ESPN uses "Miami" for Florida — must NOT match Miami OH
    "miami (fl)":           "Miami FL",
    "miami fl":             "Miami FL",
    "villanova":            "Villanova",
    "utah st":              "Utah St.",
    "utah state":           "Utah St.",
    "missouri":             "Missouri",
    "texas":                "Texas",
    "nc state":             "N.C. State",
    "n.c. state":           "N.C. State",
    "high point":           "High Point",
    "hawaii":               "Hawaii",
    "kennesaw st":          "Kennesaw St.",
    "kennesaw state":       "Kennesaw St.",
    "queens":               "Queens",   # KenPom uses "Queens (NC)" — verify
    "long island":          "LIU",

    # ── SOUTH (1-seed: Florida) ──────────────────────────
    "florida":              "Florida",
    "houston":              "Houston",
    "illinois":             "Illinois",
    "nebraska":             "Nebraska",
    "vanderbilt":           "Vanderbilt",
    "north carolina":       "North Carolina",
    "saint mary's":         "Saint Mary's",
    "st. mary's":           "Saint Mary's",
    "clemson":              "Clemson",
    "iowa":                 "Iowa",
    "texas a&m":            "Texas A&M",
    "vcu":                  "VCU",
    "mcneese":              "McNeese St.",   # ESPN drops "St." — verify KenPom spelling
    "mcneese st":           "McNeese St.",
    "mcneese state":        "McNeese St.",
    "troy":                 "Troy",
    "penn":                 "Penn",
    "idaho":                "Idaho",
    "prairie view a&m":     "Prairie View A&M",  # KenPom typically uses "Prairie View"
    "prairie view":         "Prairie View A&M",
    "lehigh":               "Lehigh",

    # ── MIDWEST (1-seed: Michigan) ───────────────────────
    "michigan":             "Michigan",
    "iowa st":              "Iowa St.",
    "virginia":             "Virginia",
    "alabama":              "Alabama",
    "texas tech":           "Texas Tech",
    "tennessee":            "Tennessee",
    "kentucky":             "Kentucky",
    "georgia":              "Georgia",
    "saint louis":          "Saint Louis",
    "santa clara":          "Santa Clara",
    "miami (oh)":           "Miami OH",      # ESPN uses "Miami (OH)" for Ohio — must NOT match Miami FL
    "miami oh":             "Miami OH",
    "smu":                  "SMU",
    "akron":                "Akron",
    "hofstra":              "Hofstra",
    "wright st":            "Wright St.",
    "wright state":         "Wright St.",
    "tennessee st":         "Tennessee St.",
    "tennessee state":      "Tennessee St.",
    "howard":               "Howard",
}

# Discord webhook (set via environment variable)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")

# Log files
PREDICTIONS_LOG = "predictions_log.csv"
RESULTS_LOG     = "results_log.csv"
BARTTORVIK_FILE = "barttorvik_raw.txt"

# Model constants
AVG_TEMPO_2026     = 68.4
LAMBDA             = 0.8296
TEMPO_SCALE        = 1.2398
TEMPO_EXP          = 0.48
TEMPO_LEAGUE_EXP   = 0.04
HCA                = 3.1453

# ══════════════════════════════════════════════════════
# DATA STRUCTURES
# ══════════════════════════════════════════════════════
@dataclass
class Team:
    name: str
    adj_o: float
    adj_d: float
    adj_t: float
    sos_netrtg: float | None = None
    ncsos_netrtg: float | None = None

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

    def _to_float(value: str) -> float | None:
        cleaned = (value or "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None

    def _clean_team_fragment(raw_team: str) -> str:
        # KenPom's pasted text often appends a seed or annotation immediately
        # before the W-L token (e.g. "Duke 1"). Remove trailing numeric-only
        # tokens but preserve legitimate punctuation/spaces inside team names.
        cleaned = re.sub(r"\s+", " ", raw_team.strip())
        cleaned = re.sub(r"\s+\d+$", "", cleaned)
        cleaned = re.sub(r"^\d+\s*", "", cleaned)
        return cleaned.strip()

    with open(filepath, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            parts = [part.strip() for part in line.split("\t") if part.strip()]
            if not parts or not parts[0].isdigit():
                continue

            record_idx = next((i for i, token in enumerate(parts) if re.fullmatch(r"\d{1,2}-\d{1,2}", token)), None)
            if record_idx is None or record_idx < 2:
                continue

            team_name = _clean_team_fragment(parts[1])
            metric_tokens = parts[record_idx + 1:]
            if len(metric_tokens) < 16:
                continue

            # After the W-L anchor, the pasted KenPom layout contains:
            # NetRtg, AdjO, AdjO_rk, AdjD, AdjD_rk, AdjT, AdjT_rk, Luck,
            # Luck_rk, SOS NetRtg, SOS_rk, SOS ORtg, SOS ORtg_rk,
            # SOS DRtg, SOS DRtg_rk, NCSOS NetRtg, NCSOS_rk.
            adj_o = _to_float(metric_tokens[1])
            adj_d = _to_float(metric_tokens[3])
            adj_t = _to_float(metric_tokens[5])
            sos_netrtg = _to_float(metric_tokens[9])
            ncsos_netrtg = _to_float(metric_tokens[15])

            if team_name and adj_o is not None and adj_d is not None and adj_t is not None:
                teams[team_name] = Team(
                    name=team_name,
                    adj_o=adj_o,
                    adj_d=adj_d,
                    adj_t=adj_t,
                    sos_netrtg=sos_netrtg,
                    ncsos_netrtg=ncsos_netrtg,
                )
    return teams


def build_kenpom_sos_lookup(teams: dict[str, Team]) -> dict[str, dict[str, float | str | None]]:
    """Build a normalized team-keyed SOS lookup from parsed KenPom teams."""
    lookup = {}
    for team in teams.values():
        lookup[normalize_team_name(team.name)] = {
            "team": team.name,
            "sos_netrtg": team.sos_netrtg,
            "ncsos_netrtg": team.ncsos_netrtg,
        }
    return lookup


def get_matchup_sos_features(home: Team | None, away: Team | None) -> dict[str, float | None]:
    """Build SOS features for a matchup, safely handling missing values."""
    home_sos = home.sos_netrtg if home else None
    away_sos = away.sos_netrtg if away else None
    if home_sos is None or away_sos is None:
        return {
            "home_sos": home_sos,
            "away_sos": away_sos,
            "sos_diff": None,
            "abs_sos_diff": None,
            "avg_sos": None,
        }
    sos_diff = round(away_sos - home_sos, 2)
    return {
        "home_sos": home_sos,
        "away_sos": away_sos,
        "sos_diff": sos_diff,
        "abs_sos_diff": round(abs(sos_diff), 2),
        "avg_sos": round((away_sos + home_sos) / 2, 2),
    }


def optional_sos_adjusted_margin(base_margin: float, sos_diff: float | None, alpha: float = 0.0) -> float:
    """Optional future hook for experiments; disabled by default with alpha=0."""
    if sos_diff is None:
        return round(base_margin, 1)
    return round(base_margin + alpha * sos_diff, 1)

# ══════════════════════════════════════════════════════
# STEP 1b: LOAD BARTTORVIK T-RANK
# ══════════════════════════════════════════════════════
def parse_barttorvik(filepath: str) -> dict[str, Team]:
    """
    Parse raw Barttorvik T-Rank copy-paste from barttorvik.com/trank.php.

    The site produces a staggered multi-line format when copy-pasted, where
    each stat and its rank appear on interleaved lines.

    Handles two copy-paste variants:
      Standard:  {Rk}\\t{Team}\\t{Conf}\\t{G}\\t{Rec}   (5+ tab fields)
      Annotated: {Rk}\\t{Team}                            (2 tab fields)
                 {annotation}\\t{Conf}\\t{G}\\t{Rec}      (next line has game info)

    In both cases, AdjOE appears on the line after the record line,
    and subsequent stats follow the same staggered pattern:
      data+0:   {AdjOE}
      data+1:   {AdjOE_rank}\\t{AdjDE}
      ...
      data+17:  {3PRD_rank}\\t{AdjT}
      data+18:  {AdjT_rank}\\t{WAB}
      data+19:  {WAB_rank}

    Select-all the table on barttorvik.com/trank.php and paste into
    barttorvik_raw.txt. No editing needed.
    """
    teams = {}
    with open(filepath, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]

    i = 0
    while i < len(lines):
        parts = lines[i].strip().split("\t")

        # Detect team header: first field is a rank, second is a team name
        if (len(parts) < 2
                or not parts[0].strip().isdigit()
                or not parts[1].strip()
                or not parts[1].strip()[0].isalpha()):
            i += 1
            continue

        team_name = parts[1].strip()

        # Standard format: 5+ fields with record containing "-"
        if len(parts) >= 5 and "-" in parts[4].strip():
            data_start = i + 1
        # Annotated format: short line, next line has annotation + conf + G + rec
        elif len(parts) < 5 and i + 1 < len(lines):
            next_parts = lines[i + 1].strip().split("\t")
            if len(next_parts) >= 4 and "-" in next_parts[-1].strip():
                data_start = i + 2
            else:
                i += 1
                continue
        else:
            i += 1
            continue

        try:
            adj_o = float(lines[data_start].strip())
            adj_d = float(lines[data_start + 1].strip().split("\t")[1])
            adj_t = float(lines[data_start + 17].strip().split("\t")[1])
            teams[team_name] = Team(name=team_name, adj_o=adj_o, adj_d=adj_d, adj_t=adj_t)
        except (ValueError, IndexError):
            pass

        i += 1
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


def fuzzy_lookup(query: str, team_dict: dict[str, Team], threshold: int = FUZZY_THRESHOLD) -> Team | None:
    """Exact-match first lookup with conservative alias/fuzzy fallback."""
    query_norm = normalize_team_name(query)
    lookup = {normalize_team_name(name): team for name, team in team_dict.items()}

    # 1) exact normalized match first
    if query_norm in lookup:
        return lookup[query_norm]

    # 2) alias map fallback
    alias = TEAM_ALIASES.get(query_norm)
    if alias and normalize_team_name(alias) in lookup:
        return lookup[normalize_team_name(alias)]

    # 3) conservative fuzzy fallback — run on normalized keys/query for consistency
    norm_keys = list(lookup.keys())
    match_norm, score = process.extractOne(query_norm, norm_keys)
    if score >= threshold:
        return lookup[match_norm]
    return None

# ══════════════════════════════════════════════════════
# STEP 2: PULL TODAY'S MATCHUPS FROM ESPN
# ══════════════════════════════════════════════════════
def get_matchups_for_date(date_str: str | None = None) -> list[Matchup]:
    """
    Pull NCAAB schedule for a date (YYYY-MM-DD) from ESPN's unofficial API.
    No API key required.
    """
    selected = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yyyymmdd = selected.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={yyyymmdd}&groups=50"
    
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


def fetch_scores_for_date(date_str: str) -> dict[tuple[str, str], tuple[float, float]]:
    """
    Fetch final scores from ESPN for a given date (YYYY-MM-DD).
    Returns dict: (home_name, away_name) -> (home_score, away_score)
    Only includes games with status 'STATUS_FINAL'.
    """
    espn_date = date_str.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard?dates={espn_date}&groups=50"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ESPN fetch failed for {date_str}: {e}")
        return {}

    scores = {}
    for event in data.get("events", []):
        comp = event.get("competitions", [{}])[0]
        status = comp.get("status", {}).get("type", {}).get("name", "")
        if status != "STATUS_FINAL":
            continue

        competitors = comp.get("competitors", [])
        if len(competitors) < 2:
            continue

        home_name = away_name = None
        home_score = away_score = 0
        for c in competitors:
            team = c.get("team", {})
            name = team.get("shortDisplayName") or team.get("displayName", "")
            score = float(c.get("score", 0))
            if c.get("homeAway") == "home":
                home_name, home_score = name, score
            else:
                away_name, away_score = name, score

        if home_name and away_name:
            scores[(home_name, away_name)] = (home_score, away_score)

    return scores


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
    normalized_to_team: dict[str, str] = {}
    team_to_game: dict[str, tuple[str, str]] = {}
    for (h, a) in odds_lookup:
        api_team_names.append(h)
        api_team_names.append(a)
        normalized_to_team[normalize_team_name(h)] = h
        normalized_to_team[normalize_team_name(a)] = a
        team_to_game[h] = (h, a)
        team_to_game[a] = (h, a)

    for m in matchups:
        if not api_team_names:
            break

        # Step 1: find the API team that best matches ESPN's home team
        home_norm = normalize_team_name(m.home)
        home_match = normalized_to_team.get(home_norm)
        if home_match is None:
            home_match, home_score = process.extractOne(m.home, api_team_names)
            if home_score < FUZZY_THRESHOLD:
                continue

        game_key = team_to_game[home_match]
        api_h, api_a = game_key
        entry = odds_lookup[game_key]

        # Step 2: verify the away team also matches the other team in that game
        other_api_team = api_a if home_match == api_h else api_h
        away_norm = normalize_team_name(m.away)
        if normalize_team_name(other_api_team) != away_norm:
            _, away_score = process.extractOne(m.away, [other_api_team])
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

    # Build each team's matchup efficiency from its own offense and the opponent's
    # defense, then apply the lambda dampening factor to that matchup average.
    # This keeps lambda in the model without reverting to a shared tempo input.
    eff_home = ((home.adj_o + away.adj_d) / 2) * LAMBDA
    eff_away = ((away.adj_o + home.adj_d) / 2) * LAMBDA

    # AdjO / AdjD are per-100-possession metrics, so each team now scales its own
    # matchup efficiency by its own adjusted tempo instead of a shared averaged tempo.
    pace_home = TEMPO_SCALE * home.adj_t
    pace_away = TEMPO_SCALE * away.adj_t
    pts_home = pace_home * eff_home / 100
    pts_away = pace_away * eff_away / 100
    total    = pts_home + pts_away
    spread   = -((pts_home - pts_away) + hca)  # Negative = home favored

    # Preserve the legacy single tempo field for downstream consumers as a display-only
    # summary, even though team scoring now uses each side's own adjusted tempo.
    display_tempo = (pace_home + pace_away) / 2
    return {
        "home_score": round(pts_home, 1),
        "away_score": round(pts_away, 1),
        "total":      round(total, 1),
        "spread":     round(spread, 1),
        "tempo":      round(display_tempo, 1),
    }

# ══════════════════════════════════════════════════════
# STEP 5: RUN FULL SLATE + FLAG EDGES
# ══════════════════════════════════════════════════════
def run_slate(kenpom_file: str = "kenpom_raw.txt", run_date: str | None = None):
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
    matchups = get_matchups_for_date(run_date)
    if not matchups:
        print("  No games found for today.")
        return

    # Attach lines
    matchups = get_odds(matchups)

    entries = []
    no_data = []

    # In tournament mode, constrain lookup to the 68 tournament teams only.
    # Build the filtered dicts once outside the loop for efficiency.
    if TOURNAMENT_MODE:
        tournament_values = set(TOURNAMENT_2026_TEAMS.values())
        tournament_kp = {k: v for k, v in kp_teams.items() if k in tournament_values}
        tournament_bt = {k: v for k, v in bt_teams.items() if k in tournament_values} if bt_teams else {}
        print(f"  Tournament mode: {len(tournament_kp)} KenPom teams matched to bracket.")
        unmatched = tournament_values - set(tournament_kp.keys())
        if unmatched:
            print(f"  WARNING: These tournament teams not found in KenPom data -- check name spelling:")
            for t in sorted(unmatched):
                print(f"    {t}")

    for m in matchups:
        # Resolve ESPN display name → KenPom name, with tournament map taking
        # priority to prevent mismatches against the full ~360-team dataset.
        if TOURNAMENT_MODE:
            kp_home_name = TOURNAMENT_2026_TEAMS.get(m.home.lower())
            kp_away_name = TOURNAMENT_2026_TEAMS.get(m.away.lower())
            kp_home = kp_teams.get(kp_home_name) if kp_home_name else fuzzy_lookup(m.home, tournament_kp)
            kp_away = kp_teams.get(kp_away_name) if kp_away_name else fuzzy_lookup(m.away, tournament_kp)
        else:
            kp_home_name = kp_away_name = None
            kp_home = fuzzy_lookup(m.home, kp_teams)
            kp_away = fuzzy_lookup(m.away, kp_teams)

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
            if TOURNAMENT_MODE:
                bt_home = bt_teams.get(kp_home_name) if kp_home_name else fuzzy_lookup(m.home, tournament_bt)
                bt_away = bt_teams.get(kp_away_name) if kp_away_name else fuzzy_lookup(m.away, tournament_bt)
            else:
                bt_home = fuzzy_lookup(m.home, bt_teams)
                bt_away = fuzzy_lookup(m.away, bt_teams)
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

        # Flag edge games
        is_spread_edge = kp_spread_edge is not None and abs(kp_spread_edge) >= EDGE_THRESHOLD
        is_total_edge  = kp_total_edge  is not None and abs(kp_total_edge)  >= EDGE_THRESHOLD

        # Confidence: HIGH when both KenPom and T-Rank agree on edge direction
        confidence = ""
        if bt_result and is_spread_edge and bt_spread_edge is not None:
            same_spread_dir = (kp_spread_edge > 0) == (bt_spread_edge > 0)
            bt_also_edge    = abs(bt_spread_edge) >= EDGE_THRESHOLD
            if same_spread_dir and bt_also_edge:
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
            "is_edge": is_spread_edge,  # Only spread edges trigger edge flag (not totals)
            "is_spread_edge": is_spread_edge,
            "is_total_edge": is_total_edge,
            "confidence": confidence,
        }
        entry.update(get_matchup_sos_features(kp_home, kp_away))

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
    "away_sos", "home_sos", "sos_diff", "abs_sos_diff", "avg_sos",
    "kp_spread_edge", "kp_total_edge", "bt_spread_edge", "bt_total_edge",
    "is_edge", "confidence"
]

def log_predictions(entries: list[dict]):
    """Append today's predictions to predictions_log.csv."""
    log_path = Path(PREDICTIONS_LOG)
    write_header = not log_path.exists() or log_path.stat().st_size == 0

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
                "away_sos":         e["away_sos"] if e["away_sos"] is not None else "",
                "home_sos":         e["home_sos"] if e["home_sos"] is not None else "",
                "sos_diff":         e["sos_diff"] if e["sos_diff"] is not None else "",
                "abs_sos_diff":     e["abs_sos_diff"] if e["abs_sos_diff"] is not None else "",
                "avg_sos":          e["avg_sos"] if e["avg_sos"] is not None else "",
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

    # Post only individual edge game alerts.
    for i, e in enumerate(edge_games):
        kp      = e["result"]
        is_high = e["confidence"] == "HIGH"
        title   = f"{'⚡⚡ HIGH CONF  ' if is_high else '⚡  '}{e['away']} @ {e['home']}"
        fields: list[dict] = []

        kp_body = (
            f"`{e['home']}` **{kp['home_score']}**  vs  `{e['away']}` **{kp['away_score']}**\n"
            f"Spread: **{e['model_fav']} -{e['model_line']:.1f}**  |  Total: **{kp['total']}**"
        )
        fields.append({"name": "KenPom", "value": kp_body, "inline": False})

        if e["bt_result"]:
            bt     = e["bt_result"]
            bt_fav = e["home"] if bt["spread"] < 0 else e["away"]
            bt_val = (
                f"`{e['home']}` **{bt['home_score']}**  vs  `{e['away']}` **{bt['away_score']}**\n"
                f"Spread: **{bt_fav} -{abs(bt['spread']):.1f}**  |  Total: **{bt['total']}**"
            )
            fields.append({"name": "T-Rank", "value": bt_val, "inline": False})

        if e["vegas_spread"] is not None:
            v_val = f"Spread: **{e['vegas_fav']} -{e['vegas_line']:.1f}**  |  Total: **{e['vegas_total']}**"
            fields.append({"name": "Vegas Line", "value": v_val, "inline": False})

        edge_parts: list[str] = []
        if e["is_spread_edge"]:
            direction = e["home"] if e["spread_edge"] > 0 else e["away"]
            edge_parts.append(f"Spread: **{direction}** by {abs(e['spread_edge']):.1f} pts over Vegas")
        if e["bt_spread_edge"] is not None and abs(e["bt_spread_edge"]) >= EDGE_THRESHOLD:
            direction = e["home"] if e["bt_spread_edge"] > 0 else e["away"]
            edge_parts.append(f"T-Rank: **{direction}** by {abs(e['bt_spread_edge']):.1f} pts over Vegas")
        if e["is_total_edge"]:
            direction = "OVER" if e["total_edge"] > 0 else "UNDER"
            edge_parts.append(f"Total: **{direction}** by {abs(e['total_edge']):.1f} pts")
        if edge_parts:
            fields.append({"name": "Edge Summary", "value": "\n".join(edge_parts), "inline": False})

        embed: dict = {
            "title":  title,
            "color":  0xFFD700 if is_high else 0xFF4500,
            "fields": fields,
        }
        if i == len(edge_games) - 1:
            embed["footer"] = {"text": footer_text}

        _post(
            {"embeds": [embed]},
            f"edge game {i + 1}/{len(edge_games)} ({e['away']} @ {e['home']})",
        )


# ══════════════════════════════════════════════════════
# QUICK SCORE PREDICTION (two-team lookup)
# ══════════════════════════════════════════════════════
def quick_predict(kenpom_file: str = "kenpom_raw.txt"):
    """Prompt for two teams and a neutral-court flag, then display the prediction."""
    kp_teams = parse_kenpom(kenpom_file)
    bt_teams = load_barttorvik()

    home_name = input("Home team: ").strip()
    away_name = input("Away team: ").strip()
    neutral_input = input("Neutral court? (Y/N): ").strip().upper()
    neutral = neutral_input == "Y"

    kp_home = fuzzy_lookup(home_name, kp_teams)
    kp_away = fuzzy_lookup(away_name, kp_teams)

    if not kp_home:
        print(f"  Could not find '{home_name}' in KenPom data.")
        return
    if not kp_away:
        print(f"  Could not find '{away_name}' in KenPom data.")
        return

    kp_result = predict_game(kp_home, kp_away, neutral=neutral)

    bt_result = None
    if bt_teams:
        bt_home = fuzzy_lookup(home_name, bt_teams)
        bt_away = fuzzy_lookup(away_name, bt_teams)
        if bt_home and bt_away:
            bt_result = predict_game(bt_home, bt_away, neutral=neutral)

    model_fav = kp_home.name if kp_result["spread"] < 0 else kp_away.name
    model_line = abs(kp_result["spread"])

    entry = {
        "home": kp_home.name, "away": kp_away.name,
        "neutral": neutral,
        "result": kp_result,
        "bt_result": bt_result,
        "model_fav": model_fav, "model_line": model_line,
    }

    # ── Display ──
    venue = " [N]" if neutral else ""
    print(f"\n{'═'*50}")
    print(f"  {kp_away.name} @ {kp_home.name}{venue}")
    print(f"{'═'*50}")
    print(f"  KenPom  →  {kp_home.name} {kp_result['home_score']}  -  {kp_away.name} {kp_result['away_score']}")
    print(f"  Spread: {model_fav} -{model_line}")
    print(f"  Total:  {kp_result['total']}")
    if bt_result:
        bt_fav = kp_home.name if bt_result["spread"] < 0 else kp_away.name
        bt_line = abs(bt_result["spread"])
        print(f"\n  T-Rank  →  {kp_home.name} {bt_result['home_score']}  -  {kp_away.name} {bt_result['away_score']}")
        print(f"  Spread: {bt_fav} -{bt_line}")
        print(f"  Total:  {bt_result['total']}")
    print(f"{'═'*50}\n")

    return entry


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "predict":
        quick_predict()
    else:
        run_slate("kenpom_raw.txt")
