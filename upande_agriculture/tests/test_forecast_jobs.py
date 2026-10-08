import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import jobs
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
