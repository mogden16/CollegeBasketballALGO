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
    DISCORD_WEBHOOK_URL, fetch_scores_for_date, EDGE_THRESHOLD,
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
    "spread_vs_vegas_error", "model_beat_vegas",
    # Edge context — carried over from predictions_log so results can be
    # filtered to Discord-alerted games without re-joining against predictions.
    "is_edge", "confidence", "kp_spread_edge", "kp_total_edge", "bt_spread_edge",
]

# results format before edge columns were added
_LEGACY_RESULTS_HEADERS_V2 = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
    "bt_home_score", "bt_away_score", "bt_total", "bt_spread",
    "vegas_spread", "vegas_total",
    "spread_error", "total_error",
    "spread_vs_vegas_error", "model_beat_vegas",
]

# results format before bt_* columns were added
_LEGACY_RESULTS_HEADERS = [
    "date", "home_team", "away_team",
    "actual_home_score", "actual_away_score", "actual_total", "actual_spread",
    "kp_home_score", "kp_away_score", "kp_total", "kp_spread",
    "vegas_spread", "vegas_total",
    "spread_error", "total_error",
    "spread_vs_vegas_error", "model_beat_vegas",
]


def _read_csv(path: str, known_headers: list[str], *legacy_header_sets: list[str]) -> list[dict]:
    """Read a CSV, handling headered and headerless files.

    Headerless files (results_log) may contain rows from different schema
    versions if the file was built up over time.  To handle this cleanly,
    the header set is chosen per-row based on exact field-count match rather
    than from the first line only.  This prevents column misalignment when
    the file transitions from a 17-column to a 21-column to a 25-column format.
    """
    all_header_sets = [known_headers] + list(legacy_header_sets)
    # Map field count → header list; keep the most-current set for any given length.
    header_by_len: dict[int, list[str]] = {}
    for h in reversed(all_header_sets):   # iterate oldest-first so newest wins
        header_by_len[len(h)] = h

    with open(path, newline="", encoding="utf-8-sig") as f:
        first_line = f.readline().strip()
        f.seek(0)

        # If the first field looks like a date the file has no header row.
        if first_line and first_line.split(",")[0].count("-") == 2:
            rows = []
            for raw in csv.reader(f):
                if not raw:
                    continue
                n = len(raw)
                headers = header_by_len.get(n)
                if headers is None:
                    # Closest match (handles rows with trailing empty fields)
                    headers = min(all_header_sets, key=lambda h: abs(len(h) - n))
                rows.append({
                    headers[i]: raw[i].strip()
                    for i in range(min(n, len(headers)))
                })
            return rows
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
        for row in _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS_V2, _LEGACY_RESULTS_HEADERS):
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
                "is_edge":              p.get("is_edge", ""),
                "confidence":           p.get("confidence", ""),
                "kp_spread_edge":       p.get("kp_spread_edge", ""),
                "kp_total_edge":        p.get("kp_total_edge", ""),
                "bt_spread_edge":       p.get("bt_spread_edge", ""),
            })

            print(f"  Saved. Spread error: {spread_error:+.1f} pts  |  Total error: {total_error:+.1f} pts\n")

    print(f"Results saved to {RESULTS_LOG}\n")

# ══════════════════════════════════════════════════════
# PERFORMANCE SUMMARY (shared helper)
# ══════════════════════════════════════════════════════
def _pct(num: int, denom: int) -> str:
    return f"{num}/{denom} ({100 * num / denom:.1f}%)" if denom else "N/A"


