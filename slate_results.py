"""
Slate Results — Auto-check scores via ESPN, log results, performance report.
Uses predictions logged by kenpom_predictor.py.

Usage:
  python slate_results.py             Run full pipeline: check → report → Discord
  python slate_results.py --results   Enter actual scores interactively
"""

import csv
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from thefuzz import process

from kenpom_predictor import (
    PREDICTIONS_LOG, PREDICTIONS_HEADERS, RESULTS_LOG, FUZZY_THRESHOLD,
    DISCORD_WEBHOOK_URL, fetch_scores_for_date,
)

# ══════════════════════════════════════════════════════
# RESULTS CSV SCHEMA
# ══════════════════════════════════════════════════════
RESULTS_HEADERS = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
    "bt_home_score", "bt_away_score", "bt_total", "bt_spread",
    "vegas_spread", "vegas_total",
    "spread_error", "total_error",
    "spread_vs_vegas_error", "model_beat_vegas"
]

# Old results format (before bt_* columns were added)
_LEGACY_RESULTS_HEADERS = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
    "vegas_spread", "vegas_total",
    "spread_error", "total_error",
    "spread_vs_vegas_error", "model_beat_vegas"
]


def _read_csv(path: str, known_headers: list[str], legacy_headers: list[str] = None) -> list[dict]:
    """Read a CSV, auto-detecting missing header rows and legacy column layouts."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline()
        f.seek(0)
        # Headerless CSV: first field looks like a date (YYYY-MM-DD)
        if first_line and first_line.strip() and first_line.strip().split(",")[0].count("-") == 2:
            num_fields = len(first_line.strip().split(","))
            if num_fields == len(known_headers):
                headers = known_headers
            elif legacy_headers and num_fields == len(legacy_headers):
                headers = legacy_headers
            else:
                headers = known_headers
            reader = csv.DictReader(f, fieldnames=headers)
        else:
            reader = csv.DictReader(f)
        return [{k.strip(): (v.strip() if v else "") for k, v in row.items() if k} for row in reader]


# ══════════════════════════════════════════════════════
# ENTER ACTUAL RESULTS (interactive)
# ══════════════════════════════════════════════════════
def enter_results():
    """
    Interactive CLI to enter actual game scores.
    Reads unresolved predictions from predictions_log.csv,
    lets you enter final scores, and saves to results_log.csv.
    """
    if not Path(PREDICTIONS_LOG).exists():
        print("No predictions log found. Run the predictor first.")
        return

    predictions = _read_csv(PREDICTIONS_LOG, PREDICTIONS_HEADERS)

    resolved = set()
    if Path(RESULTS_LOG).exists():
        for row in _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS):
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
            actual_spread = -(actual_home - actual_away)
            spread_error  = round(float(p["kp_spread"]) - actual_spread, 1)
            total_error   = round(float(p["kp_total"]) - actual_total, 1)

            spread_vs_vegas = ""
            model_beat_vegas = ""
            if p["vegas_spread"]:
                vegas_spread_error = round(float(p["vegas_spread"]) - actual_spread, 1)
                spread_vs_vegas    = round(abs(spread_error) - abs(vegas_spread_error), 1)
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
                "bt_home_score":        p.get("bt_home_score", ""),
                "bt_away_score":        p.get("bt_away_score", ""),
                "bt_total":             p.get("bt_total", ""),
                "bt_spread":            p.get("bt_spread", ""),
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
# PERFORMANCE SUMMARY (shared helper)
# ══════════════════════════════════════════════════════
def _pct(num: int, denom: int) -> str:
    return f"{num}/{denom} ({100 * num / denom:.1f}%)" if denom else "N/A"


def performance_summary(rows: list[dict], label: str) -> str:
    """Build performance stats string for a set of result rows."""
    if not rows:
        return f"\n  {label}: No data."

    n = len(rows)
    spread_errors = [abs(float(r["spread_error"])) for r in rows if r.get("spread_error")]
    total_errors  = [abs(float(r["total_error"]))  for r in rows if r.get("total_error")]
    beat_vegas    = [r for r in rows if r.get("model_beat_vegas") == "YES"]
    vs_vegas_rows = [r for r in rows if r.get("model_beat_vegas") in ("YES", "NO")]

    mae_spread = sum(spread_errors) / len(spread_errors) if spread_errors else None
    mae_total  = sum(total_errors)  / len(total_errors)  if total_errors  else None

    # --- Moneyline: did the model pick the correct winner? ---
    # spread < 0 means home is favored / won
    ml_correct = 0
    ml_total = 0
    for r in rows:
        kp = r.get("kp_spread", "")
        actual = r.get("actual_spread", "")
        if not kp or not actual:
            continue
        kp_val, actual_val = float(kp), float(actual)
        if actual_val == 0:
            continue  # push / no winner
        ml_total += 1
        if (kp_val < 0) == (actual_val < 0):
            ml_correct += 1

    # --- KP spread ATS: did KP correctly pick the right side vs Vegas? ---
    kp_ats_correct = 0
    kp_ats_total = 0
    for r in rows:
        kp = r.get("kp_spread", "")
        vegas = r.get("vegas_spread", "")
        actual = r.get("actual_spread", "")
        if not kp or not vegas or not actual:
            continue
        kp_val, vegas_val, actual_val = float(kp), float(vegas), float(actual)
        if kp_val == vegas_val:
            continue  # no edge, no pick
        kp_ats_total += 1
        # KP says take home ATS if kp_spread < vegas_spread (home stronger than Vegas thinks)
        kp_says_home = kp_val < vegas_val
        home_covered = actual_val < vegas_val
        if kp_says_home == home_covered:
            kp_ats_correct += 1

    # --- BT spread ATS: did T-Rank correctly pick the right side vs Vegas? ---
    bt_ats_correct = 0
    bt_ats_total = 0
    for r in rows:
        bt = r.get("bt_spread", "")
        vegas = r.get("vegas_spread", "")
        actual = r.get("actual_spread", "")
        if not bt or not vegas or not actual:
            continue
        bt_val, vegas_val, actual_val = float(bt), float(vegas), float(actual)
        if bt_val == vegas_val:
            continue
        bt_ats_total += 1
        bt_says_home = bt_val < vegas_val
        home_covered = actual_val < vegas_val
        if bt_says_home == home_covered:
            bt_ats_correct += 1

    lines = []
    lines.append(f"\n{'═'*60}")
    lines.append(f"  {label}  |  {n} games")
    lines.append(f"{'═'*60}")
    lines.append(f"  KP Spread MAE      : {mae_spread:.2f} pts" if mae_spread else "  KP Spread MAE      : N/A")
    lines.append(f"  Total MAE          : {mae_total:.2f} pts"  if mae_total  else "  Total MAE          : N/A")
    lines.append(f"  Moneyline (KP)     : {_pct(ml_correct, ml_total)}")
    lines.append(f"  KP Spread ATS      : {_pct(kp_ats_correct, kp_ats_total)}")
    lines.append(f"  BT Spread ATS      : {_pct(bt_ats_correct, bt_ats_total)}")
    if vs_vegas_rows:
        lines.append(f"  KP Closer Than Vegas: {_pct(len(beat_vegas), len(vs_vegas_rows))}")

    return "\n".join(lines)

# ══════════════════════════════════════════════════════
# PERFORMANCE REPORT + DISCORD
# ══════════════════════════════════════════════════════
def _send_discord_report(text: str) -> None:
    """Send performance report text to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return
    payload = {
        "embeds": [{
            "title": "📊 Model Performance Report",
            "description": f"```\n{text}\n```",
            "color": 0x3498DB,
        }]
    }
    try:
        resp = requests.post(
            DISCORD_WEBHOOK_URL, json=payload,
            headers={"Content-Type": "application/json"}, timeout=10,
        )
        if resp.status_code == 204:
            print(f"  Discord: performance report posted successfully.")
        else:
            print(f"  Discord: returned status {resp.status_code} -- {resp.text}")
    except Exception as exc:
        print(f"  Discord: failed to send report -- {exc}")


