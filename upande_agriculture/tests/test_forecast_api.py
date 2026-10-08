import base64
import io

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate
from PIL import Image

from upande_agriculture.forecast import api
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestForecastApi(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse(f"FC API {self._testMethodName[-8:]}", (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 10, "FC-ROSE", 500)))

	def _plots(self):
		return [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == self.gh]

	def test_plan_creates_two_plots_per_section_one_for_a_single_bed(self):
		plots = self._plots()
		self.assertEqual(sorted((p["section"], p["bed"]) for p in plots), [("S1", 3), ("S1", 6), ("S2", 10)])
		self.assertTrue(all(p["due"] for p in plots))
		self.assertEqual([s["stage_name"] for s in plots[0]["stages"]], ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"])
		self.assertEqual(len(self._plots()), 3)  # second call creates nothing new

	def test_submit_is_idempotent_and_marks_plot_done(self):
		plot = self._plots()[0]["plot"]
		im = io.BytesIO(); Image.new("RGB", (32, 32), (200, 0, 0)).save(im, "JPEG")
		args = dict(client_uuid="fc-api-1", sample_plot=plot, counts='[{"stage_name": "Rice", "count": 7}]',
			plants_counted=10, captured_at=f"{nowdate()} 08:00:00", photos=[base64.b64encode(im.getvalue()).decode()])
		a, b = api.submit_plot_count(**args), api.submit_plot_count(**args)
		self.assertEqual(a, b)
		doc = frappe.get_doc("Bed Sample", a["name"])
		self.assertEqual((doc.section, doc.total_count, doc.stages[0].days_to_harvest), ("S1", 7, 28))
		self.assertTrue(doc.photo_1)
		p = [x for x in self._plots() if x["plot"] == plot][0]
		self.assertFalse(p["due"])
		self.assertEqual(p["last_counted"], nowdate())

	def test_bay_forecast_shape(self):
		f = api.get_bay_forecast(self.gh, "S1")
		self.assertEqual(len(f["daily"]), 7)
		self.assertTrue(f["flags"]["no_count"])
		for k in ("today", "tomorrow", "next7", "low7", "high7"):
			self.assertIsInstance(f[k], int)

	def test_unknown_section_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_bay_forecast(self.gh, "NOPE")

	def test_garbage_photo_is_skipped_but_count_saves(self):
		plot = self._plots()[0]["plot"]
		r = api.submit_plot_count(client_uuid="fc-api-bad", sample_plot=plot, counts=[{"stage_name": "Rice", "count": 3}],
			photos=[base64.b64encode(b"not an image").decode()])
		doc = frappe.get_doc("Bed Sample", r["name"])
		self.assertEqual(doc.total_count, 3)
		self.assertFalse(doc.photo_1)
		self.assertIn("photo 1 skipped", doc.notes)

	def test_negative_count_refused(self):
		plot = self._plots()[0]["plot"]
		with self.assertRaises(frappe.ValidationError):
			api.submit_plot_count(client_uuid="fc-api-neg", sample_plot=plot, counts=[{"stage_name": "Rice", "count": -1}])

	def test_days_clamped_to_60(self):
		self.assertEqual(len(api.get_bay_forecast(self.gh, "S1", days=500)["daily"]), 60)
