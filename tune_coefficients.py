"""
Coefficient Tuner for KenPom Predictor
---------------------------------------
Optimizes LAMBDA and TEMPO_SCALE against completed game results using
Nelder-Mead minimization of RMSE. Optionally tunes HCA in a second pass.

Usage:
  python tune_coefficients.py
"""

import csv
import re
import math
import numpy as np
from pathlib import Path
from scipy.optimize import minimize

import kenpom_predictor
from kenpom_predictor import (
    Team, parse_kenpom, load_barttorvik, fuzzy_lookup,
    RESULTS_LOG, FUZZY_THRESHOLD,
)
from slate_results import RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS, _read_csv

KENPOM_FILE = "kenpom_raw.txt"
PREDICTOR_PATH  = Path("kenpom_predictor.py")
MATCHUP_TS_PATH = Path("cloudflare-app/src/matchup.ts")


# ══════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════
def load_games(teams: dict[str, Team]) -> list[dict]:
    """
    Load completed games from results_log.csv and match team names
    to Team objects. Returns list of dicts with keys:
      home, away (Team), home_score, away_score, neutral (bool)
    """
    if not Path(RESULTS_LOG).exists():
        print(f"ERROR: {RESULTS_LOG} not found.")
        return []

    rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS)
    games = []
    skipped = 0

    for row in rows:
        home_name = row.get("home_team", "").strip()
        away_name = row.get("away_team", "").strip()
        try:
            home_score = float(row["actual_home_score"])
            away_score = float(row["actual_away_score"])
        except (ValueError, KeyError):
            skipped += 1
            continue

        home_team = fuzzy_lookup(home_name, teams)
        away_team = fuzzy_lookup(away_name, teams)
        if not home_team or not away_team:
            skipped += 1
            continue

        # Cross-reference neutral flag from predictions_log if available
        neutral = False

        games.append({
            "home": home_team,
            "away": away_team,
            "home_score": home_score,
            "away_score": away_score,
            "neutral": neutral,
        })

    if skipped:
        print(f"  Skipped {skipped} rows (missing data or unmatched teams).")
    print(f"  Loaded {len(games)} completed games for tuning.\n")
    return games


# ══════════════════════════════════════════════════════
# PREDICTION WITH OVERRIDDEN COEFFICIENTS
# ══════════════════════════════════════════════════════
def predict_with_params(home: Team, away: Team, neutral: bool,
                        lam: float, tempo_scale: float, hca: float) -> tuple[float, float]:
    """Run predict_game logic with explicit coefficient values."""
    adj_hca = 0.0 if neutral else hca
    eff_home = ((home.adj_o + away.adj_d) / 2) * lam
    eff_away = ((away.adj_o + home.adj_d) / 2) * lam
    pts_home = (tempo_scale * home.adj_t) * eff_home / 100
    pts_away = (tempo_scale * away.adj_t) * eff_away / 100
    pts_home += adj_hca / 2
    pts_away -= adj_hca / 2
    return pts_home, pts_away


# ══════════════════════════════════════════════════════
# LOSS FUNCTIONS
# ══════════════════════════════════════════════════════
def rmse_score(games: list[dict], lam: float, tempo_scale: float, hca: float) -> float:
    """Compute RMSE across all predicted home and away scores vs actuals."""
    sse = 0.0
    n = 0
    for g in games:
        pred_h, pred_a = predict_with_params(
            g["home"], g["away"], g["neutral"], lam, tempo_scale, hca
        )
        sse += (pred_h - g["home_score"]) ** 2
        sse += (pred_a - g["away_score"]) ** 2
        n += 2
    return math.sqrt(sse / n) if n > 0 else float("inf")


def rmse_spread(games: list[dict], lam: float, tempo_scale: float, hca: float) -> float:
    """Compute RMSE of predicted spread vs actual margin."""
    sse = 0.0
    n = 0
    for g in games:
        pred_h, pred_a = predict_with_params(
            g["home"], g["away"], g["neutral"], lam, tempo_scale, hca
        )
        pred_spread = pred_h - pred_a
        actual_spread = g["home_score"] - g["away_score"]
        sse += (pred_spread - actual_spread) ** 2
        n += 1
    return math.sqrt(sse / n) if n > 0 else float("inf")