def performance_report():
    """Print model accuracy summary from results_log.csv and send to Discord."""
    if not Path(RESULTS_LOG).exists():
        print("No results log found. Enter some actual scores first with --results.")
        return

    all_rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS)

    if not all_rows:
        print("Results log is empty.")
        return

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    rows = [r for r in all_rows if r.get("date") == yesterday]

    if not rows:
        print(f"No results found for {yesterday}.")
        return

    report_parts = []
    report_parts.append(performance_summary(rows, f"SLATE RESULTS — {yesterday}"))

    # Edge game performance
    edge_rows = [r for r in rows if r.get("is_edge", "").lower() == "true"]
    edge_text = ""
    if edge_rows:
        edge_spread_errors = [abs(float(r["spread_error"])) for r in edge_rows if r["spread_error"]]
        edge_mae = sum(edge_spread_errors) / len(edge_spread_errors) if edge_spread_errors else None
        edge_text += f"\n  EDGE GAMES ({len(edge_rows)} games flagged)"
        edge_text += f"\n  Edge spread MAE    : {edge_mae:.2f} pts" if edge_mae else "\n  N/A"
    report_parts.append(edge_text)

    # Best and worst predictions
    scored_rows = [r for r in rows if r.get("spread_error")]
    sorted_by_error = sorted(scored_rows, key=lambda r: abs(float(r["spread_error"])))
    best_worst = []
    if sorted_by_error:
        best_worst.append(f"\n  BEST PREDICTIONS (smallest spread error):")
        for r in sorted_by_error[:3]:
            best_worst.append(f"    {r['date']}  {r['away_team']} @ {r['home_team']}  error: {float(r['spread_error']):+.1f} pts")
        best_worst.append(f"\n  WORST PREDICTIONS (largest spread error):")
        for r in sorted_by_error[-3:]:
            best_worst.append(f"    {r['date']}  {r['away_team']} @ {r['home_team']}  error: {float(r['spread_error']):+.1f} pts")
    report_parts.append("\n".join(best_worst))

    footer = f"\n{'═'*60}\n"
    report_parts.append(footer)

    full_report = "\n".join(report_parts)
    print(full_report)
    _send_discord_report(full_report)

