#!/usr/bin/env python3
"""
Standalone diagnostic script for The Odds API NCAAB endpoint.

Checks connectivity, quota, and data availability for spreads/totals.
Runnable independently:  python diagnose_odds.py
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
PREFERRED_BOOK = "draftkings"


def main():
    if not ODDS_API_KEY or ODDS_API_KEY == "YOUR_API_KEY_HERE":
        print("ERROR: ODDS_API_KEY not set in .env")
        sys.exit(1)

    url = (
        "https://api.the-odds-api.com/v4/sports/basketball_ncaab/odds/"
        f"?apiKey={ODDS_API_KEY}&regions=us&markets=spreads,totals"
    )

    print(f"Fetching: {url.replace(ODDS_API_KEY, '***')}\n")

    try:
        resp = requests.get(url, timeout=15)
    except Exception as e:
        print(f"ERROR: Request failed -- {e}")
        sys.exit(1)

    # ── Response metadata ──
    remaining = resp.headers.get("x-requests-remaining", "?")
    used = resp.headers.get("x-requests-used", "?")
    print(f"HTTP status:         {resp.status_code}")
    print(f"x-requests-used:     {used}")
    print(f"x-requests-remaining:{remaining}")

    if resp.status_code != 200:
        print(f"\nERROR: Non-200 response.\nBody: {resp.text[:500]}")
        sys.exit(1)

    games = resp.json()
    print(f"Games returned:      {len(games)}\n")

    if not games:
        print("No games returned. The season may be over or no games are scheduled.")
        sys.exit(0)

    # ── Per-game breakdown ──
    has_spreads = 0
    has_totals = 0
    has_dk = 0

    print(f"{'Home':<30} {'Away':<30} {'Books':>5} {'Sprd':>5} {'Totl':>5} {'DK':>4}")
    print("-" * 90)

    for game in games:
        home = game.get("home_team", "?")
        away = game.get("away_team", "?")
        bookmakers = game.get("bookmakers", [])
        num_books = len(bookmakers)

        # Check what markets exist across all bookmakers
        game_has_spreads = False
        game_has_totals = False
        for bookie in bookmakers:
            for market in bookie.get("markets", []):
                if market["key"] == "spreads":
                    game_has_spreads = True
                if market["key"] == "totals":
                    game_has_totals = True

        if game_has_spreads:
            has_spreads += 1
        if game_has_totals:
            has_totals += 1

        # Check DraftKings specifically
        dk_spread = dk_total = None
        dk_books = [b for b in bookmakers if b.get("key") == PREFERRED_BOOK]
        game_has_dk = bool(dk_books)
        if game_has_dk:
            has_dk += 1
            dk = dk_books[0]
            for market in dk.get("markets", []):
                if market["key"] == "spreads":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == home:
                            dk_spread = outcome.get("point")
                if market["key"] == "totals":
                    for outcome in market["outcomes"]:
                        if outcome["name"] == "Over":
                            dk_total = outcome.get("point")

        sprd_flag = "Y" if game_has_spreads else "-"
        totl_flag = "Y" if game_has_totals else "-"
        dk_flag = "Y" if game_has_dk else "-"

        print(f"{home:<30} {away:<30} {num_books:>5} {sprd_flag:>5} {totl_flag:>5} {dk_flag:>4}", end="")
        if game_has_dk:
            print(f"  spread={dk_spread}  total={dk_total}", end="")
        print()

    # ── Summary ──
    n = len(games)
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"  {has_spreads}/{n} games have spreads")
    print(f"  {has_totals}/{n} games have totals")
    print(f"  {has_dk}/{n} games have {PREFERRED_BOOK} data")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
