from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import calibrate, jobs
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


class TestForecastJobs(FrappeTestCase):
	def test_snapshot_writes_21_days_per_section_and_replaces_same_day(self):
		make_variety()
		gh = make_greenhouse("FC JOB", (("S1", 1, 9, "FC-ROSE", 1000),))
		make_count(make_plot(gh, "S1"), nowdate(), {"Rice": 10})
		jobs.daily_snapshot()
		jobs.daily_snapshot()
		n = frappe.db.count("Harvest Forecast Snapshot", {"greenhouse": gh, "made_on": nowdate()})
		self.assertEqual(n, 21)

	def test_accuracy_report_compares_snapshot_with_picks(self):
		from upande_agriculture.upande_agriculture.report.harvest_forecast_accuracy.harvest_forecast_accuracy import execute
		make_variety()
		gh = make_greenhouse("FC ACC", (("S1", 1, 9, "FC-ROSE", 1000),))
		yday = add_days(getdate(nowdate()), -1)
		frappe.get_doc({"doctype": "Harvest Forecast Snapshot", "made_on": add_days(yday, -1), "target_date": yday,
			"greenhouse": gh, "section": "S1", "variety": "FC-ROSE", "stems": 100, "low": 80, "high": 120}).insert()
		make_harvest(gh, "S1", "FC-ROSE", yday, 80)
		cols, rows = execute({"from_date": yday, "to_date": yday})
		row = [r for r in rows if r["greenhouse"] == gh][0]
		self.assertEqual((row["horizon"], row["forecast"], row["actual"]), ("0-2", 100, 80))
		self.assertAlmostEqual(row["wape"], 25.0)

	def test_snapshot_survives_one_failing_section(self):
		from upande_agriculture.forecast import service
		make_variety()
		gh = make_greenhouse("FC JOB BAD", (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 18, "FC-ROSE", 500)))
		real = service.forecast_section

		def flaky(row, *a, **kw):
			if row.greenhouse == gh and row.section == "S1":
				raise ValueError("bad section")
			return real(row, *a, **kw)
		with mock.patch.object(service, "forecast_section", flaky), mock.patch.object(frappe, "log_error"):
			jobs.daily_snapshot()
		self.assertEqual(frappe.db.count("Harvest Forecast Snapshot", {"greenhouse": gh, "section": "S1"}), 0)
		self.assertEqual(frappe.db.count("Harvest Forecast Snapshot", {"greenhouse": gh, "section": "S2"}), 21)

	def test_weekly_calibrate_commits_each_variety_and_rolls_back_a_failure(self):
		rows = [frappe._dict(variety="FC-A"), frappe._dict(variety="FC-B")]

		def fit(variety):
			if variety == "FC-A":
				raise ValueError("boom")
		with mock.patch.object(jobs.data, "sections", return_value=rows), \
				mock.patch.object(calibrate, "calibrate", side_effect=fit), \
				mock.patch.object(frappe.db, "commit") as commit, mock.patch.object(frappe.db, "rollback") as rollback, \
				mock.patch.object(frappe, "log_error"):
			done = jobs.weekly_calibrate()
		self.assertEqual(done, ["FC-B"])
		self.assertEqual((commit.call_count, rollback.call_count), (1, 1))
