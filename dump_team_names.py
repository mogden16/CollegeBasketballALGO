"""
dump_team_names.py
------------------
Diagnostic: shows how every ESPN team name maps to KenPom, Barttorvik,
and The Odds API so you can spot mismatches and fill in team_aliases.txt.

For each ESPN team it prints:
  KenPom     best-match name + fuzzy score
  Barttorvik best-match name + fuzzy score
  Odds API   best-match name + fuzzy score
  *** WARN *** on any row where the score is below FUZZY_THRESHOLD

When mismatches exist it writes a starter team_aliases.txt with one
section per source ([kenpom], [barttorvik], [odds_api]).  Edit the
right-hand side of each flagged line to the exact name used by that
source, then re-run the main predictor — it will use the exact aliases
instead of fuzzy-matching.

Usage:
    python dump_team_names.py
"""

import os
import sys
import requests
from datetime import datetime, timezone
from thefuzz import process
from dotenv import load_dotenv
from pathlib import Path

# Re-use the parse functions from the main script (safe because of __main__ guard)
from kenpom_predictor import (
    parse_kenpom,
    parse_barttorvik,
    ODDS_API_KEY,
    ODDS_BOOK,
    FUZZY_THRESHOLD,
)

load_dotenv()

KENPOM_FILE    = "kenpom_raw.txt"
BARTTORVIK_FILE = "barttorvik_raw.txt"
ALIAS_FILE      = "team_aliases.txt"


# ── Data fetchers ─────────────────────────────────────────────────────────────

def fetch_espn_teams() -> list[str]:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/"
        f"mens-college-basketball/scoreboard?dates={today}&groups=50"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    names = []
    for event in data.get("events", []):
        for comp in event.get("competitions", [{}]):
            for team in comp.get("competitors", []):
                name = team.get("team", {}).get("displayName", "")
                if name:
                    names.append(name)
    return names


def fetch_odds_api_teams() -> list[str]:
    if not ODDS_API_KEY:
        print("  WARNING: ODDS_API_KEY not set — skipping Odds API column.")
        return []
    url = (
        "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=us&markets=spreads&bookmakers={ODDS_BOOK}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    teams = []
    for game in resp.json():
        for key in ("home_team", "away_team"):
            name = game.get(key, "")
            if name:
                teams.append(name)
    return teams


# ── Helpers ───────────────────────────────────────────────────────────────────

def best_match(query: str, candidates: list[str]) -> tuple[str, int]:
    if not candidates:
        return ("(no data)", 0)
    return process.extractOne(query, candidates)


def status(score: int) -> str:
    return "OK" if score >= FUZZY_THRESHOLD else "*** WARN ***"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Load local data files
    kp_names = list(parse_kenpom(KENPOM_FILE).keys()) if Path(KENPOM_FILE).exists() else []
    bt_names = list(parse_barttorvik(BARTTORVIK_FILE).keys()) if Path(BARTTORVIK_FILE).exists() else []

    print(f"  KenPom    : {len(kp_names)} teams loaded from {KENPOM_FILE}")
    print(f"  Barttorvik: {len(bt_names)} teams loaded from {BARTTORVIK_FILE}")

    print("\nFetching ESPN games...")
    try:
        espn_names = fetch_espn_teams()
        print(f"  {len(espn_names)} ESPN team entries found.\n")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    print("Fetching Odds API teams...")
    try:
        odds_names = fetch_odds_api_teams()
        print(f"  {len(odds_names)} Odds API team entries found.\n")
    except Exception as e:
        print(f"  ERROR: {e}")
        odds_names = []

    # Collect warnings per source: {source: {espn_name: best_guess}}
    warns: dict[str, dict[str, str]] = {"kenpom": {}, "barttorvik": {}, "odds_api": {}}

    COL = 32
    print(f"{'ESPN name':<{COL}}  {'Source':<10} {'Best match':<{COL}} {'Score':>5}  Status")
    print(f"{'─'*COL}  {'─'*10} {'─'*COL} {'─'*5}  {'─'*12}")

    for espn_name in sorted(set(espn_names)):
        printed_name = False

        for source_label, source_key, name_list in [
            ("KenPom",     "kenpom",     kp_names),
            ("Barttorvik", "barttorvik", bt_names),
            ("Odds API",   "odds_api",   odds_names),
        ]:
            match, score = best_match(espn_name, name_list)
            st = status(score)
            label_col = espn_name if not printed_name else ""
            print(f"{label_col:<{COL}}  {source_label:<10} {match:<{COL}} {score:>5}  {st}")
            printed_name = True
            if score < FUZZY_THRESHOLD:
                warns[source_key][espn_name] = match
        print()

    # ── Write/update starter alias file ──────────────────────────────────────
    total_warns = sum(len(v) for v in warns.values())

    if total_warns == 0:
        print("All ESPN names matched all sources above the threshold.")
        print("No team_aliases.txt changes needed.")
        return

    print(f"{'═'*80}")
    print(f"  {total_warns} unmatched name(s) found across all sources.")
    print(f"  Writing starter entries to: {ALIAS_FILE}")
    print(f"  Edit each right-hand side to the EXACT name used by that data source.")
    print(f"{'═'*80}\n")

    with open(ALIAS_FILE, "w") as f:
        f.write("# team_aliases.txt\n")
        f.write("# Maps ESPN team names to the exact name each data source uses.\n")
        f.write("# Format:  espn_name = source_name\n")
        f.write("# Sections: [kenpom]  [barttorvik]  [odds_api]\n")
        f.write("# Lines starting with # are comments.  Blank lines are ignored.\n\n")

        for section, label in [("kenpom", "KenPom"), ("barttorvik", "Barttorvik"), ("odds_api", "Odds API")]:
            if not warns[section]:
                continue
            f.write(f"[{section}]\n")
            for espn_name, best_guess in sorted(warns[section].items()):
                f.write(f"{espn_name} = {best_guess}  # <-- verify: correct {label} name\n")
            f.write("\n")

    print(f"Wrote {ALIAS_FILE}. Edit it, then run: python kenpom_predictor.py")


if __name__ == "__main__":
    main()
