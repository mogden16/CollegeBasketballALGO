#!/usr/bin/env python3
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDICTIONS = ROOT / "predictions_log.csv"
OUTPUT = ROOT / "cloudflare-app" / "data" / "picks-of-day.json"


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader(PREDICTIONS.open()))
latest = max(row["date"] for row in rows)
latest_rows = [
    row for row in rows if row["date"] == latest and row.get("is_edge", "").lower() == "true"
]

for row in latest_rows:
    spread_edges = [
        abs(v)
        for v in [to_float(row.get("kp_spread_edge")), to_float(row.get("bt_spread_edge"))]
        if v is not None
    ]
    row["edge_score"] = round(sum(spread_edges) / len(spread_edges), 2) if spread_edges else 0

latest_rows.sort(key=lambda row: row["edge_score"], reverse=True)

picks = []
for row in latest_rows[:15]:
    kp_spread = to_float(row.get("kp_spread")) or 0
    picks.append(
        {
            "date": row["date"],
            "matchup": f"{row['away_team']} @ {row['home_team']}",
            "recommendedBet": f"{'Home' if kp_spread < 0 else 'Away'} {row.get('kp_spread')}",
            "modelSpread": row.get("kp_spread"),
            "vegasSpread": row.get("vegas_spread") or None,
            "edge": row["edge_score"],
            "confidence": row.get("confidence") or ("MEDIUM" if row["edge_score"] >= 5 else "LOW"),
        }
    )

OUTPUT.parent.mkdir(parents=True, exist_ok=True)
OUTPUT.write_text(json.dumps({"asOf": latest, "picks": picks}, indent=2))
print(f"Wrote {len(picks)} picks to {OUTPUT}")
