#!/usr/bin/env python3
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREDICTIONS = ROOT / "predictions_log.csv"
OUTPUT = ROOT / "cloudflare-app" / "data" / "games-by-date.json"


def to_float(value: str):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def to_bool(value: str):
    return str(value or "").strip().lower() == "true"


def projection(row, prefix):
    return {
        "homeScore": to_float(row.get(f"{prefix}_home_score")),
        "awayScore": to_float(row.get(f"{prefix}_away_score")),
        "total": to_float(row.get(f"{prefix}_total")),
        "spread": to_float(row.get(f"{prefix}_spread")),
        "spreadEdge": to_float(row.get(f"{prefix}_spread_edge")),
        "totalEdge": to_float(row.get(f"{prefix}_total_edge")),
    }


def main():
    rows = list(csv.DictReader(PREDICTIONS.open()))
    by_date: dict[str, list[dict]] = {}

    for row in rows:
        game = {
            "selectedDate": row["date"],
            "homeTeam": row["home_team"],
            "awayTeam": row["away_team"],
            "neutral": to_bool(row.get("neutral")),
            "neutralSite": to_bool(row.get("neutral")),
            "gameTimeUtc": None,
            "gameTimeEtDisplay": "Time TBD",
            "awayLogo": None,
            "homeLogo": None,
            "kenpom": projection(row, "kp"),
            "trank": projection(row, "bt"),
            "vegas": {
                "spread": to_float(row.get("vegas_spread")),
                "total": to_float(row.get("vegas_total")),
            },
            "projectedSpread": to_float(row.get("kp_spread")) or to_float(row.get("bt_spread")),
            "projectedTotal": to_float(row.get("kp_total")) or to_float(row.get("bt_total")),
            "fanduelSpread": to_float(row.get("vegas_spread")),
            "fanduelTotal": to_float(row.get("vegas_total")),
            "edge": None,
            "edgeSummary": [],
            "travelDistanceMiles": None,
            "source": "cache",
            "isEdge": to_bool(row.get("is_edge")),
            "confidence": row.get("confidence") or None,
        }
        by_date.setdefault(row["date"], []).append(game)

    payload = {"dates": dict(sorted(by_date.items()))}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {sum(len(v) for v in payload['dates'].values())} games across {len(payload['dates'])} dates to {OUTPUT}")


if __name__ == "__main__":
    main()