def performance_summary(rows: list[dict], label: str) -> str:
    """
    Build a performance stats block for a set of result rows.

    Four metrics, each broken out by all games / edge games / HIGH confidence:
      1. Moneyline   — did the model pick the correct outright winner?
      2. KP Spread ATS — did KenPom correctly call which side covers vs Vegas?
      3. KP Total O/U  — did KenPom correctly call over/under vs Vegas total?
      4. BT Spread ATS — did T-Rank correctly call which side covers vs Vegas?

    Edge games = rows where |spread_edge| >= EDGE_THRESHOLD (the Discord alert threshold).
    HIGH confidence = rows where both KP and BT agreed and both cleared EDGE_THRESHOLD.
    """
    if not rows:
        return f"\n  {label}: No data."

    n = len(rows)

    # ── Safe float helper ──────────────────────────────────────────────────────
    def _f(val):
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    # ── MAE ───────────────────────────────────────────────────────────────────
    spread_errs = [abs(_f(r["spread_error"])) for r in rows if _f(r.get("spread_error")) is not None]
    total_errs  = [abs(_f(r["total_error"]))  for r in rows if _f(r.get("total_error"))  is not None]
    mae_spread  = sum(spread_errs) / len(spread_errs) if spread_errs else None
    mae_total   = sum(total_errs)  / len(total_errs)  if total_errs  else None

    # ── Metric helpers ────────────────────────────────────────────────────────
    def _moneyline(subset):
        """Correct outright winner, no spread involved."""
        correct = total = 0
        for r in subset:
            kp  = _f(r.get("kp_spread"))
            act = _f(r.get("actual_spread"))
            if kp is None or act is None or act == 0:
                continue
            total += 1
            if (kp < 0) == (act < 0):
                correct += 1
        return correct, total

    def _spread_ats(subset, spread_key, edge_key, threshold=None):
        """
        ATS correctness for a given model spread column.
        If threshold is set, only rows where |edge_key| >= threshold are counted
        (i.e. games the model actually had a strong opinion on).
        Falls back to computing edge from spread values if edge_key is missing.
        """
        correct = total = 0
        for r in subset:
            model = _f(r.get(spread_key))
            vegas = _f(r.get("vegas_spread"))
            act   = _f(r.get("actual_spread"))
            if model is None or vegas is None or act is None:
                continue
            if threshold is not None:
                # Use pre-computed edge when available; fall back to raw diff
                edge = _f(r.get(edge_key))
                if edge is None:
                    edge = vegas - model   # kp_spread_edge convention: vegas - model
                if abs(edge) < threshold:
                    continue
            elif model == vegas:
                continue   # no opinion, skip for all-games ATS
            total += 1
            if (model < vegas) == (act < vegas):   # same side picked vs covered
                correct += 1
        return correct, total

    def _total_ou(subset, threshold=None):
        """
        Over/under correctness vs Vegas total.
        If threshold is set, only rows where |kp_total_edge| >= threshold counted.
        Falls back to computing edge on the fly if kp_total_edge missing.
        """
        correct = total = 0
        for r in subset:
            kp_tot    = _f(r.get("kp_total"))
            vegas_tot = _f(r.get("vegas_total"))
            act_tot   = _f(r.get("actual_total"))
            if kp_tot is None or vegas_tot is None or act_tot is None:
                continue
            if threshold is not None:
                edge = _f(r.get("kp_total_edge"))
                if edge is None:
                    edge = kp_tot - vegas_tot   # kp_total_edge convention: kp - vegas
                if abs(edge) < threshold:
                    continue
            total += 1
            if (kp_tot > vegas_tot) == (act_tot > vegas_tot):
                correct += 1
        return correct, total

    # ── Subsets ───────────────────────────────────────────────────────────────
    # For rows that predate the is_edge/confidence columns (legacy format),
    # compute the edge flag on the fly from stored spread values so that
    # historical data still populates the edge/high breakdowns.
    def _row_is_edge(r):
        stored = r.get("is_edge", "").lower()
        if stored == "true":
            return True
        if stored == "false":
            return False
        # Legacy row — derive from spreads
        kp = _f(r.get("kp_spread"))
        vs = _f(r.get("vegas_spread"))
        return kp is not None and vs is not None and abs(vs - kp) >= EDGE_THRESHOLD

    def _row_is_high(r):
        stored = r.get("confidence", "").upper()
        if stored == "HIGH":
            return True
        if stored:   # non-blank non-HIGH means explicitly not HIGH
            return False
        # Legacy row — HIGH if KP and BT both clear EDGE_THRESHOLD in same direction
        kp = _f(r.get("kp_spread"))
        bt = _f(r.get("bt_spread"))
        vs = _f(r.get("vegas_spread"))
        if kp is None or bt is None or vs is None:
            return False
        kp_edge = vs - kp
        bt_edge = vs - bt
        return (abs(kp_edge) >= EDGE_THRESHOLD
                and abs(bt_edge) >= EDGE_THRESHOLD
                and (kp_edge > 0) == (bt_edge > 0))

    edge_rows = [r for r in rows if _row_is_edge(r)]
    high_rows = [r for r in rows if _row_is_high(r)]

    # ── Build each metric ─────────────────────────────────────────────────────
    ml_all  = _moneyline(rows)
    ml_edge = _moneyline(edge_rows)
    ml_high = _moneyline(high_rows)

    kp_ats_all  = _spread_ats(rows,      "kp_spread", "kp_spread_edge")
    kp_ats_edge = _spread_ats(rows,      "kp_spread", "kp_spread_edge", EDGE_THRESHOLD)
    kp_ats_high = _spread_ats(high_rows, "kp_spread", "kp_spread_edge")

    kp_ou_all  = _total_ou(rows)
    kp_ou_edge = _total_ou(rows, EDGE_THRESHOLD)

    bt_ats_all  = _spread_ats(rows,      "bt_spread", "bt_spread_edge")
    bt_ats_edge = _spread_ats(rows,      "bt_spread", "bt_spread_edge", EDGE_THRESHOLD)
    bt_ats_high = _spread_ats(high_rows, "bt_spread", "bt_spread_edge")

    # ── Format ────────────────────────────────────────────────────────────────
    thr = f"{EDGE_THRESHOLD:.0f}"
    lines = []
    lines.append(f"\n{'═'*60}")
    lines.append(f"  {label}  |  {n} games")
    lines.append(f"{'═'*60}")

    lines.append(f"\n  1. MONEYLINE  (outright winner, spread ignored)")
    lines.append(f"     All games          : {_pct(*ml_all)}")
    lines.append(f"     Edge games (≥{thr}pt): {_pct(*ml_edge)}")
    lines.append(f"     HIGH confidence    : {_pct(*ml_high)}")

    lines.append(f"\n  2. KP SPREAD ATS  (vs Vegas spread)")
    lines.append(f"     All games          : {_pct(*kp_ats_all)}")
    lines.append(f"     Edge games (≥{thr}pt): {_pct(*kp_ats_edge)}")
    lines.append(f"     HIGH confidence    : {_pct(*kp_ats_high)}")

    lines.append(f"\n  3. KP TOTAL  O/U  (vs Vegas total)")
    lines.append(f"     All games          : {_pct(*kp_ou_all)}")
    lines.append(f"     Total edge (≥{thr}pt): {_pct(*kp_ou_edge)}")

    lines.append(f"\n  4. BT SPREAD ATS  (vs Vegas spread)")
    lines.append(f"     All games          : {_pct(*bt_ats_all)}")
    lines.append(f"     Edge games (≥{thr}pt): {_pct(*bt_ats_edge)}")
    lines.append(f"     HIGH confidence    : {_pct(*bt_ats_high)}")

    lines.append(f"\n  ACCURACY (MAE)")
    lines.append(f"     KP Spread MAE      : {mae_spread:.2f} pts" if mae_spread else "     KP Spread MAE      : N/A")
    lines.append(f"     KP Total MAE       : {mae_total:.2f} pts"  if mae_total  else "     KP Total MAE       : N/A")

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


