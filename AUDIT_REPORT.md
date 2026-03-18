# CollegeBasketballALGO — Full Audit Report
**Date:** 2026-03-15 (Selection Sunday)
**Auditor:** Claude Code
**Files audited:** `kenpom_predictor.py`, `slate_results.py`

---

## SECTION 1: CALCULATION AUDIT

### 1. KenPom-Derived Spread
**Verdict: WARNING** — Model uses a valid but non-standard formula

**Location:** `kenpom_predictor.py:363-378` (`predict_game()`)

The code does NOT use the standard KenPom formula. Instead it uses a lambda-shrinkage model:

```python
tempo    = (home.adj_t * away.adj_t) ** 0.48 * (68.4 ** 0.04)  # Geometric mean variant
eff_home = home.adj_o + 0.8905 * (away.adj_d - 100)            # Additive lambda adjustment
eff_away = away.adj_o + 0.8905 * (home.adj_d - 100)
pts_home = tempo * eff_home / 100
pts_away = tempo * eff_away / 100
spread   = -((pts_home - pts_away) + hca)
```

**Standard formula** would be:
```
poss = avg(home_AdjT, away_AdjT)
home_score = poss * (home_AdjO / 100) * (away_AdjD / 100)
away_score = poss * (away_AdjO / 100) * (home_AdjD / 100)
```

The lambda model is a legitimate regression-to-the-mean approach, and the tempo exponents sum to 1.0 (0.48×2 + 0.04 = 1.0), making the formula dimensionally consistent. However:
- The additive lambda adjustment diverges from the standard multiplicative approach for extreme AdjD values
- LAMBDA=0.8905 and the tempo exponents appear tuned but are hardcoded without documentation of how they were derived

**Sign convention:** PASS — `spread < 0` = home favored ✓ (line 371)
**Home court adjustment:** PASS — HCA = 1.9895 applied to spread, zeroed for neutral sites (lines 49, 364) ✓

### 2. KenPom-Derived Total (O/U)
**Verdict: WARNING** — Non-standard formula; HCA not reflected in total

**Location:** `kenpom_predictor.py:370`

```python
total = pts_home + pts_away
```

- Tempo is geometric mean (not arithmetic average as specified) — this is a deliberate modeling choice
- The efficiency adjustment uses the lambda-shrinkage model described above, not the standard `(AdjO/100) * (AdjD/100)` multiplicative approach
- HCA is NOT incorporated into the score predictions — it's only applied to the spread. In reality, home court advantage affects both teams' scoring. This makes the total prediction slightly inaccurate for non-neutral games.

### 3. T-Rank (Barttorvik) Spread and Total
**Verdict: PASS** — Consistent with KenPom model

**Location:** `kenpom_predictor.py:435`

```python
bt_result = predict_game(bt_home, bt_away, neutral=m.neutral)
```

Both KenPom and T-Rank data are fed through the identical `predict_game()` function. Same formula, same constants, same sign conventions. ✓

### 4. Edge Detection
**Verdict: FAIL** — `is_edge` includes total edge logic (should be spread-only)

**Location:** `kenpom_predictor.py:448-469`

| Sub-item | Verdict | Line | Explanation |
|----------|---------|------|-------------|
| Edge formula | WARNING | 423 | Uses `vegas_spread - kp_spread` (inverted sign convention from spec). Internally consistent — positive = model likes home more than Vegas. |
| EDGE_THRESHOLD on spread | PASS | 448 | `abs(kp_spread_edge) >= 3.0` correctly applied |
| EDGE_THRESHOLD on totals | **FAIL** | 449 | Total edge IS being thresholded: `is_total_edge = kp_total_edge is not None and abs(kp_total_edge) >= EDGE_THRESHOLD` |
| `is_edge` flag | **FAIL** | 469 | `is_edge = is_spread_edge or is_total_edge` — totals ARE included. Per spec, `is_edge` should only reflect spread edges. |

**Impact:** Games are being flagged as edge plays solely due to total mismatches. Looking at the predictions log, many "edge" games have no spread edge but large total edges (e.g., Merrimack vs Sacred Heart: spread edge -0.4, total edge 13.9 → flagged as edge). This produces false positives for spread betting.

### 5. Performance Metrics in `performance_summary()`
**Verdict: PASS with minor issue**

**Location:** `slate_results.py:179-258`

| Metric | Verdict | Lines | Explanation |
|--------|---------|-------|-------------|
| KP Spread MAE | PASS | 185-190 | `mean(\|kp_spread - actual_spread\|)` ✓ |
| Total MAE | PASS | 186-191 | `mean(\|kp_total - actual_total\|)` ✓ |
| Moneyline (KP) | PASS | 193-207 | `sign(kp_spread) == sign(actual_spread)`, pushes excluded (`actual_val == 0`) ✓ |
| KP Spread ATS | PASS | 209-226 | Logic correct: `kp_says_home = kp_val < vegas_val`, `home_covered = actual_val < vegas_val` ✓ |
| BT Spread ATS | PASS | 228-244 | Same logic using bt_spread ✓ |
| ATS push handling | WARNING | 224 | Pushes against the spread (`actual_spread == vegas_spread`) are NOT excluded. They count as losses. Should be excluded for accurate ATS tracking. |

