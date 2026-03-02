"""
dump_team_names.py
------------------
One-shot diagnostic: fetches today's ESPN NCAAB games and today's Odds API
games, then prints a side-by-side comparison with fuzzy match scores.

For every ESPN matchup it shows:
  - the ESPN home / away names
  - the best-matching Odds API game
  - the individual fuzzy scores for home and away
  - a WARN flag when either score is below FUZZY_THRESHOLD

Run this on a game day, then copy the output into team_aliases.txt (see
format at the bottom of this file) for any pairs marked WARN.

Usage:
    python dump_team_names.py
"""

import os
import requests
from datetime import datetime, timezone
from thefuzz import process
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY    = os.getenv("ODDS_API_KEY")
ODDS_BOOK       = "draftkings"
FUZZY_THRESHOLD = 75

# ── ESPN ──────────────────────────────────────────────────────────────────────
def fetch_espn_games() -> list[tuple[str, str]]:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    url = (
        "https://site.api.espn.com/apis/site/v2/sports/basketball/"
        f"mens-college-basketball/scoreboard?dates={today}&groups=50"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    games = []
    for event in data.get("events", []):
        home = away = None
        for comp in event.get("competitions", [{}]):
            for team in comp.get("competitors", []):
                name = team.get("team", {}).get("displayName", "")
                if team.get("homeAway") == "home":
                    home = name
                else:
                    away = name
        if home and away:
            games.append((home, away))
    return games


# ── Odds API ──────────────────────────────────────────────────────────────────
def fetch_api_teams() -> list[str]:
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set.")
        return []
    url = (
        "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=us&markets=spreads&bookmakers={ODDS_BOOK}"
    )
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    teams = []
    for game in data:
        h = game.get("home_team", "")
        a = game.get("away_team", "")
        if h:
            teams.append(h)
        if a:
            teams.append(a)
    return teams


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Fetching ESPN games...")
    espn_games = fetch_espn_games()
    print(f"  {len(espn_games)} games found on ESPN.\n")

    print("Fetching Odds API teams...")
    api_teams = fetch_api_teams()
    print(f"  {len(api_teams) // 2} games found on Odds API ({len(api_teams)} team entries).\n")

    if not api_teams:
        print("No Odds API data — check your ODDS_API_KEY.")
        return

    print(f"{'ESPN Home':<35} {'Best API Match':<35} {'Score':>6}  Status")
    print(f"{'─'*35} {'─'*35} {'─'*6}  {'─'*6}")

    warn_pairs: list[tuple[str, str]] = []

    for espn_home, espn_away in espn_games:
        for espn_name in (espn_home, espn_away):
            api_match, score = process.extractOne(espn_name, api_teams)
            status = "OK" if score >= FUZZY_THRESHOLD else "*** WARN ***"
            print(f"  {espn_name:<33} {api_match:<35} {score:>6}  {status}")
            if score < FUZZY_THRESHOLD:
                warn_pairs.append((espn_name, api_match))
        print()

    # ── Write starter alias file for WARN pairs ───────────────────────────────
    alias_path = "team_aliases.txt"
    if warn_pairs:
        print(f"\n{'═'*80}")
        print(f"  {len(warn_pairs)} unmatched name(s). Starter alias file written to: {alias_path}")
        print(f"  Edit the right-hand side of each line to the correct Odds API name.")
        print(f"{'═'*80}\n")
        with open(alias_path, "w") as f:
            f.write("# team_aliases.txt\n")
            f.write("# Format: espn_name = odds_api_name\n")
            f.write("# One mapping per line. Lines starting with # are comments.\n")
            f.write("# The right-hand side must exactly match the name used by The Odds API.\n\n")
            for espn_name, best_guess in warn_pairs:
                f.write(f"{espn_name} = {best_guess}  # <-- verify/correct this\n")
    else:
        print("\nAll ESPN names matched the Odds API above the threshold — no aliases needed today.")
        print("(If you still see wrong spreads, lower FUZZY_THRESHOLD in this script and re-run.)")


if __name__ == "__main__":
    main()
