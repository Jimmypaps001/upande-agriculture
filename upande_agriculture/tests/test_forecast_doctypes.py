import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from upande_agriculture.tests.forecast_fixtures import make_count, make_greenhouse, make_plot, make_variety


class TestForecastDoctypes(FrappeTestCase):
	def test_plot_takes_section_variety_and_title(self):
		make_variety()
		gh = make_greenhouse()
		plot = frappe.get_doc("Sample Plot", make_plot(gh, "S1"))
		self.assertEqual(plot.variety, "FC-ROSE")
		self.assertIn("S1 · plot 1", plot.title)

	def test_duplicate_plot_number_refused(self):
		make_variety()
		gh = make_greenhouse()
		make_plot(gh, "S1", 1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Sample Plot", "greenhouse": gh, "section": "S1", "plot_no": 1,
				"plants": 10}).insert(ignore_permissions=True)

	def test_count_on_plot_fills_section_and_totals(self):
		make_variety()
		gh = make_greenhouse()
		s = frappe.get_doc("Bed Sample", make_count(make_plot(gh, "S1"), nowdate(), {"Rice": 12, "Pea": 3}))
		self.assertEqual((s.greenhouse, s.section, s.variety, s.total_count), (gh, "S1", "FC-ROSE", 15))

	def test_section_has_plants_field(self):
		self.assertTrue(frappe.get_meta("Warehouse Section").has_field("custom_plants"))
