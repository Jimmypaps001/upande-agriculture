"""Pure maths: python -m unittest upande_agriculture.tests.test_forecast_curves"""
import unittest

from upande_agriculture.forecast import curves


class TestCurves(unittest.TestCase):
	def test_day_mass_sums_to_one(self):
		self.assertAlmostEqual(sum(curves.day_mass(o, 28, 5.6) for o in range(-10, 70)), 1.0, places=6)

	def test_day_mass_peaks_at_mean(self):
		masses = [curves.day_mass(o, 10, 2) for o in range(21)]
		self.assertEqual(masses.index(max(masses)), 10)

	def test_stage_mass_is_flat_across_its_band(self):
		self.assertAlmostEqual(sum(curves.stage_mass(o, 20, 3, 1) for o in range(-20, 60)), 1.0, places=6)
		a, b, c = (curves.stage_mass(o, 20, 3, 1) for o in (19, 20, 21))
		self.assertLess(max(a, b, c) / min(a, b, c), 1.25)
		self.assertAlmostEqual(curves.stage_mass(5, 10, 0, 2), curves.day_mass(5, 10, 2))

	def test_stage_mass_floors_at_the_count_day(self):
		self.assertAlmostEqual(sum(curves.stage_mass(o, 1, 1, 1) for o in range(0, 11)), 1.0, places=6)
		self.assertEqual(curves.stage_mass(-1, 1, 1, 1), 0.0)

	def test_zero_spread_is_one_day(self):
		self.assertEqual(curves.day_mass(5, 5, 0), 1.0)
		self.assertEqual(curves.day_mass(4, 5, 0), 0.0)

	def test_defaults_when_no_rows(self):
		p = curves.stage_params(None)
		self.assertEqual(list(p), ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"])
		# band = midpoints to the neighbouring stages' days: (centre, half width)
		self.assertEqual({k: v[:2] for k, v in p.items()},
			{"Rice": (28.0, 3.0), "Pea": (21.75, 3.25), "Chickpea": (15.0, 3.5),
			"Showing colour": (8.5, 3.0), "Opening": (3.0, 2.5)})
		self.assertEqual(p["Showing colour"][0], 8.5)
		self.assertEqual(p["Rice"], (28.0, 3.0, 0.85))

	def test_blanks_take_defaults_unknown_without_days_dropped(self):
		p = curves.stage_params([
			{"stage_name": "Rice", "days_to_harvest": 30, "spread_days": None, "survival": None},
			{"stage_name": "Marble", "days_to_harvest": None},
			{"stage_name": "Bud", "days_to_harvest": 12, "spread_days": 1, "survival": 0.5},
		])
		self.assertEqual(p["Rice"], (30.0, 9.0, 0.85))  # half the 18-day gap to the next stage
		self.assertNotIn("Marble", p)
		self.assertEqual(p["Bud"], (12.0, 1.5, 0.5))  # spread floor 1.5

	def test_temperature_factor(self):
		self.assertAlmostEqual(curves.temperature_factor(18, 20), 12.7 / 14.7)
		self.assertEqual(curves.temperature_factor(None, 20), 1.0)
		self.assertEqual(curves.temperature_factor(18, 4), 1.0)

	def test_bands(self):
		self.assertEqual(curves.default_band(0), (0.85, 1.15))
		lo, hi = curves.default_band(14)
		self.assertAlmostEqual(lo, 0.71)
		self.assertAlmostEqual(hi, 1.29)
		self.assertEqual(curves.band(1, {"0-2": [0.9, 1.1, 0.05]}), (0.9, 1.1))
		self.assertEqual(curves.band(5, {"0-2": [0.9, 1.1, 0.05]}), curves.default_band(5))
		self.assertEqual(curves.bucket(30), "14-20")

	def test_quantile(self):
		self.assertEqual(curves.quantile([4, 1, 3, 2], 0.5), 2.5)
		self.assertEqual(curves.quantile([7], 0.9), 7)
