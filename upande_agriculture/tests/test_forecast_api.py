import base64
import io
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate
from PIL import Image

from upande_agriculture.forecast import api
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_plot, make_variety


class TestForecastApi(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse(f"FC API {self._testMethodName[-8:]}", (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 10, "FC-ROSE", 500)))

	def _plots(self):
		return [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == self.gh]

	def test_plan_creates_eight_spots_of_five_plants_per_section(self):
		plots = self._plots()
		s1 = [p for p in plots if p["section"] == "S1"]
		self.assertEqual(len(s1), 8)
		self.assertTrue(all(2 <= p["bed"] <= 8 and p["plants"] == 5 and not p["set_up"] for p in s1))
		self.assertEqual([p["bed"] for p in s1], sorted(p["bed"] for p in s1))
		self.assertEqual({p["bed"] for p in plots if p["section"] == "S2"}, {10})  # a single bed takes them all
		self.assertTrue(all(p["due"] for p in plots))
		self.assertEqual([s["stage_name"] for s in plots[0]["stages"]], ["Eye pop-up", "Maroon shoot", "Pea", "Bud", "Colour break"])
		self.assertEqual(len(self._plots()), 16)  # second call creates nothing new

	def test_spot_layout_is_systematic_and_deterministic(self):
		a = api.spot_layout("GH|A", 1, 41)
		self.assertEqual(a, api.spot_layout("GH|A", 1, 41))
		beds = [b for b, _p in a]
		self.assertTrue(all(2 <= b <= 40 for b in beds))
		self.assertTrue(all(4 <= y - x <= 5 for x, y in zip(beds, beds[1:])))  # step 39/8
		self.assertEqual([p for _b, p in a], ["¼", "½", "¾", "¼", "½", "¾", "¼", "½"])
		self.assertNotEqual([b for b, _p in api.spot_layout("GH|B", 1, 200)], [b for b, _p in api.spot_layout("GH|A", 1, 200)])
		self.assertEqual({b for b, _p in api.spot_layout("GH|A", None, None)}, {None})

	def test_section_without_beds_gets_spots_awaiting_set_up(self):
		gh = make_greenhouse(f"FC API NB {self._testMethodName[-6:]}", (("S9", None, None, "FC-ROSE", 300),))
		plots = [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == gh]
		self.assertEqual(len(plots), 8)
		self.assertTrue(all(p["bed"] in (None, 0) and not p["set_up"] for p in plots))

	def test_tops_up_old_plots_and_leaves_them_alone(self):
		gh = make_greenhouse(f"FC API TOP {self._testMethodName[-6:]}", (("S1", 1, 21, "FC-ROSE", 1000),))
		old = make_plot(gh, "S1", 1, plants=10)
		plots = [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == gh]
		self.assertEqual(len(plots), 8)
		self.assertEqual(sorted(p["plot_no"] for p in plots), list(range(1, 9)))
		self.assertEqual(frappe.db.get_value("Sample Plot", old, "plants"), 10)

	def test_submit_is_idempotent_and_marks_plot_done(self):
		plot = self._plots()[0]["plot"]
		im = io.BytesIO(); Image.new("RGB", (32, 32), (200, 0, 0)).save(im, "JPEG")
		args = dict(client_uuid="fc-api-1", sample_plot=plot, counts='[{"stage_name": "Pea", "count": 7}]',
			plants_counted=10, captured_at=f"{nowdate()} 08:00:00", photos=[base64.b64encode(im.getvalue()).decode()])
		a, b = api.submit_plot_count(**args), api.submit_plot_count(**args)
		self.assertEqual(a, b)
		doc = frappe.get_doc("Bed Sample", a["name"])
		self.assertEqual((doc.section, doc.total_count, doc.stages[0].days_to_harvest), ("S1", 7, 18))  # pea: 14-21 days out, middle 17.5
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

	def test_blank_client_uuid_refused(self):
		plot = self._plots()[0]["plot"]
		for blank in ("", "  ", None):
			with self.assertRaises(frappe.ValidationError):
				api.submit_plot_count(client_uuid=blank, sample_plot=plot, counts='[{"stage_name": "Rice", "count": 1}]')

	def test_quality_flags(self):
		order = ["Pea", "Bud", "Colour break"]
		self.assertEqual(api.quality_flags({"Pea": 40}, {"Pea": 4}, order), ["jump: Pea 4→40"])
		self.assertEqual(api.quality_flags({"Pea": 6}, {"Pea": 4}, order), [])  # within max(3, 50%)
		self.assertEqual(api.quality_flags({"Pea": 0, "Bud": 25}, {"Pea": 0, "Bud": 20}, order),
			["flow: Bud 25 > 22 possible from the last count"])  # more buds than pea + bud could supply
		self.assertEqual(api.quality_flags({"Pea": 1}, None, order, duration_seconds=9, missing_plants=2),
			["fast: 9 s for the spot", "missing: 2 plants"])

	def test_submit_v2_stores_plants_flags_and_shows_last(self):
		plot = self._plots()[0]["plot"]
		yday = add_days(nowdate(), -1)
		api.submit_plot_count(client_uuid=f"fc-v2a-{self.gh}", sample_plot=plot, counts=[{"stage_name": "Pea", "count": 4}],
			captured_at=f"{yday} 08:00:00", duration_seconds=60)
		pc = [{"plant": i, "counts": {"Pea": 10 if i == 1 else 7}} for i in range(1, 5)]
		r = api.submit_plot_count(client_uuid=f"fc-v2b-{self.gh}", sample_plot=plot,
			counts='[{"stage_name": "Pea", "count": 38}]', plant_counts=json.dumps(pc), blind_shoots=2,
			missing_plants=1, duration_seconds=8)
		self.assertEqual(r["flags"], ["jump: Pea 4→38", "flow: Pea 38 > 6 possible from the last count",
			"fast: 8 s for the spot", "missing: 1 plant"])
		doc = frappe.get_doc("Bed Sample", r["name"])
		self.assertEqual((doc.plants_counted, doc.blind_shoots, doc.missing_plants, doc.duration_seconds), (4, 2, 1, 8))
		self.assertEqual(json.loads(doc.plant_counts), pc)
		self.assertEqual((doc.counted_by, doc.is_recount), ("Administrator", 0))
		self.assertIn("jump: Pea 4→38", doc.quality_flags)
		self.assertEqual(api.submit_plot_count(client_uuid=f"fc-v2b-{self.gh}", sample_plot=plot, counts=[])["flags"], r["flags"])
		p = [x for x in self._plots() if x["plot"] == plot][0]
		self.assertEqual(p["last"], {"date": nowdate(), "counts": {"Pea": 38}, "blind_shoots": 2, "missing_plants": 1})
		self.assertEqual((p["position"], p["route_no"], p["plants"]), ("¼", 1, 5))

	def test_plan_route_numbers_follow_the_beds(self):
		s1 = sorted((p for p in self._plots() if p["section"] == "S1"), key=lambda p: p["route_no"])
		self.assertEqual([p["route_no"] for p in s1], list(range(1, 9)))
		self.assertEqual([p["bed"] for p in s1], sorted(p["bed"] for p in s1))

	def test_second_person_same_day_is_a_recount(self):
		plot = self._plots()[0]["plot"]
		api.submit_plot_count(client_uuid=f"fc-rc1-{self.gh}", sample_plot=plot, counts=[{"stage_name": "Pea", "count": 4}])
		email = "fc-counter@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "FC Counter", "send_welcome_email": 0,
				"roles": [{"role": "Agriculture User"}]}).insert(ignore_permissions=True)
		frappe.set_user(email)
		try:
			r = api.submit_plot_count(client_uuid=f"fc-rc2-{self.gh}", sample_plot=plot, counts=[{"stage_name": "Pea", "count": 5}])
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Bed Sample", r["name"], ["is_recount", "counted_by"]), (1, email))

	def test_bad_plant_counts_refused(self):
		plot = self._plots()[0]["plot"]
		for bad in ('{"plant": 1}', '[{"plant": 1, "counts": {"Pea": -2}}]'):
			with self.assertRaises(frappe.ValidationError):
				api.submit_plot_count(client_uuid=f"fc-bad-{bad[:4]}-{self.gh}", sample_plot=plot,
					counts=[{"stage_name": "Pea", "count": 1}], plant_counts=bad)

	def test_confirm_plot_bed_is_idempotent_and_checks_the_range(self):
		plot = self._plots()[0]["plot"]
		for _ in range(2):
			self.assertEqual(api.confirm_plot_bed(plot, "7"), {"name": plot, "bed": 7, "set_up": 1})
		doc = frappe.get_doc("Sample Plot", plot)
		self.assertEqual((doc.bed, doc.set_up, str(doc.started_on)), (7, 1, nowdate()))
		with self.assertRaises(frappe.ValidationError):
			api.confirm_plot_bed(plot, 40)

	def test_plot_tags_pdf(self):
		self._plots()
		html = api.plot_tags_html(self.gh, "S1")
		self.assertEqual(html.count('class="tag"'), 8)
		self.assertIn("Spot 1 of 8 · plants 1–5", html)
		self.assertIn("Skip the first 2 m of the bed.", html)
		api.plot_tags_pdf(self.gh)
		self.assertTrue(frappe.local.response.filecontent.startswith(b"%PDF"))
		self.assertEqual(frappe.local.response.type, "pdf")
