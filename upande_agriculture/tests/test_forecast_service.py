from unittest import mock

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
		self.gh = make_greenhouse("FC SVC " + self._testMethodName[-14:], (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 18, "FC-ROSE", None)))

	def test_counted_section_forecasts_buds_with_bands(self):
		make_count(make_plot(self.gh, "S1"), self.today, {"Colour break": 10})  # 1 bud/plant, ~7 days out
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertEqual(len(f["daily"]), 56)
		self.assertFalse(any(f["flags"].values()))
		total = sum(d["stems"] for d in f["daily"])
		self.assertAlmostEqual(total, 1000 * 0.99, delta=5)
		d8 = f["daily"][8]
		self.assertLess(d8["low"], d8["stems"])
		self.assertGreater(d8["high"], d8["stems"])

	def test_no_plants_regrowth_only_flagged(self):
		make_harvest(self.gh, "S2", "FC-ROSE", add_days(self.today, -50), 100)
		make_count(make_plot(self.gh, "S2"), self.today, {"Pea": 10})
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

	def test_counted_stage_missing_from_protocol_still_forecast(self):
		from upande_agriculture.forecast import curves
		make_count(make_plot(self.gh, "S1"), self.today, {"Opening": 10})
		protocol = {k: v for k, v in curves.stage_params(curves.LEGACY_STAGES).items() if k != "Opening"}
		with mock.patch.object(data, "stages", return_value=protocol):
			f = service.forecast_section(_row(self.gh, "S1"))
		self.assertAlmostEqual(sum(d["stems"] for d in f["daily"]), 1000 * 0.99, delta=5)

	def test_one_failing_section_does_not_stop_the_others(self):
		real = service.forecast_section

		def flaky(row, *a, **kw):
			if row.section == "S1":
				raise ValueError("bad section")
			return real(row, *a, **kw)
		with mock.patch.object(service, "forecast_section", flaky), mock.patch.object(frappe, "log_error") as log:
			out = service.forecast_all(greenhouse=self.gh)
		self.assertEqual([f["section"] for f in out], ["S2"])
		self.assertTrue(log.called)

	def test_bad_error_bands_json_falls_back_to_defaults(self):
		frappe.get_doc({"doctype": "Harvest Forecast Calibration", "variety": "FC-ROSE", "fitted_on": nowdate(),
			"error_bands": "{not json"}).insert(ignore_permissions=True)
		self.assertEqual(data.params("FC-ROSE")["error_bands"], {})

	def _history(self, days, forecast, picked, ratio=1):
		for i in range(1, days + 1):
			d = add_days(self.today, -i)
			for ahead in (1, 2):  # snapshots made 1 and 2 days ahead, averaged
				frappe.get_doc({"doctype": "Harvest Forecast Snapshot", "made_on": add_days(d, -ahead), "target_date": d,
					"greenhouse": self.gh, "section": "S1", "variety": "FC-ROSE", "stems": forecast * ratio,
					"ratio": ratio}).insert()
			frappe.get_doc({"doctype": "Harvest Forecast Snapshot", "made_on": add_days(d, -5), "target_date": d,
				"greenhouse": self.gh, "section": "S1", "variety": "FC-ROSE", "stems": 9999}).insert()  # too far ahead
			if picked:
				make_harvest(self.gh, "S1", "FC-ROSE", d, picked)

	def test_section_ratio_corrects_and_flags(self):
		make_count(make_plot(self.gh, "S1"), self.today, {"Colour break": 10})
		self._history(12, 50, 75)
		with mock.patch.object(service, "section_ratio", return_value=(None, 12)):
			raw = service.forecast_section(_row(self.gh, "S1"))
		r, n = service.section_ratio(self.gh, "S1", self.today)
		self.assertEqual((round(r, 6), n), (1.5, 12))
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertEqual((round(f["ratio"], 6), f["ratio_days"]), (1.5, 12))
		self.assertTrue(f["flags"]["unrepresentative"])
		for a, b in zip(raw["daily"], f["daily"]):
			self.assertAlmostEqual(b["stems"], 1.5 * a["stems"], places=4)
			self.assertAlmostEqual(b["high"], 1.5 * a["high"], places=4)
		from upande_agriculture.forecast import api
		bay = api.get_bay_forecast(self.gh, "S1")
		self.assertEqual((bay["ratio"], bay["ratio_days"], bay["flags"]["unrepresentative"]), (1.5, 12, True))

	def test_section_ratio_needs_ten_days_and_300_stems(self):
		self._history(9, 50, 75)
		self.assertEqual(service.section_ratio(self.gh, "S1", self.today), (None, 9))
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertIsNone(f["ratio"])
		self.assertFalse(f["flags"]["unrepresentative"])

	def test_section_ratio_few_stems_ignored(self):
		self._history(12, 20, 30)  # 240 forecast stems
		self.assertEqual(service.section_ratio(self.gh, "S1", self.today), (None, 12))

	def test_section_ratio_is_clamped_and_measured_on_the_raw_forecast(self):
		self._history(12, 50, 400, ratio=1.1)  # stored stems 55 = raw 50 x 1.1
		self.assertEqual(service.section_ratio(self.gh, "S1", self.today), (2.0, 12))

	def test_section_ratio_close_to_one_not_flagged(self):
		self._history(12, 50, 55)
		r, _n = service.section_ratio(self.gh, "S1", self.today)
		self.assertAlmostEqual(r, 1.1)
		self.assertFalse(service.forecast_section(_row(self.gh, "S1"))["flags"]["unrepresentative"])
