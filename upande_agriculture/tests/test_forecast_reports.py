from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)
from upande_agriculture.upande_agriculture.report.section_harvest_forecast.section_harvest_forecast import (
	execute as section_report)
from upande_agriculture.upande_agriculture.report.weekly_harvest_forecast.weekly_harvest_forecast import (
	execute as weekly_report)


class TestForecastReports(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse("FC REP", (("FC-ROSE", 1000, "1-9"),))
		make_count(make_plot(self.gh, "FC-ROSE"), nowdate(), {"Colour break": 10})

	def test_section_report_has_a_column_per_day(self):
		cols, rows = section_report({"greenhouse": self.gh, "days": 14})
		self.assertEqual(sum(1 for c in cols if c["fieldname"].startswith("d")), 14)
		row = rows[0]
		self.assertAlmostEqual(row["total"], sum(row[f"d{i}"] for i in range(14)), delta=14)
		self.assertGreater(row["total"], 900)

	def test_weekly_report_adds_actual_so_far_this_week(self):
		today = getdate(nowdate())
		monday = add_days(today, -today.weekday())
		if monday < today:
			make_harvest(self.gh, "FC-ROSE", "FC-ROSE", monday, 50)
		cols, rows = weekly_report({"weeks": 3})
		ours = [r for r in rows if r["variety"] == "FC-ROSE"]
		self.assertEqual(len(ours), 3)
		self.assertEqual(ours[0]["actual"], 50 if monday < today else 0)
		self.assertAlmostEqual(ours[0]["total"], ours[0]["actual"] + ours[0]["forecast"], delta=1)

	def test_weekly_report_covers_eight_weeks_by_default(self):
		cols, rows = weekly_report({})
		self.assertEqual(len([r for r in rows if r["variety"] == "FC-ROSE"]), 8)

	def test_sales_user_can_run_weekly_report(self):
		import frappe
		from frappe.desk.query_report import run
		email = "fc-sales@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "FC Sales",
				"send_welcome_email": 0, "roles": [{"role": "Sales User"}]}).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			out = run("Weekly Harvest Forecast", filters={"weeks": 1})
		finally:
			frappe.set_user("Administrator")
		self.assertIn("result", out)


class TestPlotCountQuality(FrappeTestCase):
	def test_quality_report_rows(self):
		import frappe
		from frappe.desk.query_report import run

		from upande_agriculture.forecast import api
		from upande_agriculture.upande_agriculture.report.plot_count_quality.plot_count_quality import execute
		make_variety()
		gh = make_greenhouse("FC PCQ", (("FC-ROSE", 1000, "1-9"),))
		plot = make_plot(gh, "FC-ROSE")
		api.submit_plot_count(client_uuid="fc-pcq-1", sample_plot=plot, counts=[{"stage_name": "Pea", "count": 4}],
			duration_seconds=8)
		email = "fc-pcq@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "FC PCQ", "send_welcome_email": 0,
				"roles": [{"role": "Agriculture User"}]}).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			api.submit_plot_count(client_uuid="fc-pcq-2", sample_plot=plot, counts=[{"stage_name": "Pea", "count": 6}],
				duration_seconds=40)
			out = run("Plot Count Quality", filters={"greenhouse": gh})
		finally:
			frappe.set_user("Administrator")
		self.assertIn("result", out)
		cols, rows = execute({"greenhouse": gh, "from_date": nowdate(), "to_date": nowdate()})
		self.assertEqual(len(rows), 1)
		r = rows[0]
		self.assertEqual((r["due"], r["counted"], r["flagged"], r["seconds"]), (1, 1, 1, 24))
		self.assertIn("fast: 8 s", r["flags"])
		self.assertIn("4 vs 6 (2 apart)", r["recounts"])
		self.assertEqual(r["counters"], "Administrator, FC PCQ")
