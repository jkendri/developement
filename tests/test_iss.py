import unittest
from datetime import datetime, timedelta, timezone

from ftdx1200.iss import _Tracker, predict_passes

# CelesTrak ISS elements for 2026-10-17.
LINE1 = "1 25544U 98067A   26290.17683436  .00008767  00000+0  16757-3 0  9993"
LINE2 = "2 25544  51.6327  56.4129 0006776 273.6806  86.3408 15.48906925590665"
START = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
TLE = (LINE1, LINE2)


class IssTests(unittest.TestCase):
    def test_overhead_elevation_is_near_zenith(self):
        track = _Tracker(LINE1, LINE2, 0.0, 0.0)
        sub = track.subpoint(START)
        self.assertIsNotNone(sub)
        assert sub is not None
        over = _Tracker(LINE1, LINE2, sub[0], sub[1])
        look = over.look(START)
        self.assertIsNotNone(look)
        assert look is not None
        self.assertGreater(look[0], 89.0)

    def test_passes_meet_height_and_duration(self):
        out = predict_passes(32.8, -96.8, min_el=10, min_duration_min=2, count=5, tle=TLE, start=START)
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["passes"]), 5)
        prev = ""
        for item in out["passes"]:
            self.assertGreaterEqual(item["max_el"], 10)
            self.assertGreaterEqual(item["duration_sec"], 120)
            self.assertLess(item["aos"], item["peak"])
            self.assertLess(item["peak"], item["los"])
            self.assertGreater(item["aos"], prev)
            aos = datetime.fromisoformat(item["aos"].replace("Z", "+00:00"))
            los = datetime.fromisoformat(item["los"].replace("Z", "+00:00"))
            self.assertAlmostEqual((los - aos).total_seconds(), item["duration_sec"], delta=1)
            prev = item["aos"]

    def test_stricter_limits_drop_low_passes(self):
        loose = predict_passes(32.8, -96.8, min_el=10, min_duration_min=0, count=8, tle=TLE, start=START)
        high = predict_passes(32.8, -96.8, min_el=40, min_duration_min=0, count=8, tle=TLE, start=START)
        short = predict_passes(32.8, -96.8, min_el=10, min_duration_min=6, count=8, tle=TLE, start=START)
        self.assertLess(len(high["passes"]), len(loose["passes"]))
        self.assertTrue(all(item["max_el"] >= 40 for item in high["passes"]))
        self.assertTrue(high["passes"])
        self.assertTrue(all(item["duration_sec"] >= 360 for item in short["passes"]))
        none = predict_passes(32.8, -96.8, min_el=10, min_duration_min=12, count=8, tle=TLE, start=START)
        self.assertEqual(none["passes"], [])

    def test_count_caps_the_list(self):
        out = predict_passes(32.8, -96.8, min_el=0, min_duration_min=0, count=2, tle=TLE, start=START)
        self.assertEqual(len(out["passes"]), 2)

    def test_in_progress_pass_is_kept(self):
        first = predict_passes(32.8, -96.8, min_el=10, min_duration_min=0, count=1, tle=TLE, start=START)
        aos = datetime.fromisoformat(first["passes"][0]["aos"].replace("Z", "+00:00"))
        mid = aos + timedelta(minutes=1)
        again = predict_passes(32.8, -96.8, min_el=10, min_duration_min=0, count=1, tle=TLE, start=mid)
        self.assertEqual(again["passes"][0]["aos"], first["passes"][0]["aos"])


if __name__ == "__main__":
    unittest.main()
