#!/usr/bin/env python3
"""Fetch fresh KenPom and BartTorvik ratings into the raw text files used by the model.

This script is designed for cron / scheduled execution.

KenPom notes:
- KenPom usually requires an active subscription and authenticated session.
- Prefer providing `KENPOM_COOKIE` with your browser's authenticated cookie string.
- As a fallback, the script can attempt a username/password login using
  `KENPOM_USERNAME` and `KENPOM_PASSWORD`, but KenPom may change its login flow.

BartTorvik notes:
- BartTorvik is public, but page structure can still change.

Example cron (6:00 AM UTC daily):
    0 6 * * * cd /workspace/CollegeBasketballALGO && /usr/bin/env python3 scripts/refresh_ratings.py >> refresh_ratings.log 2>&1
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KENPOM_OUTPUT = ROOT / "kenpom_raw.txt"
DEFAULT_BARTTORVIK_OUTPUT = ROOT / "barttorvik_raw.txt"
DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class RefreshError(RuntimeError):
    """Raised when a ratings source cannot be refreshed."""


@dataclass
class SourceResult:
    source: str
    output: Path
    row_count: int


def session_with_headers() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def coerce_text(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text


def parse_cookie_string(cookie_string: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in cookie_string.split(";"):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            cookies[key] = value
    return cookies


def login_kenpom(session: requests.Session) -> None:
    cookie_string = os.getenv("KENPOM_COOKIE", "").strip()
    if cookie_string:
        session.cookies.update(parse_cookie_string(cookie_string))
        return

    username = os.getenv("KENPOM_USERNAME", "").strip()
    password = os.getenv("KENPOM_PASSWORD", "").strip()
    if not username or not password:
        raise RefreshError(
            "KenPom credentials not configured. Set KENPOM_COOKIE or "
            "KENPOM_USERNAME / KENPOM_PASSWORD."
        )

    login_url = "https://kenpom.com/handlers/login_handler.php"
    payload = {"email": username, "password": password}
    response = session.post(login_url, data=payload, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    response.raise_for_status()


def fetch_html(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    return response.text


def extract_table_rows(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    best_rows: list[list[str]] = []
    for table in tables:
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [coerce_text(cell.get_text(" ", strip=True)) for cell in tr.find_all(["th", "td"])]
            if any(cells):
                rows.append(cells)
        if len(rows) > len(best_rows):
            best_rows = rows
    if not best_rows:
        raise RefreshError("No HTML table rows found in fetched page.")
    return best_rows


def looks_like_kenpom_page(html: str) -> bool:
    lowered = html.lower()
    return "team" in lowered and ("adjt" in lowered or "adj. t" in lowered or "adjt." in lowered)


def looks_like_barttorvik_page(html: str) -> bool:
    lowered = html.lower()
    return "adjoe" in lowered and "adjde" in lowered and "barthag" in lowered


def numeric_prefix(value: str) -> bool:
    return bool(re.match(r"^\d+", value.strip()))


def find_column_index(header: list[str], aliases: Iterable[str]) -> int | None:
    normalized = [coerce_text(cell).lower() for cell in header]
    for alias in aliases:
        alias_norm = alias.lower()
        for idx, col in enumerate(normalized):
            if col == alias_norm:
                return idx
    return None


def write_tsv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerows(rows)


def fetch_kenpom(output: Path) -> SourceResult:
    session = session_with_headers()
    login_kenpom(session)
    html = fetch_html(session, "https://kenpom.com/")
    if not looks_like_kenpom_page(html):
        raise RefreshError(
            "Fetched KenPom page did not look like the ratings table. Your session may be unauthenticated."
        )

    rows = extract_table_rows(html)
    header = next((row for row in rows if any("team" == cell.lower() for cell in row)), None)
    if header is None:
        raise RefreshError("Could not locate the KenPom header row.")

    team_idx = find_column_index(header, ["team"])
    conf_idx = find_column_index(header, ["conf"])
    rec_idx = find_column_index(header, ["w-l", "rec"])
    net_idx = find_column_index(header, ["netrtg", "net rtg", "adjem"])
    off_idx = find_column_index(header, ["ortg", "adjoe", "adjoe"])
    def_idx = find_column_index(header, ["drtg", "adjde", "adjde"])
    tempo_idx = find_column_index(header, ["adjt", "adj t", "adj. t"])
    if None in (team_idx, conf_idx, rec_idx, net_idx, off_idx, def_idx, tempo_idx):
        raise RefreshError("KenPom columns were not found in the fetched table.")

    normalized_rows: list[list[str]] = [
        ["Rk", "Team", "Conf", "W-L", "NetRtg", "ORtg", "ORtg_rank", "DRtg", "DRtg_rank", "AdjT"]
    ]
    row_count = 0
    for row in rows:
        if len(row) <= max(team_idx, conf_idx, rec_idx, net_idx, off_idx, def_idx, tempo_idx):
            continue
        if not row or not numeric_prefix(row[0]):
            continue
        normalized_rows.append(
            [
                row[0],
                row[team_idx],
                row[conf_idx],
                row[rec_idx],
                row[net_idx],
                row[off_idx],
                "",
                row[def_idx],
                "",
                row[tempo_idx],
            ]
        )
        row_count += 1

    if row_count == 0:
        raise RefreshError("KenPom table was fetched but no team rows were parsed.")

    write_tsv(output, normalized_rows)
    return SourceResult("kenpom", output, row_count)


def fetch_barttorvik(output: Path, year: int) -> SourceResult:
    session = session_with_headers()
    html = fetch_html(session, f"https://barttorvik.com/trank.php?year={year}")
    if not looks_like_barttorvik_page(html):
        raise RefreshError("Fetched BartTorvik page did not look like the T-Rank table.")

    rows = extract_table_rows(html)
    header = next((row for row in rows if any(cell.lower() == "team" for cell in row)), None)
    if header is None:
        raise RefreshError("Could not locate the BartTorvik header row.")

    desired_columns = {
        "Rk": ["rk"],
        "Team": ["team"],
        "Conf": ["conf"],
        "G": ["g"],
        "Rec": ["rec", "w-l"],
        "AdjOE": ["adjoe"],
        "AdjDE": ["adjde"],
        "Barthag": ["barthag"],
        "Adj T.": ["adj t.", "adj t", "adjt"],
        "WAB": ["wab"],
    }
    indices: dict[str, int] = {}
    for name, aliases in desired_columns.items():
        idx = find_column_index(header, aliases)
        if idx is None:
            raise RefreshError(f"BartTorvik column '{name}' was not found in the fetched table.")
        indices[name] = idx

    normalized_rows = [list(desired_columns.keys())]
    row_count = 0
    for row in rows:
        if len(row) <= max(indices.values()):
            continue
        if not row or not numeric_prefix(row[indices["Rk"]]):
            continue
        normalized_rows.append([row[indices[name]] for name in desired_columns.keys()])
        row_count += 1

    if row_count == 0:
        raise RefreshError("BartTorvik table was fetched but no team rows were parsed.")

    write_tsv(output, normalized_rows)
    return SourceResult("barttorvik", output, row_count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-kenpom", action="store_true", help="Skip refreshing KenPom.")
    parser.add_argument("--skip-barttorvik", action="store_true", help="Skip refreshing BartTorvik.")
    parser.add_argument("--kenpom-output", type=Path, default=DEFAULT_KENPOM_OUTPUT)
    parser.add_argument("--barttorvik-output", type=Path, default=DEFAULT_BARTTORVIK_OUTPUT)
    parser.add_argument(
        "--year",
        type=int,
        default=datetime.now(timezone.utc).year,
        help="Season year for the BartTorvik T-Rank page.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    results: list[SourceResult] = []
    failures: list[str] = []

    if not args.skip_kenpom:
        try:
            results.append(fetch_kenpom(args.kenpom_output))
        except Exception as exc:  # noqa: BLE001 - surface actionable fetch errors in cron logs
            failures.append(f"KenPom refresh failed: {exc}")

    if not args.skip_barttorvik:
        try:
            results.append(fetch_barttorvik(args.barttorvik_output, args.year))
        except Exception as exc:  # noqa: BLE001 - surface actionable fetch errors in cron logs
            failures.append(f"BartTorvik refresh failed: {exc}")

    for result in results:
        print(f"[{result.source}] wrote {result.row_count} rows to {result.output}")

    if failures:
        for failure in failures:
            print(failure)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