# ══════════════════════════════════════════════════════
# OPTIMIZATION
# ══════════════════════════════════════════════════════
def optimize_lambda_tempo(games: list[dict], hca: float) -> tuple[float, float, float]:
    """Optimize LAMBDA and TEMPO_SCALE with fixed HCA using Nelder-Mead."""
    def objective(x):
        lam, ts = x
        # Enforce bounds via penalty
        if not (0.1 <= lam <= 1.5) or not (0.7 <= ts <= 1.3):
            return 1e6
        return rmse_score(games, lam, ts, hca)

    result = minimize(
        objective,
        x0=[0.5, 1.0],
        method="Nelder-Mead",
        options={"xatol": 1e-5, "fatol": 1e-6, "maxiter": 2000},
    )
    return result.x[0], result.x[1], result.fun


def optimize_hca(games: list[dict], lam: float, tempo_scale: float) -> tuple[float, float]:
    """Optimize HCA independently with LAMBDA and TEMPO_SCALE locked."""
    def objective(x):
        h = x[0]
        if not (0.0 <= h <= 8.0):
            return 1e6
        return rmse_spread(games, lam, tempo_scale, h)

    result = minimize(
        objective,
        x0=[kenpom_predictor.HCA],
        method="Nelder-Mead",
        options={"xatol": 1e-5, "fatol": 1e-6, "maxiter": 1000},
    )
    return result.x[0], result.fun


