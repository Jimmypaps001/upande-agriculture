import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_cycle, make_greenhouse, make_harvest, make_plot, make_variety)


class TestForecastData(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse()
		self.today = getdate(nowdate())

	def test_sections_lists_variety_and_plants(self):
		row = [r for r in data.sections(greenhouse=self.gh) if r.section == "FC-ROSE"][0]
		self.assertEqual((row.variety, row.plants, row.from_bed, row.to_bed, row.source, row.shared_with),
			("FC-ROSE", 1000, 1, 9, "Crop Cycle", []))

	def test_latest_round_takes_latest_count_per_plot_within_three_days(self):
		p1, p2 = make_plot(self.gh, "FC-ROSE", 1), make_plot(self.gh, "FC-ROSE", 2)
		make_count(p1, add_days(self.today, -10), {"Rice": 99})   # an older round: ignored
		make_count(p1, add_days(self.today, -2), {"Rice": 10})    # superseded below
		make_count(p1, add_days(self.today, -1), {"Rice": 20, "Pea": 5})
		make_count(p2, add_days(self.today, -3), {"Rice": 10, "Marble": 4})  # same round, unknown stage kept
		r = data.latest_round(self.gh, "FC-ROSE", self.today)
		self.assertEqual(r["date"], add_days(self.today, -1))
		self.assertEqual(r["plots"], 2)
		self.assertEqual(r["rates"], {"Rice": 1.5, "Pea": 0.25, "Marble": 0.2})

	def test_latest_round_none_when_too_old(self):
		make_count(make_plot(self.gh, "FC-ROSE", 1), add_days(self.today, -70), {"Rice": 10})
		self.assertIsNone(data.latest_round(self.gh, "FC-ROSE", self.today))

	def test_cuts_by_variety_and_day(self):
		other = make_variety("FC-ROSE-B")
		gh2 = make_greenhouse("FC GH CUTS2", (("FC-ROSE", 500, None),))
		d = add_days(self.today, -2)
		make_harvest(self.gh, "AB", "FC-ROSE", d, 40)  # any old section stamp: the variety decides
		make_harvest(self.gh, None, "FC-ROSE", d, 10)
		make_harvest(self.gh, None, other, d, 7)
		make_harvest(gh2, None, "FC-ROSE", d, 3)
		self.assertEqual(data.cuts(self.gh, "FC-ROSE", add_days(self.today, -5), self.today), {d: 50.0})

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

	def test_units_from_crop_cycles(self):
		make_variety("FC-ROSE-B")
		make_variety("FC-CHRYS", has_variants=0)
		gh = make_greenhouse("FC GH CC", (("FC-ROSE", 1000, "1-10, 15-20"), ("FC-ROSE", 500, "30-40"),
			("FC-ROSE-B", 200, "nope"), ("FC-CHRYS", 300, "41-50")))
		cc = make_cycle(gh, "FC-ROSE-B", 900)
		frappe.db.set_value("Crop Cycle", cc, "plants_standing", 800)  # what is still standing beats what went in
		ended = make_cycle(gh, "FC-ROSE-B", 5000)
		frappe.db.set_value("Crop Cycle", ended, "status", "Ended")
		rows = data.sections(greenhouse=gh)
		self.assertEqual([(r.section, r.plants, r.from_bed, r.to_bed, r.source) for r in rows],
			[("FC-ROSE", 1500, 1, 40, "Crop Cycle"), ("FC-ROSE-B", 1000, None, None, "Crop Cycle")])
		self.assertEqual([r.section for r in data.sections(greenhouse=gh, variety="FC-ROSE-B")], ["FC-ROSE-B"])
		farm = frappe.db.get_value("Warehouse", gh, "custom_farm")
		self.assertIn(gh, {r.greenhouse for r in data.sections(farm=farm)})
		self.assertEqual(data.sections(farm="NO SUCH FARM"), [])

	def _gh_table(self, name, rows):
		gh = make_greenhouse(name, ())
		for i, (variety, plants, size) in enumerate(rows, start=1):
			frappe.get_doc({"doctype": "Varieties per GH", "parent": gh, "parenttype": "Warehouse",
				"parentfield": "custom_varieties_grown", "idx": i, "variety": variety, "no_of_plants": plants,
				"size_m2": size}).db_insert()
		return gh

	def test_units_from_greenhouse_varieties(self):
		if not frappe.db.table_exists("Varieties per GH"):
			self.skipTest("no Varieties per GH on this site")
		for v in ("FC-MADAM", "FC-TACAZZI", "FC-THIRD"):
			make_variety(v)
		make_variety("FC-CHRYS", has_variants=0)
		gh = self._gh_table("FC GH TABLE", [
			("FC-MADAM", "161,723", "21773"), ("FC-TACAZZI", "161723", "21773"),  # equal sub-houses, not shared
			("FC-MADAM", " 1 000 ", "50"),     # a second, small block of the same variety
			(None, "5", "1"),                  # blank variety: skipped
			("FC-THIRD", "5", "1"),
			("FC-CHRYS", "300", "10"),         # not a rose
			("FC-THIRD", "abc", None)])        # unreadable plants count as 0
		rows = {r.section: r for r in data.sections(greenhouse=gh)}
		self.assertEqual(set(rows), {"FC-MADAM", "FC-TACAZZI", "FC-THIRD"})
		self.assertEqual((rows["FC-MADAM"].plants, rows["FC-MADAM"].shared_with), (161723 + 1000, []))
		self.assertEqual((rows["FC-TACAZZI"].plants, rows["FC-TACAZZI"].shared_with), (161723, []))
		self.assertEqual((rows["FC-THIRD"].plants, rows["FC-THIRD"].shared_with), (5, []))
		self.assertTrue(all(r.source == "Greenhouse varieties" and r.from_bed is None for r in rows.values()))
		make_cycle(gh, "FC-THIRD", 77)  # any active crop cycle replaces the table for the greenhouse
		self.assertEqual([(r.section, r.plants) for r in data.sections(greenhouse=gh)], [("FC-THIRD", 77)])