# ══════════════════════════════════════════════════════
# AUTO-CHECK RESULTS VIA ESPN
# ══════════════════════════════════════════════════════
def check_results():
    """
    Automatically fetch actual scores from ESPN, compare to predictions,
    and log results. Returns all result rows (existing + new).
    """
    if not Path(PREDICTIONS_LOG).exists():
        print("No predictions log found. Run the predictor first.")
        return []

    predictions = _read_csv(PREDICTIONS_LOG, PREDICTIONS_HEADERS)

    if not predictions:
        print("  No predictions found in log.")
        return []

    def _col(p, kp_name, model_name):
        return p.get(kp_name, p.get(model_name, ""))

    resolved = set()
    existing_rows = []
    if Path(RESULTS_LOG).exists():
        existing_rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS)
        for row in existing_rows:
            resolved.add((row["date"], row["home_team"], row["away_team"]))

    pending = [
        p for p in predictions
        if (p["date"], p["home_team"], p["away_team"]) not in resolved
    ]

    if not pending:
        print("No pending predictions to resolve. All caught up.")
        return existing_rows

    dates = sorted(set(p["date"] for p in pending))
    print(f"\n  Fetching scores for {len(dates)} date(s): {', '.join(dates)}")

    espn_scores = {}
    for d in dates:
        espn_scores[d] = fetch_scores_for_date(d)
        print(f"  {d}: {len(espn_scores[d])} final game(s) found on ESPN")

    results_path = Path(RESULTS_LOG)
    write_header = not results_path.exists()
    new_rows = []

    with open(results_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULTS_HEADERS)
        if write_header:
            writer.writeheader()

        for p in pending:
            scores = espn_scores.get(p["date"], {})
            if not scores:
                continue

            best_match = None
            best_score = 0
            for (espn_home, espn_away), (home_sc, away_sc) in scores.items():
                home_result = process.extractOne(p["home_team"], [espn_home, espn_away])
                away_result = process.extractOne(p["away_team"], [espn_home, espn_away])
                if not home_result or not away_result:
                    continue
                combined = home_result[1] + away_result[1]
                if home_result[0] == espn_home and away_result[0] == espn_away and combined > best_score:
                    best_score = combined
                    best_match = (espn_home, espn_away, home_sc, away_sc)

            if not best_match or best_score < FUZZY_THRESHOLD * 2:
                continue

            _, _, actual_home, actual_away = best_match
            actual_total  = actual_home + actual_away
            actual_spread = -(actual_home - actual_away)

            pred_spread = _col(p, "kp_spread", "model_spread")
            pred_total  = _col(p, "kp_total", "model_total")
            pred_home   = _col(p, "kp_home_score", "model_home_score")
            pred_away   = _col(p, "kp_away_score", "model_away_score")

            spread_error  = round(float(pred_spread) - actual_spread, 1)
            total_error   = round(float(pred_total) - actual_total, 1)

            spread_vs_vegas = ""
            model_beat_vegas = ""
            if p.get("vegas_spread"):
                vegas_spread_error = round(float(p["vegas_spread"]) - actual_spread, 1)
                spread_vs_vegas    = round(abs(spread_error) - abs(vegas_spread_error), 1)
                model_beat_vegas   = "YES" if spread_vs_vegas < 0 else "NO"

            row = {
                "date":                 p["date"],
                "home_team":            p["home_team"],
                "away_team":            p["away_team"],
                "actual_home_score":    actual_home,
                "actual_away_score":    actual_away,
                "actual_total":         actual_total,
                "actual_spread":        actual_spread,
                "kp_home_score":        pred_home,
                "kp_away_score":        pred_away,
                "kp_total":             pred_total,
                "kp_spread":            pred_spread,
                "bt_home_score":        p.get("bt_home_score", ""),
                "bt_away_score":        p.get("bt_away_score", ""),
                "bt_total":             p.get("bt_total", ""),
                "bt_spread":            p.get("bt_spread", ""),
                "vegas_spread":         p.get("vegas_spread", ""),
                "vegas_total":          p.get("vegas_total", ""),
                "spread_error":         spread_error,
                "total_error":          total_error,
                "spread_vs_vegas_error": spread_vs_vegas,
                "model_beat_vegas":     model_beat_vegas,
            }
            writer.writerow(row)
            new_rows.append(row)

    print(f"\n  Resolved {len(new_rows)} game(s). Results saved to {RESULTS_LOG}")
    return existing_rows + new_rows


# ══════════════════════════════════════════════════════
# FULL PIPELINE: check → report → Discord
# ══════════════════════════════════════════════════════
def run_results_pipeline():
    """Daily automated pipeline: check ESPN scores, then report + Discord."""
    print(f"\n{'═'*60}")
    print(f"  Slate Results Pipeline  |  {datetime.now().strftime('%A %b %d, %Y')}")
    print(f"{'═'*60}")

    # Step 1: Check results via ESPN
    all_rows = check_results()

    if not all_rows:
        print("  No results to report.")
        return

    # Step 2: Performance report + Discord
    performance_report()


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--results" in sys.argv:
        enter_results()
    else:
        run_results_pipeline()