def _send_discord_table(text: str, label: str) -> None:
    """Post a pre-formatted game table to Discord, chunking if needed."""
    if not DISCORD_WEBHOOK_URL:
        return
    # Discord embed description cap is 4096 chars; leave room for ``` wrapper (8 chars)
    CHUNK = 3800
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
    total  = len(chunks)
    for idx, chunk in enumerate(chunks):
        title = f"📋 {label}" if total == 1 else f"📋 {label}  ({idx + 1}/{total})"
        payload = {"embeds": [{"title": title, "description": f"```\n{chunk}\n```", "color": 0x2ECC71}]}
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload,
                                 headers={"Content-Type": "application/json"}, timeout=10)
            status = "posted" if resp.status_code == 204 else f"status {resp.status_code}"
            print(f"  Discord table: {status}")
        except Exception as exc:
            print(f"  Discord table: failed — {exc}")


def performance_report():
    """Print model accuracy summary from results_log.csv and send to Discord."""
    if not Path(RESULTS_LOG).exists():
        print("No results log found. Enter some actual scores first with --results.")
        return

    all_rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS_V2, _LEGACY_RESULTS_HEADERS)

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

    # Edge game detail — best/worst within the flagged set
    # Use the same dynamic-fallback logic as performance_summary for legacy rows.
    def _f_local(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _is_edge(r):
        s = r.get("is_edge", "").lower()
        if s == "true":
            return True
        if s == "false":
            return False
        kp = _f_local(r.get("kp_spread"))
        vs = _f_local(r.get("vegas_spread"))
        return kp is not None and vs is not None and abs(vs - kp) >= EDGE_THRESHOLD

    def _is_high(r):
        s = r.get("confidence", "").upper()
        if s == "HIGH":
            return True
        if s:
            return False
        kp = _f_local(r.get("kp_spread"))
        bt = _f_local(r.get("bt_spread"))
        vs = _f_local(r.get("vegas_spread"))
        if kp is None or bt is None or vs is None:
            return False
        kp_e = vs - kp
        bt_e = vs - bt
        return (abs(kp_e) >= EDGE_THRESHOLD and abs(bt_e) >= EDGE_THRESHOLD
                and (kp_e > 0) == (bt_e > 0))

    edge_rows = [r for r in rows if _is_edge(r)]
    high_rows = [r for r in rows if _is_high(r)]
    edge_text = ""
    if edge_rows:
        edge_text += f"\n  EDGE GAMES FLAGGED : {len(edge_rows)}"
        if high_rows:
            edge_text += f"  ({len(high_rows)} HIGH confidence)"
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
        existing_rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS_V2, _LEGACY_RESULTS_HEADERS)
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
                "is_edge":              p.get("is_edge", ""),
                "confidence":           p.get("confidence", ""),
                "kp_spread_edge":       p.get("kp_spread_edge", ""),
                "kp_total_edge":        p.get("kp_total_edge", ""),
                "bt_spread_edge":       p.get("bt_spread_edge", ""),
            }
            writer.writerow(row)
            new_rows.append(row)

    print(f"\n  Resolved {len(new_rows)} game(s). Results saved to {RESULTS_LOG}")
    return existing_rows + new_rows