# ══════════════════════════════════════════════════════
# WRITE-BACK TO kenpom_predictor.py AND matchup.ts
# ══════════════════════════════════════════════════════
def update_constant(name: str, new_value: float) -> bool:
    """
    Update a numeric constant in kenpom_predictor.py using regex replacement.
    Also mirrors the change to cloudflare-app/src/matchup.ts so both stay in sync.
    """
    formatted = f"{new_value:.4f}" if abs(new_value) < 10 else f"{new_value:.2f}"

    # ── kenpom_predictor.py (Python assignment: NAME = value) ──
    py_source = PREDICTOR_PATH.read_text(encoding="utf-8")
    py_pattern = rf"^({name}\s*=\s*)[\d.]+(.*)$"
    new_py, py_count = re.subn(py_pattern, rf"\g<1>{formatted}\2", py_source, count=1, flags=re.MULTILINE)
    if py_count == 0:
        print(f"  WARNING: Could not find {name} in {PREDICTOR_PATH}")
        return False
    PREDICTOR_PATH.write_text(new_py, encoding="utf-8")

    # ── matchup.ts (TypeScript: export const NAME = value;) ──
    if MATCHUP_TS_PATH.exists():
        ts_source = MATCHUP_TS_PATH.read_text(encoding="utf-8")
        # Matches: export const NAME = <number>;
        ts_pattern = rf"^(export\s+const\s+{name}\s*=\s*)[\d.]+(\s*;.*)$"
        new_ts, ts_count = re.subn(ts_pattern, rf"\g<1>{formatted}\2", ts_source, count=1, flags=re.MULTILINE)
        if ts_count > 0:
            MATCHUP_TS_PATH.write_text(new_ts, encoding="utf-8")
            print(f"  Also updated {name} in {MATCHUP_TS_PATH}")
        else:
            print(f"  NOTE: {name} not found in {MATCHUP_TS_PATH} — skipped Cloudflare sync.")
    else:
        print(f"  NOTE: {MATCHUP_TS_PATH} not found — skipped Cloudflare sync.")

    return True


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════
def main():
    print(f"\n{'═'*60}")
    print(f"  Coefficient Tuner — KenPom Predictor")
    print(f"{'═'*60}\n")

    # Load team data
    teams = parse_kenpom(KENPOM_FILE)
    print(f"  Loaded {len(teams)} teams from KenPom data.")
    bt_teams = load_barttorvik()
    # Merge: prefer KenPom, fill gaps with Barttorvik
    for name, team in bt_teams.items():
        if name not in teams:
            teams[name] = team

    # Load game results
    games = load_games(teams)
    if not games:
        print("  No games to tune on. Exiting.")
        return

    current_hca = kenpom_predictor.HCA
    current_lambda = kenpom_predictor.LAMBDA
    current_tempo_scale = kenpom_predictor.TEMPO_SCALE

    # ── Baseline ──
    baseline_rmse = rmse_score(games, current_lambda, current_tempo_scale, current_hca)

    # ── Pass 1: Optimize LAMBDA + TEMPO_SCALE ──
    print("  Pass 1: Optimizing LAMBDA and TEMPO_SCALE (HCA fixed) ...")
    opt_lambda, opt_tempo_scale, opt_rmse = optimize_lambda_tempo(games, current_hca)

    # ── Results table ──
    print(f"\n{'─'*60}")
    print(f"  {'':20s} {'LAMBDA':>10} {'TEMPO_SCALE':>12} {'RMSE':>10}")
    print(f"{'─'*60}")
    print(f"  {'Starting':20s} {current_lambda:>10.4f} {current_tempo_scale:>12.4f} {baseline_rmse:>10.4f}")
    print(f"  {'Optimized':20s} {opt_lambda:>10.4f} {opt_tempo_scale:>12.4f} {opt_rmse:>10.4f}")
    improvement = baseline_rmse - opt_rmse
    pct = (improvement / baseline_rmse * 100) if baseline_rmse > 0 else 0.0
    print(f"{'─'*60}")
    print(f"  Improvement: {improvement:+.4f} ({pct:+.2f}%)")
    print(f"{'─'*60}\n")

    # ── Write-back prompt ──
    answer = input("  Write optimized LAMBDA and TEMPO_SCALE back to kenpom_predictor.py? [y/N]: ").strip().lower()
    if answer == "y":
        ok1 = update_constant("LAMBDA", opt_lambda)
        ok2 = update_constant("TEMPO_SCALE", opt_tempo_scale)
        if ok1 and ok2:
            print(f"  Updated LAMBDA={opt_lambda:.4f}, TEMPO_SCALE={opt_tempo_scale:.4f} in {PREDICTOR_PATH}\n")
        else:
            print("  Some constants could not be updated.\n")
    else:
        print("  Skipped write-back.\n")

    # ── Pass 2 (optional): Optimize HCA ──
    answer2 = input("  Run Pass 2 to optimize HCA independently? [y/N]: ").strip().lower()
    if answer2 == "y":
        # Use the best LAMBDA/TEMPO_SCALE from pass 1
        locked_lambda = opt_lambda if answer == "y" else current_lambda
        locked_ts = opt_tempo_scale if answer == "y" else current_tempo_scale

        baseline_spread_rmse = rmse_spread(games, locked_lambda, locked_ts, current_hca)
        print(f"\n  Pass 2: Optimizing HCA (LAMBDA={locked_lambda:.4f}, TEMPO_SCALE={locked_ts:.4f} locked) ...")
        opt_hca, opt_spread_rmse = optimize_hca(games, locked_lambda, locked_ts)

        print(f"\n{'─'*60}")
        print(f"  {'':20s} {'HCA':>10} {'Spread RMSE':>12}")
        print(f"{'─'*60}")
        print(f"  {'Starting':20s} {current_hca:>10.4f} {baseline_spread_rmse:>12.4f}")
        print(f"  {'Optimized':20s} {opt_hca:>10.4f} {opt_spread_rmse:>12.4f}")
        hca_improvement = baseline_spread_rmse - opt_spread_rmse
        hca_pct = (hca_improvement / baseline_spread_rmse * 100) if baseline_spread_rmse > 0 else 0.0
        print(f"{'─'*60}")
        print(f"  Improvement: {hca_improvement:+.4f} ({hca_pct:+.2f}%)")
        print(f"{'─'*60}\n")

        hca_answer = input("  Write optimized HCA back to kenpom_predictor.py? [y/N]: ").strip().lower()
        if hca_answer == "y":
            if update_constant("HCA", opt_hca):
                print(f"  Updated HCA={opt_hca:.4f} in {PREDICTOR_PATH}\n")
            else:
                print("  Could not update HCA.\n")
        else:
            print("  Skipped HCA write-back.\n")

    print(f"{'═'*60}")
    print("  Done.")
    print(f"{'═'*60}\n")


if __name__ == "__main__":
    main()
