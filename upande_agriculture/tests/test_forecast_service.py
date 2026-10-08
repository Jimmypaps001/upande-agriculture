import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data, service
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


def _row(gh, section):
	return [r for r in data.sections(greenhouse=gh) if r.section == section][0]


class TestForecastService(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.today = getdate(nowdate())
		self.gh = make_greenhouse("FC SVC " + self._testMethodName[5:12], (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 18, "FC-ROSE", None)))

	def test_counted_section_forecasts_buds_with_bands(self):
		make_count(make_plot(self.gh, "S1"), self.today, {"Showing colour": 10})  # 1 bud/plant, ~8 days out
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertEqual(len(f["daily"]), 21)
		self.assertFalse(any(f["flags"].values()))
		total = sum(d["stems"] for d in f["daily"])
		self.assertAlmostEqual(total, 1000 * 0.97, delta=5)
		d8 = f["daily"][8]
		self.assertLess(d8["low"], d8["stems"])
		self.assertGreater(d8["high"], d8["stems"])

	def test_no_plants_regrowth_only_flagged(self):
		make_harvest(self.gh, "S2", "FC-ROSE", add_days(self.today, -50), 100)
		make_count(make_plot(self.gh, "S2"), self.today, {"Rice": 10})
		f = service.forecast_section(_row(self.gh, "S2"))
		self.assertTrue(f["flags"]["no_plants"])
		self.assertEqual(sum(d["buds"] for d in f["daily"]), 0)
		self.assertGreater(sum(d["regrowth"] for d in f["daily"]), 50)

	def test_never_counted_and_stale_flags(self):
		self.assertTrue(service.forecast_section(_row(self.gh, "S1"))["flags"]["no_count"])
		make_count(make_plot(self.gh, "S1"), add_days(self.today, -9), {"Rice": 10})
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertTrue(f["flags"]["stale_count"])
		self.assertEqual(f["last_count"], add_days(self.today, -9))

	def test_forecast_all_covers_sections(self):
		got = {(f["greenhouse"], f["section"]) for f in service.forecast_all(greenhouse=self.gh)}
		self.assertEqual(got, {(self.gh, "S1"), (self.gh, "S2")})
