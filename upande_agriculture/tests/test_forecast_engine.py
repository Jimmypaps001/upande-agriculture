"""Pure maths: python -m unittest upande_agriculture.tests.test_forecast_engine"""
import datetime
import unittest

from upande_agriculture.forecast import engine

D = datetime.date(2026, 10, 5)
P = {"time_scale": 1.0, "bud_survival": 1.0, "regrowth_days": 56, "regrowth_yield": 1.0}


class TestEngine(unittest.TestCase):
	def test_bud_curve_total_and_peak(self):
		out = engine.bud_curve({"Rice": 2.0}, {"Rice": (10.0, 1.0, 0.5)}, D, 1.0, D, 21)
		self.assertAlmostEqual(sum(out), 1.0, places=4)  # 2 buds/plant x 50% survival
		self.assertEqual(out.index(max(out)), 10)

	def test_time_scale_moves_the_peak(self):
		out = engine.bud_curve({"Rice": 1.0}, {"Rice": (10.0, 1.0, 1.0)}, D, 0.5, D, 21)
		self.assertEqual(out.index(max(out)), 5)

	def test_unknown_stage_ignored(self):
		self.assertEqual(sum(engine.bud_curve({"Marble": 9.0}, {"Rice": (10.0, 1.0, 1.0)}, D, 1, D, 21)), 0)

	def test_counted_stage_missing_from_protocol_takes_its_default(self):
		# protocol lists only Rice; "opening" (any case) is still forecast from the default, ~3 days out
		out = engine.bud_curve({"opening": 1.0}, {"Rice": (10.0, 1.0, 1.0)}, D, 1.0, D, 21)
		self.assertGreater(sum(out), 0.9)
		self.assertLess(out.index(max(out)), 6)

	def test_counted_after_start_still_lands_later(self):
		# forecast from D, count made 2 days earlier: peak 8 days into the window
		out = engine.bud_curve({"Rice": 1.0}, {"Rice": (10.0, 1.0, 1.0)}, D - datetime.timedelta(days=2), 1, D, 21)
		self.assertEqual(out.index(max(out)), 8)

	def test_regrowth_lands_regrowth_days_after_the_cut(self):
		out = engine.regrowth_curve({D - datetime.timedelta(days=50): 100.0}, 56, D, 21)
		self.assertEqual(out.index(max(out)), 6)
		self.assertGreater(sum(out), 75)

	def test_regrowth_already_visible_is_skipped(self):
		old = D - datetime.timedelta(days=50)  # its bud reached rice at D-22, before the count on D
		new = D - datetime.timedelta(days=20)  # reaches rice at D+8: not yet counted
		self.assertEqual(sum(engine.regrowth_curve({old: 100.0}, 56, D, 21, D, 1.0, 28.0)), 0)
		self.assertGreater(sum(engine.regrowth_curve({new: 100.0}, 56, D, 40, D, 1.0, 28.0)), 0)

	def test_section_forecast_scales_by_plants_and_adds_regrowth(self):
		stages = {"Rice": (10.0, 1.0, 1.0)}
		f = engine.section_forecast(D, 21, 1000, {"date": D, "rates": {"Rice": 1.0}}, stages,
			{D - datetime.timedelta(days=50): 100.0}, P)
		self.assertAlmostEqual(sum(f["buds"]), 1000, delta=1)
		self.assertEqual([round(a + b, 6) for a, b in zip(f["buds"], f["regrowth"])], [round(x, 6) for x in f["stems"]])

	def test_no_plants_means_regrowth_only(self):
		f = engine.section_forecast(D, 21, 0, None, {"Rice": (10.0, 1.0, 1.0)},
			{D - datetime.timedelta(days=50): 100.0}, P)
		self.assertEqual(sum(f["buds"]), 0)
		self.assertGreater(sum(f["regrowth"]), 75)
