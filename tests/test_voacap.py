import unittest

from ftdx1200.voacap import BANDS, TARGETS, predict_chart


class VoacapTests(unittest.TestCase):
    def test_chart_has_eight_bands(self):
        chart = predict_chart(32.8, -96.8, power=100, mode="ssb", ssn=69)
        self.assertTrue(chart["ok"])
        self.assertEqual(len(chart["bands"]), len(BANDS))
        self.assertEqual(len(chart["paths"]), len(TARGETS))
        self.assertIn("US East", [path["name"] for path in chart["paths"]])
        for path in chart["paths"]:
            self.assertGreater(path["muf"], 1)
        for path in chart["paths"]:
            self.assertEqual(len(path["bands"]), len(BANDS))
            for cell in path["bands"]:
                self.assertGreaterEqual(cell["rel"], 0)
                self.assertLessEqual(cell["rel"], 1)


if __name__ == "__main__":
    unittest.main()
