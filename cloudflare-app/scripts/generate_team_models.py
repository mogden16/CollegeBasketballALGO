#!/usr/bin/env python3
"""
Generate cloudflare-app/data/team-models.json from kenpom_raw.txt and barttorvik_raw.txt.

Reads the same raw paste files used by kenpom_predictor.py, so team-models.json
always reflects the latest data after a KenPom/Barttorvik refresh.

Run from any directory — paths resolve relative to this script's location:
  python cloudflare-app/scripts/generate_team_models.py
  python generate_team_models.py          (if cwd is scripts/)
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path resolution ───────────────────────────────────────────────────────────
SCRIPTS_DIR    = Path(__file__).resolve().parent
CF_DIR         = SCRIPTS_DIR.parent
REPO_ROOT      = CF_DIR.parent

KENPOM_FILE    = REPO_ROOT / "kenpom_raw.txt"
BARTTORVIK_FILE = REPO_ROOT / "barttorvik_raw.txt"
OUTPUT_FILE    = CF_DIR / "data" / "team-models.json"


# ══════════════════════════════════════════════════════
# PARSE KENPOM_RAW.TXT
# ══════════════════════════════════════════════════════
def build_kenpom_section() -> dict:
    """
    Parse kenpom_raw.txt (tab-separated copy-paste from kenpom.com).

    Column layout after interspersed rank columns are counted:
      0: Rk   1: Team   2: Conf   3: W-L   4: NetRtg
      5: ORtg  6: ORtg_rank   7: DRtg   8: DRtg_rank
      9: AdjT  10: AdjT_rank  11: Luck   ...

    The Team field may include a trailing rank annotation (e.g. "Duke 1"),
    which is stripped to recover the canonical team name.
    """
    result = {}
    with open(KENPOM_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if not parts[0].strip().isdigit():
                continue
            if len(parts) < 12:
                continue
            try:
                raw_name = parts[1].strip()
                # Strip leading/trailing rank numbers (e.g. "1 Duke 1" → "Duke")
                name = re.sub(r"\s+", " ",
                              re.sub(r"\s*\d+$", "",
                                     re.sub(r"^\d+\s*", "", raw_name))).strip()
                record  = parts[3].strip()
                net_rtg = float(parts[4].strip().lstrip("+"))
                adj_o   = float(parts[5].strip())
                adj_d   = float(parts[7].strip())
                adj_t   = float(parts[9].strip())
                luck    = float(parts[11].strip().lstrip("+"))

                result[name] = {
                    "adjO":       adj_o,
                    "adjD":       adj_d,
                    "adjT":       adj_t,
                    "sourceName": name,
                    "record":     record,
                    "netRtg":     round(net_rtg, 4),
                    "ortg":       adj_o,
                    "drtg":       adj_d,
                    "luck":       round(luck, 4),
                }
            except (ValueError, IndexError):
                continue
    return result


# ══════════════════════════════════════════════════════
# PARSE BARTTORVIK_RAW.TXT
# ══════════════════════════════════════════════════════
def build_trank_section() -> dict:
    """
    Parse barttorvik_raw.txt (copy-paste from barttorvik.com/trank.php).

    Barttorvik produces a staggered multi-line format where each stat and its
    rank appear on interleaved lines. Replicates the parse_barttorvik logic
    from kenpom_predictor.py without requiring that module to be imported.

    Stat offsets from the first data line after the team header:
      data+0:  AdjOE
      data+1:  AdjOE_rank  \\t  AdjDE
      ...
      data+17: 3PRD_rank   \\t  AdjT
      data+18: AdjT_rank   \\t  WAB
    """
    if not BARTTORVIK_FILE.exists():
        print(f"  WARNING: {BARTTORVIK_FILE} not found — T-Rank section will be empty.")
        return {}

    result = {}
    with open(BARTTORVIK_FILE, encoding="utf-8") as f:
        lines = [line.rstrip("\n") for line in f]

    i = 0
    while i < len(lines):
        parts = lines[i].strip().split("\t")

        # Team header: first field is a rank digit, second is a team name
        if (len(parts) < 2
                or not parts[0].strip().isdigit()
                or not parts[1].strip()
                or not parts[1].strip()[0].isalpha()):
            i += 1
            continue

        team_name = parts[1].strip()

        # Standard format: 5+ fields, record field contains "-"
        if len(parts) >= 5 and "-" in parts[4].strip():
            data_start = i + 1
        # Annotated format: short header line, next line has conf/G/record
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
            result[team_name] = {
                "adjO":       adj_o,
                "adjD":       adj_d,
                "adjT":       adj_t,
                "sourceName": team_name,
            }
        except (ValueError, IndexError):
            pass

        i += 1

    return result


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════
def main():
    print(f"\n{'═'*60}")
    print(f"  Generate team-models.json")
    print(f"{'═'*60}\n")

    if not KENPOM_FILE.exists():
        print(f"ERROR: {KENPOM_FILE} not found.")
        sys.exit(1)

    print(f"  Reading {KENPOM_FILE.name} ...")
    kenpom = build_kenpom_section()
    print(f"  Parsed {len(kenpom)} KenPom teams.")

    print(f"  Reading {BARTTORVIK_FILE.name} ...")
    trank = build_trank_section()
    if trank:
        print(f"  Parsed {len(trank)} T-Rank teams.")

    # Sorted union of all names for the teams index array
    all_names = sorted(set(kenpom.keys()) | set(trank.keys()))
    print(f"  Total unique teams: {len(all_names)}")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "generatedAt": generated_at,
        "kenpom":      kenpom,
        "trank":       trank,
        "teams":       all_names,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\n  Written → {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    print(f"  generatedAt: {generated_at}")
    print(f"\n{'═'*60}\n")


if __name__ == "__main__":
    main()