# ══════════════════════════════════════════════════════
# GAME TABLE OUTPUT
# ══════════════════════════════════════════════════════
def print_game_table(rows: list[dict], label: str = "") -> None:
    """
    Print a per-game table of predictions vs actuals.

    Columns (all spreads from home team's perspective, negative = home favored):
      Date | Matchup | KP | BT | Vegas | Score | Err | ATS | O/U

    Edge flags: ⚡ = edge game,  ⚡⚡ = HIGH confidence
    ATS: W = KP picked correct side vs Vegas, L = wrong, – = no Vegas line
    O/U: actual result (O/U), marked ✓ if KP predicted correctly, ✗ if not
    """
    if not rows:
        print("  No data to display.")
        return

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _short(name: str, width: int) -> str:
        return name[:width] if len(name) > width else name

    # ── Column widths ──────────────────────────────────────────────────────────
    W_DATE    = 10
    W_MATCHUP = 30
    W_KP      = 7
    W_BT      = 7
    W_VEGAS   = 9   # spread + total  e.g. -8.5/141
    W_SCORE   = 11  # e.g. "73-64 H" or "73-64 A"
    W_ERR     = 6
    W_ATS     = 3
    W_OU      = 4

    sep_width = W_DATE + 1 + W_MATCHUP + 1 + W_KP + 1 + W_BT + 1 + W_VEGAS + 1 + W_SCORE + 1 + W_ERR + 1 + W_ATS + 1 + W_OU

    hdr = (
        f"{'Date':<{W_DATE}} {'Matchup':<{W_MATCHUP}} "
        f"{'KP':>{W_KP}} {'BT':>{W_BT}} {'Vegas':>{W_VEGAS}} "
        f"{'Score':>{W_SCORE}} {'Err':>{W_ERR}} {'ATS':>{W_ATS}} {'O/U':>{W_OU}}"
    )

    lines = []
    if label:
        lines.append(f"\n{'═' * sep_width}")
        lines.append(f"  {label}")
    lines.append(f"\n{hdr}")
    lines.append("─" * sep_width)

    prev_date = None
    for r in sorted(rows, key=lambda x: (x.get("date", ""), x.get("home_team", ""))):
        home = r.get("home_team", "")
        away = r.get("away_team", "")
        date = r.get("date", "")

        # Blank line between dates for readability
        if prev_date and date != prev_date:
            lines.append("")
        prev_date = date

        # ── Edge flag ──
        # Use stored values when present; fall back to computing from spreads
        # so that legacy rows (which predate the is_edge column) still render flags.
        conf_stored    = r.get("confidence", "").upper()
        is_edge_stored = r.get("is_edge", "").lower()

        kp_v_edge  = _f(r.get("kp_spread"))
        bt_v_edge  = _f(r.get("bt_spread"))
        vegas_edge = _f(r.get("vegas_spread"))

        if is_edge_stored == "true":
            is_edge = True
        elif is_edge_stored == "false":
            is_edge = False
        else:
            # Legacy row — compute from spread delta
            is_edge = (kp_v_edge is not None and vegas_edge is not None
                       and abs(vegas_edge - kp_v_edge) >= EDGE_THRESHOLD)

        if conf_stored == "HIGH":
            conf = "HIGH"
        elif conf_stored:
            conf = ""
        else:
            # Legacy row — HIGH if both KP and BT edge in the same direction
            if (is_edge and bt_v_edge is not None and vegas_edge is not None):
                kp_e = vegas_edge - kp_v_edge
                bt_e = vegas_edge - bt_v_edge
                conf = ("HIGH" if abs(bt_e) >= EDGE_THRESHOLD and (kp_e > 0) == (bt_e > 0)
                        else "")
            else:
                conf = ""

        if conf == "HIGH":
            flag = " ⚡⚡"
        elif is_edge:
            flag = " ⚡"
        else:
            flag = ""

        matchup_raw = f"{_short(away, 13)} @ {_short(home, 13)}"
        matchup = f"{matchup_raw}{flag}"

        # ── Spreads (home perspective, negative = home favored) ──
        kp_v    = _f(r.get("kp_spread"))
        bt_v    = _f(r.get("bt_spread"))
        vegas_v = _f(r.get("vegas_spread"))
        vegas_t = _f(r.get("vegas_total"))

        kp_str    = f"{kp_v:+.1f}"   if kp_v    is not None else "  N/A"
        bt_str    = f"{bt_v:+.1f}"   if bt_v    is not None else "  N/A"
        if vegas_v is not None:
            vegas_str = f"{vegas_v:+.1f}"
            if vegas_t is not None:
                vegas_str += f"/{vegas_t:.0f}"
        else:
            vegas_str = "    N/A"

        # ── Actual score ──
        a_home = _f(r.get("actual_home_score"))
        a_away = _f(r.get("actual_away_score"))
        if a_home is not None and a_away is not None:
            winner = "H" if a_home > a_away else "A"
            score_str = f"{int(a_home)}-{int(a_away)} {winner}"
        else:
            score_str = "Pending"

        # ── Spread error ──
        err_v   = _f(r.get("spread_error"))
        err_str = f"{err_v:+.1f}" if err_v is not None else "   N/A"

        # ── ATS (KP vs Vegas) ──
        act_v = _f(r.get("actual_spread"))
        if kp_v is not None and vegas_v is not None and act_v is not None and kp_v != vegas_v:
            ats_str = "W" if (kp_v < vegas_v) == (act_v < vegas_v) else "L"
        else:
            ats_str = "–"

        # ── O/U: show actual result (O/U) and whether KP was right ──
        kp_t  = _f(r.get("kp_total"))
        act_t = _f(r.get("actual_total"))
        if kp_t is not None and vegas_t is not None and act_t is not None:
            went_over   = act_t > vegas_t
            kp_said_over = kp_t > vegas_t
            ou_letter = "O" if went_over else "U"
            ou_right  = went_over == kp_said_over
            ou_str = f"{ou_letter} {'✓' if ou_right else '✗'}"
        else:
            ou_str = " –"

        lines.append(
            f"{date:<{W_DATE}} {matchup:<{W_MATCHUP}} "
            f"{kp_str:>{W_KP}} {bt_str:>{W_BT}} {vegas_str:>{W_VEGAS}} "
            f"{score_str:>{W_SCORE}} {err_str:>{W_ERR}} {ats_str:>{W_ATS}} {ou_str:>{W_OU}}"
        )

    lines.append("─" * sep_width)
    lines.append(f"  ⚡ = edge game (|KP vs Vegas| ≥ {EDGE_THRESHOLD:.0f} pts)   "
                 f"⚡⚡ = HIGH confidence")
    lines.append(f"  Spreads from home's perspective (– = home favored)   "
                 f"ATS: W/L = KP picked correct side vs Vegas")
    lines.append(f"  O/U: ✓ = KP predicted direction correctly, ✗ = wrong")

    output = "\n".join(lines)
    print(output)
    return output