### 6. CSV Result Entry (`enter_results()`)
**Verdict: FAIL** — bt_* columns missing from manual entry path

**Location:** `slate_results.py:148-166`

The `enter_results()` function writes to `RESULTS_HEADERS` (20 columns) but only provides 16 fields — **`bt_home_score`, `bt_away_score`, `bt_total`, `bt_spread` are completely missing** from the `writer.writerow()` dict. DictWriter fills them with empty strings.

**Meanwhile, `check_results()` (line 432-454) correctly includes all bt_* columns.** So the auto-check path is fixed, but the manual entry path still has this bug.

**Additional data integrity issue:** The `results_log.csv` file has no header row and contains a mix of legacy 17-column rows and new 20-column rows. The `_read_csv()` function detects column count from the first row, so if the first row is legacy format, all subsequent 20-column rows have their last 3 fields silently dropped.

---

## SECTION 2: TODAY'S PICKS

**Date:** Sunday, March 15, 2026 — Selection Sunday

Today's slate is minimal (conference championship games only). ESPN API is unavailable from this environment, so predictions are computed directly from local KenPom/Barttorvik data files + web-sourced Vegas lines.

### Picks Table

| Matchup | KP Spread | BT Spread | Vegas Spread | Spread Edge | KP Total | BT Total | Vegas O/U | Total Edge | Consensus |
|---------|-----------|-----------|-------------|-------------|----------|----------|-----------|------------|-----------|
| Purdue @ Michigan [N] | Mich -4.8 | Mich -4.0 | Mich -6.5 | +1.7 (KP) | 168.5 | 172.1 | 153.5 | +15.0 / +18.6 | — |
| Arkansas @ Vanderbilt [N] | Vandy -0.5 | Vandy -0.3 | Vandy -1.5 | +1.0 (KP) | 177.9 | 178.5 | 164.5 | +13.4 / +14.0 | — |

**Edge convention:** Positive edge = model thinks home team is better than Vegas does.

### Analysis

**Neither game crosses the 3.0-point EDGE_THRESHOLD for spread edges.** No official spread edge plays today.

#### Purdue @ Michigan (Big Ten Championship, Neutral Site)
- **KP Spread:** Michigan -4.8 | **BT Spread:** Michigan -4.0 | **Vegas:** Michigan -6.5
- Both models have Michigan winning by ~4-5 points. Vegas has Michigan favored by 6.5.
- **The edge is +1.7 (under threshold)** — both models think Purdue is getting too many points. This makes sense: Michigan (AdjO 128.4, AdjD 89.0) is elite on both ends, but Purdue's offense (AdjO 129.7) is #2 nationally. The gap is primarily defensive: Michigan's 89.0 AdjD vs Purdue's 98.5. Vegas seems to be weighting Michigan's regular-season dominance (31-2) more heavily.
- **Total:** Models say 168.5-172.1, Vegas says 153.5. The 15-18 point total gap is enormous but consistent with the systematic total over-prediction the model exhibits (the model does not discount tournament pace, which tends to be slower than regular season).
- **Lean:** Purdue +6.5 is interesting but below threshold. No official play. If forced, take **Purdue +6.5** — 4-5 point game.

#### Arkansas @ Vanderbilt (SEC Championship, Neutral Site)
- **KP Spread:** Vanderbilt -0.5 | **BT Spread:** Vanderbilt -0.3 | **Vegas:** Vanderbilt -1.5
- Dead heat in the models. Vanderbilt (AdjO 125.3, AdjD 98.7) vs Arkansas (AdjO 128.0, AdjD 102.5). Arkansas has the better offense, Vanderbilt the better defense. Similar tempo profiles.
- **The edge is +1.0 (under threshold)** — model and Vegas essentially agree this is a pick'em.
- **Total:** Models at ~178, Vegas at 164.5. Same systematic total overshoot.
- **Lean:** No play. This is a coin flip and the model agrees with Vegas.

### Ranked Picks (Highest to Lowest Confidence)

1. **No official spread edge plays today.** Selection Sunday has only 2 major conference championship games, and neither triggers the 3.0-point threshold.
2. **Soft lean:** Purdue +6.5 (1.7-pt edge, both models agree Purdue should cover). This is NOT a recommended bet — it's below threshold. But if you're betting recreationally, Purdue +6.5 is the best value on the board today.

---

## SECTION 3: IMPROVEMENT RECOMMENDATIONS

