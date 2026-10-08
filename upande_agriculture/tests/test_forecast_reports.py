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
		self.gh = make_greenhouse("FC REP", (("S1", 1, 9, "FC-ROSE", 1000),))
		make_count(make_plot(self.gh, "S1"), nowdate(), {"Showing colour": 10})

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
			make_harvest(self.gh, "S1", "FC-ROSE", monday, 50)
		cols, rows = weekly_report({"weeks": 3})
		ours = [r for r in rows if r["variety"] == "FC-ROSE"]
		self.assertEqual(len(ours), 3)
		self.assertEqual(ours[0]["actual"], 50 if monday < today else 0)
		self.assertAlmostEqual(ours[0]["total"], ours[0]["actual"] + ours[0]["forecast"], delta=1)

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