def table_report(date_str: str | None = None, all_dates: bool = False,
                 send_discord: bool = False) -> None:
    """
    Load results and print the game table.
      date_str     : show only this date (YYYY-MM-DD). Defaults to yesterday.
      all_dates    : if True, ignore date_str and show full history.
      send_discord : if True, also post the table to Discord.
    """
    if not Path(RESULTS_LOG).exists():
        print("No results log found. Run the pipeline first.")
        return

    all_rows = _read_csv(RESULTS_LOG, RESULTS_HEADERS, _LEGACY_RESULTS_HEADERS_V2, _LEGACY_RESULTS_HEADERS)
    if not all_rows:
        print("Results log is empty.")
        return

    if all_dates:
        rows  = all_rows
        label = f"ALL RESULTS — {len(all_rows)} games"
    else:
        target = date_str or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        rows   = [r for r in all_rows if r.get("date") == target]
        label  = f"RESULTS — {target}"
        if not rows:
            print(f"No results found for {target}.")
            return

    text = print_game_table(rows, label)
    if send_discord and text:
        _send_discord_table(text, label)


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

    # Step 3: Game table for yesterday — printed and posted to Discord
    table_report(send_discord=True)


# ══════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════
if __name__ == "__main__":
    if "--results" in sys.argv:
        enter_results()
    elif "--table-all" in sys.argv:
        # Full history table across all logged dates
        table_report(all_dates=True)
    elif "--table" in sys.argv:
        # Table for yesterday only (default) or a specific date passed as next arg
        idx = sys.argv.index("--table")
        date_arg = sys.argv[idx + 1] if idx + 1 < len(sys.argv) and sys.argv[idx + 1].count("-") == 2 else None
        table_report(date_str=date_arg)
    else:
        run_results_pipeline()