| # | Priority | Category | Description | Impact |
|---|----------|----------|-------------|--------|
| 1 | **Critical** | Bug Fix | `is_edge` includes total edge logic — games flagged as edge bets based solely on total mismatch, producing false positives for spread bettors | High — directly misleads bet recommendations |
| 2 | **Critical** | Bug Fix | `enter_results()` missing bt_* columns — T-Rank data lost on manual entry path | Medium — auto-check path works, but manual entry drops data |
| 3 | **Critical** | Data Quality | Barttorvik parser fails on 46/365 teams (12.6%) including Purdue, Michigan St., Ohio St., Indiana, Marquette, Maryland — teams with "recent game" annotation in the copy-paste | High — major programs missing from T-Rank predictions |
| 4 | **High** | Calculation Accuracy | HCA (1.9895 pts) is hardcoded, not derived from data. Should be configurable and ideally calibrated from results | Medium — current value is reasonable but static |
| 5 | **High** | Calculation Accuracy | Totals are systematically over-predicted (avg total error +15-20 pts based on log analysis). Model doesn't account for tournament pace regression or game context | High — total predictions are unreliable |
| 6 | **High** | Data Quality | ATS push handling — pushes against the spread count as losses in `performance_summary()` | Low-Medium — inflates loss count |
| 7 | **Medium** | Data Quality | `results_log.csv` mixes legacy 17-column and new 20-column rows without header — `_read_csv()` misreads newer rows when first row is legacy format | Medium — silent data corruption |
| 8 | **Medium** | Feature Addition | No confidence interval or uncertainty output — model produces point estimates only | Medium — no way to express uncertainty |
| 9 | **Medium** | Code Quality | Model constants (LAMBDA, TEMPO_EXP, TEMPO_LEAGUE_EXP) are hardcoded without documentation of derivation | Low — maintenance burden |
| 10 | **Medium** | Feature Addition | Discord alert deduplication — no mechanism to prevent re-alerting same games on re-run | Low-Medium — annoying but not data-affecting |
| 11 | **Low** | Feature Addition | No weighting for recent games — full-season KenPom/Barttorvik averages don't capture hot/cold streaks | Medium — would improve late-season accuracy |

### Top 3 Fixes with Complete Code

#### Fix #1: Remove total edge from `is_edge` flag

**File:** `kenpom_predictor.py`, lines 448-469

```python
# BEFORE (buggy):
is_spread_edge = kp_spread_edge is not None and abs(kp_spread_edge) >= EDGE_THRESHOLD
is_total_edge  = kp_total_edge  is not None and abs(kp_total_edge)  >= EDGE_THRESHOLD
# ...
"is_edge": is_spread_edge or is_total_edge,

# AFTER (fixed):
is_spread_edge = kp_spread_edge is not None and abs(kp_spread_edge) >= EDGE_THRESHOLD
is_total_edge  = kp_total_edge  is not None and abs(kp_total_edge)  >= EDGE_THRESHOLD
# ...
"is_edge": is_spread_edge,  # Only spread edges trigger edge flag
```

#### Fix #2: Add bt_* columns to `enter_results()`

**File:** `slate_results.py`, lines 148-166

```python
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
```

#### Fix #3: Robust Barttorvik parser (handles "recent game" annotation lines)

**File:** `kenpom_predictor.py`, replace `parse_barttorvik()` function

```python
def parse_barttorvik(filepath: str) -> dict[str, Team]:
    """
    Parse raw Barttorvik T-Rank copy-paste from barttorvik.com/trank.php.

    Handles two copy-paste variants:
      Standard:  {Rk}\t{Team}\t{Conf}\t{G}\t{Rec}   (5+ tab fields)
      Annotated: {Rk}\t{Team}                        (2 tab fields)
                 {annotation}\t{Conf}\t{G}\t{Rec}    (next line has extra game info)

    In both cases, AdjOE appears on the line after the record line,
    and subsequent stats follow the same staggered pattern.
    """
    teams = {}
    with open(filepath, "r") as f:
        lines = [line.rstrip("\n") for line in f]

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        parts = line.split("\t")

        # Detect team header: first field is a rank (digit), second is a team name
        if len(parts) < 2 or not parts[0].strip().isdigit() or not parts[1].strip() or not parts[1].strip()[0].isalpha():
            i += 1
            continue

        team_name = parts[1].strip()

        # Standard format: 5+ fields with record containing "-"
        if len(parts) >= 5 and "-" in parts[4].strip():
            data_start = i + 1  # AdjOE is on the next line
        # Annotated format: 2 fields, next line has annotation + conf + G + rec
        elif len(parts) < 5:
            next_parts = lines[i + 1].strip().split("\t") if i + 1 < len(lines) else []
            if len(next_parts) >= 4 and "-" in next_parts[-1].strip():
                data_start = i + 2  # AdjOE is two lines down
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
            teams[team_name] = Team(name=team_name, adj_o=adj_o, adj_d=adj_d, adj_t=adj_t)
        except (ValueError, IndexError):
            pass

        i += 1

    return teams
```
