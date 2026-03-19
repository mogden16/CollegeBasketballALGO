import unittest
from datetime import datetime, timezone

from team_name_utils import normalize_team_name
from kenpom_predictor import Team, predict_game, LAMBDA, TEMPO_SCALE


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


    def test_predict_game_uses_each_team_tempo_for_scoring(self):
        home = Team(name="Home", adj_o=120.0, adj_d=95.0, adj_t=70.0)
        away = Team(name="Away", adj_o=110.0, adj_d=90.0, adj_t=60.0)

        result = predict_game(home, away, neutral=True)

        expected_home = (((home.adj_o + away.adj_d) / 2) * LAMBDA) * (TEMPO_SCALE * home.adj_t / 100)
        expected_away = (((away.adj_o + home.adj_d) / 2) * LAMBDA) * (TEMPO_SCALE * away.adj_t / 100)
        legacy_shared_tempo_home = (TEMPO_SCALE * ((home.adj_t + away.adj_t) / 2)) * (((home.adj_o + away.adj_d) / 2) * LAMBDA) / 100

        self.assertAlmostEqual(result["home_score"], round(expected_home, 1))
        self.assertAlmostEqual(result["away_score"], round(expected_away, 1))
        self.assertNotAlmostEqual(result["home_score"], round(legacy_shared_tempo_home, 1))
        self.assertEqual(result["tempo"], round((TEMPO_SCALE * home.adj_t + TEMPO_SCALE * away.adj_t) / 2, 1))

    def test_today_date_behavior(self):
        dt = datetime(2026, 3, 12, 1, 30, tzinfo=timezone.utc)
        self.assertRegex(local_iso_date(dt), r"\d{4}-\d{2}-\d{2}")


if __name__ == "__main__":
    unittest.main()
