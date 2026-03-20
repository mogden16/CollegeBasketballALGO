#!/usr/bin/env python3
"""Rebuild Cloudflare Worker team-model payloads from repo source data."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "cloudflare-app" / "data" / "team-models.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kenpom_predictor import parse_barttorvik, parse_kenpom  # noqa: E402


def main() -> None:
    kp_teams = parse_kenpom(str(ROOT / "kenpom_raw.txt"))
    tr_teams = parse_barttorvik(str(ROOT / "barttorvik_raw.txt"))

    payload = {
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "kenpom": {},
        "trank": {},
        "teams": sorted(set(kp_teams) | set(tr_teams)),
    }

    for name, team in kp_teams.items():
        payload["kenpom"][name] = {
            "adjO": team.adj_o,
            "adjD": team.adj_d,
            "adjT": team.adj_t,
            "sourceName": name,
            "record": None,
            "netRtg": round(team.adj_o - team.adj_d, 2),
            "ortg": team.adj_o,
            "drtg": team.adj_d,
            "luck": None,
            "sosNetRtg": team.sos_netrtg,
            "ncSosNetRtg": team.ncsos_netrtg,
        }

    # Preserve richer KP fields already present in the bundled payload if they
    # exist, while still updating the new SOS fields from the repo parser.
    if OUTPUT.exists():
        try:
            existing = json.loads(OUTPUT.read_text())
            for name, current in existing.get("kenpom", {}).items():
                if name in payload["kenpom"]:
                    payload["kenpom"][name]["record"] = current.get("record")
                    payload["kenpom"][name]["luck"] = current.get("luck")
                    payload["kenpom"][name]["netRtg"] = current.get("netRtg", payload["kenpom"][name]["netRtg"])
        except Exception:
            pass

    for name, team in tr_teams.items():
        payload["trank"][name] = {
            "adjO": team.adj_o,
            "adjD": team.adj_d,
            "adjT": team.adj_t,
            "sourceName": name,
        }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {len(payload['kenpom'])} KenPom teams and {len(payload['trank'])} T-Rank teams to {OUTPUT}")


if __name__ == "__main__":
    main()
