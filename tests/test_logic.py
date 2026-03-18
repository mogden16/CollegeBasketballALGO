import unittest
from datetime import datetime, timezone
import tempfile
from pathlib import Path

from kenpom_predictor import parse_barttorvik, parse_kenpom
from team_name_utils import normalize_team_name


def compute_side_edge(kp_edge, tr_edge, threshold, distance=None):
    pts = {"home": 0, "away": 0}
    if kp_edge is not None and abs(kp_edge) >= threshold:
        pts["home" if kp_edge > 0 else "away"] += 1
    if tr_edge is not None and abs(tr_edge) >= threshold:
        pts["home" if tr_edge > 0 else "away"] += 1
    if distance and distance[0] != distance[1]:
        pts["home" if distance[0] < distance[1] else "away"] += 1
    score = max(pts.values())
    return score, score > 1


def local_iso_date(dt: datetime) -> str:
    return dt.astimezone().strftime("%Y-%m-%d")


class LogicTests(unittest.TestCase):
    def test_team_normalization_removes_periods(self):
        self.assertEqual(normalize_team_name("N.C. State"), "nc state")
        self.assertEqual(normalize_team_name(" St. John's "), "st johns")

    def test_edge_score_and_highlight(self):
        score, highlight = compute_side_edge(4.0, 3.0, 3.0, distance=(120.0, 300.0))
        self.assertEqual(score, 3)
        self.assertTrue(highlight)

        score, highlight = compute_side_edge(4.0, -4.0, 3.0)
        self.assertEqual(score, 1)
        self.assertFalse(highlight)

    def test_today_date_behavior(self):
        dt = datetime(2026, 3, 12, 1, 30, tzinfo=timezone.utc)
        self.assertRegex(local_iso_date(dt), r"\d{4}-\d{2}-\d{2}")

    def test_parse_kenpom_normalized_tsv(self):
        content = "\n".join([
            "Rk\tTeam\tConf\tW-L\tNetRtg\tORtg\tORtg_rank\tDRtg\tDRtg_rank\tAdjT",
            "1\tDuke\tACC\t27-2\t+39.62\t127.7\t\t88.1\t\t65.4",
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "kenpom.tsv"
            path.write_text(content)
            teams = parse_kenpom(str(path))
        self.assertIn("Duke", teams)
        self.assertEqual(teams["Duke"].adj_o, 127.7)
        self.assertEqual(teams["Duke"].adj_d, 88.1)
        self.assertEqual(teams["Duke"].adj_t, 65.4)

    def test_parse_barttorvik_normalized_tsv(self):
        content = "\n".join([
            "Rk\tTeam\tConf\tG\tRec\tAdjOE\tAdjDE\tBarthag\tAdj T.\tWAB",
            "1\tMichigan\tB10\t29\t27-2\t129.3\t91.3\t.9820\t71.6\t+11.1",
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "barttorvik.tsv"
            path.write_text(content)
            teams = parse_barttorvik(str(path))
        self.assertIn("Michigan", teams)
        self.assertEqual(teams["Michigan"].adj_o, 129.3)
        self.assertEqual(teams["Michigan"].adj_d, 91.3)
        self.assertEqual(teams["Michigan"].adj_t, 71.6)


if __name__ == "__main__":
    unittest.main()
