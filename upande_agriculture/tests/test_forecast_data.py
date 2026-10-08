import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


class TestForecastData(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse()
		self.today = getdate(nowdate())

	def test_sections_lists_variety_and_plants(self):
		row = [r for r in data.sections(greenhouse=self.gh) if r.section == "S1"][0]
		self.assertEqual((row.variety, row.plants), ("FC-ROSE", 1000))

	def test_latest_round_takes_latest_count_per_plot_within_three_days(self):
		p1, p2 = make_plot(self.gh, "S1", 1), make_plot(self.gh, "S1", 2)
		make_count(p1, add_days(self.today, -10), {"Rice": 99})   # an older round: ignored
		make_count(p1, add_days(self.today, -2), {"Rice": 10})    # superseded below
		make_count(p1, add_days(self.today, -1), {"Rice": 20, "Pea": 5})
		make_count(p2, add_days(self.today, -3), {"Rice": 10, "Marble": 4})  # same round, unknown stage kept
		r = data.latest_round(self.gh, "S1", self.today)
		self.assertEqual(r["date"], add_days(self.today, -1))
		self.assertEqual(r["plots"], 2)
		self.assertEqual(r["rates"], {"Rice": 1.5, "Pea": 0.25, "Marble": 0.2})

	def test_latest_round_none_when_too_old(self):
		make_count(make_plot(self.gh, "S1", 1), add_days(self.today, -40), {"Rice": 10})
		self.assertIsNone(data.latest_round(self.gh, "S1", self.today))

	def test_cuts_by_section_and_day(self):
		make_harvest(self.gh, "S1", "FC-ROSE", add_days(self.today, -2), 40)
		make_harvest(self.gh, "S1", "FC-ROSE", add_days(self.today, -2), 10)
		make_harvest(self.gh, "S2", "FC-ROSE", add_days(self.today, -2), 7)
		c = data.cuts(self.gh, "S1", add_days(self.today, -5), self.today)
		self.assertEqual(c, {add_days(self.today, -2): 50.0})

	def test_params_defaults_then_fitted(self):
		p = data.params("FC-ROSE")
		self.assertEqual((p["time_scale"], p["model_version"]), (1.0, "defaults"))
		frappe.get_doc({"doctype": "Harvest Forecast Calibration", "variety": "FC-ROSE", "time_scale": 0.9,
			"bud_survival": 0.8, "regrowth_days": 63, "regrowth_yield": 0.7, "fitted_on": frappe.utils.now(),
			"error_bands": json.dumps({"0-2": [0.9, 1.1, 0.04]})}).insert(ignore_permissions=True)
		p = data.params("FC-ROSE")
		self.assertEqual((p["time_scale"], p["regrowth_days"], p["error_bands"]["0-2"][0]), (0.9, 63, 0.9))
		self.assertTrue(p["model_version"].startswith("FC-ROSE@"))

	def test_temperatures(self):
		for i, t in enumerate((18, 20)):
			frappe.get_doc({"doctype": "Greenhouse Temperature", "greenhouse": self.gh,
				"date": add_days(self.today, -i), "mean_temp": t}).insert(ignore_permissions=True)
		self.assertEqual(data.recent_temp(self.gh, self.today), 19)
		self.assertIsNone(data.recent_temp("NO SUCH GH", self.today))
