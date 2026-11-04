import json

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture import setup_api
from upande_agriculture.forecast import data
from upande_agriculture.patches.v2_2 import cycles_from_greenhouse_varieties
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety

V = "SU-ROSE"


def _vpg(gh, *rows):
	wh = frappe.get_doc("Warehouse", gh)
	for variety, plants, size in rows:
		wh.append("custom_varieties_grown", {"variety": variety, "no_of_plants": plants, "size_m2": size})
	wh.save(ignore_permissions=True)


def _vpg_rows(gh):
	return [(r.variety, r.no_of_plants, r.size_m2) for r in frappe.get_doc("Warehouse", gh).custom_varieties_grown]


def _cycle(gh, variety=V):
	name = frappe.db.get_value("Crop Cycle", {"greenhouse": gh, "variety": variety, "status": "Active"})
	return name and frappe.get_doc("Crop Cycle", name)


class TestSetupApi(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		make_variety(V)

	def tearDown(self):
		frappe.set_user("Administrator")

	def test_get_setup_shape(self):
		gh = make_greenhouse("SU GH SHAPE", ())
		setup_api.save_crop(gh, V, 500)
		out = setup_api.get_setup()
		self.assertTrue(out["can_edit"])
		self.assertIn(V, out["varieties"])
		self.assertEqual([s["stage_name"] for s in out["default_stages"]][0], "Eye pop-up")
		self.assertEqual(out["default_stages"][0]["survival"], 80)
		house = next(h for g in out["greenhouses"] for h in g["houses"] if h["name"] == gh)
		self.assertEqual(set(house), {"name", "warehouse_name", "beds", "sections", "crops"})
		crop = house["crops"][0]
		self.assertEqual((crop["variety"], crop["plants"], crop["protocol"], crop["planting_date"]),
			(V, 500, V, frappe.utils.nowdate()))
		self.assertTrue(any(p["name"] == V and len(p["stages"]) == 5 for p in out["protocols"]))

	def test_save_sections_replaces_and_refuses_overlap(self):
		gh = make_greenhouse("SU GH SECT", ())
		emp = frappe.get_all("Employee", filters={"status": "Active"}, limit=1, pluck="name")
		setup_api.save_sections(gh, json.dumps([{"from_bed": 1, "to_bed": 14}, {"from_bed": 15, "to_bed": 30}]))
		out = setup_api.save_sections(gh, json.dumps([
			{"from_bed": 1, "to_bed": 10, "employee": emp[0] if emp else None},
			{"section": "Back", "from_bed": 11, "to_bed": 20, "variety": V}]))
		self.assertEqual([(s["section"], s["from_bed"], s["to_bed"]) for s in out], [("Beds 1-10", 1, 10), ("Back", 11, 20)])
		self.assertEqual(sorted(frappe.get_all("Section", filters={"greenhouse": gh}, pluck="section")), ["Back", "Beds 1-10"])
		if emp:
			self.assertTrue(out[0]["employee_name"])
		with self.assertRaisesRegex(frappe.ValidationError, "Beds 12-20 overlap Beds 1-14"):
			setup_api.save_sections(gh, json.dumps([{"from_bed": 12, "to_bed": 20}, {"from_bed": 1, "to_bed": 14}]))
		with self.assertRaises(frappe.ValidationError):
			setup_api.save_sections(gh, json.dumps([{"from_bed": 9, "to_bed": 3}]))

	def test_section_with_several_varieties(self):
		gh = make_greenhouse("SU GH MULTI", ())
		other = make_variety("SU-ROSE-B")
		rows = json.dumps([{"from_bed": 1, "to_bed": 10, "varieties": [V, other, V]}])
		if not setup_api._has_more():
			with self.assertRaisesRegex(frappe.ValidationError, "latest Upande Harvest"):
				setup_api.save_sections(gh, rows)
			return
		out = setup_api.save_sections(gh, rows)
		self.assertEqual((out[0]["variety"], out[0]["varieties"]), (V, [V, other]))
		self.assertEqual(frappe.db.get_value("Warehouse Section", {"parent": gh}, "custom_more_varieties"), other)

	def test_save_crop_creates_cycle_and_collapses_duplicate_rows(self):
		gh = make_greenhouse("SU GH CROP", ())
		_vpg(gh, (V, "100", "10"), (V, "200", "20.5"))
		crop = setup_api.save_crop(gh, V, 1200, planting_date="2025-03-01")
		cc = _cycle(gh)
		self.assertEqual((cc.qty_planted, cc.plants_standing, str(cc.planting_date), cc.crop_protocol),
			(1200, 1200, "2025-03-01", V))
		self.assertEqual(_vpg_rows(gh), [(V, "1200", "30.5")])
		setup_api.save_crop(gh, V, 900)  # an update keeps the cycle and its date
		self.assertEqual((_cycle(gh).name, _cycle(gh).plants_standing, str(_cycle(gh).planting_date)),
			(cc.name, 900, "2025-03-01"))

	def test_new_crop_planted_today_unless_given(self):
		gh = make_greenhouse("SU GH PH", ())
		setup_api.save_crop(gh, V, 300)
		self.assertEqual(str(_cycle(gh).planting_date), frappe.utils.nowdate())
		setup_api.save_crop(gh, V, 300, planting_date="2024-06-01")
		self.assertEqual(str(_cycle(gh).planting_date), "2024-06-01")
		setup_api.save_crop(gh, V, 350)  # an edit without a date keeps it
		self.assertEqual(str(_cycle(gh).planting_date), "2024-06-01")

	def test_save_stages_default_vs_greenhouse(self):
		gh1, gh2 = make_greenhouse("SU GH ST1", ()), make_greenhouse("SU GH ST2", ())
		setup_api.save_crop(gh1, V, 100)
		_vpg(gh2, (V, "250", ""))  # only a Varieties per GH row: save_stages makes the cycle
		self.assertEqual(setup_api.save_stages(V, json.dumps([{"stage_name": "Pea", "days_to_harvest": 20},
			{"stage_name": "Bud", "days_to_harvest": 9.5, "spread_days": 2, "survival": 90}])), V)
		wn = frappe.db.get_value("Warehouse", gh2, "warehouse_name")
		name = setup_api.save_stages(V, json.dumps([{"stage_name": "Pea", "days_to_harvest": 30}]), greenhouse=gh2)
		self.assertEqual(name, f"{V} – {wn}")
		self.assertEqual((_cycle(gh2).crop_protocol, _cycle(gh2).plants_standing), (name, 250))
		self.assertEqual(data.stages(V, gh2)["Pea"][0], 30)
		self.assertEqual(data.stages(V, gh1)["Pea"][0], 20)
		self.assertEqual(data.stages(V)["Bud"], (9.5, 2.0, 0.9))
		self.assertEqual(next(r for r in data.sections(greenhouse=gh2)).protocol, name)
		self.assertEqual(setup_api.reset_stages(V, gh2), V)
		self.assertEqual(data.stages(V, gh2)["Pea"][0], 20)
		self.assertTrue(frappe.db.exists("Crop Protocol", name))
		for bad in ([{"stage_name": "Pea", "days_to_harvest": 0}],
				[{"stage_name": "Pea", "days_to_harvest": 5}, {"stage_name": "pea", "days_to_harvest": 6}],
				[{"stage_name": "Pea", "days_to_harvest": 5, "survival": 120}]):
			with self.assertRaises(frappe.ValidationError):
				setup_api.save_stages(V, json.dumps(bad))

	def test_remove_crop_ends_cycle(self):
		gh = make_greenhouse("SU GH RM", ())
		setup_api.save_crop(gh, V, 100, planting_date="2025-01-01")
		name = _cycle(gh).name
		setup_api.remove_crop(gh, V)
		self.assertEqual(frappe.db.get_value("Crop Cycle", name, "status"), "Ended")
		self.assertTrue(frappe.db.get_value("Crop Cycle", name, "cycle_end_date"))
		self.assertEqual(_vpg_rows(gh), [])
		with self.assertRaises(frappe.ValidationError):
			setup_api.remove_crop(gh, V)

	def test_permission_refused_without_roles(self):
		email = "su-noroles@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc({"doctype": "User", "email": email, "first_name": "No Roles",
				"send_welcome_email": 0}).insert(ignore_permissions=True)
		gh = make_greenhouse("SU GH PERM", ())
		frappe.set_user(email)
		with self.assertRaises(frappe.PermissionError):
			setup_api.get_setup()
		with self.assertRaises(frappe.PermissionError):
			setup_api.save_crop(gh, V, 10)

	def test_patch_creates_cycles(self):
		gh = make_greenhouse("SU GH PATCH", ())
		other = make_variety("SU-ROSE-B")
		plain = make_variety("SU-PLAIN", has_variants=0)
		_vpg(gh, (V, "400", "1"), (V, "100", "1"), (other, "", "1"), (other, "0", ""), (plain, "900", ""))
		cycles_from_greenhouse_varieties.execute()
		cycles_from_greenhouse_varieties.execute()  # idempotent
		self.assertEqual(frappe.db.count("Crop Cycle", {"greenhouse": gh, "status": "Active"}), 1)
		cc = _cycle(gh)
		self.assertEqual((cc.plants_standing, cc.crop_protocol), (500, V))
		self.assertEqual(str(cc.planting_date), setup_api.MIGRATED_DATE)
		self.assertIn(setup_api.MIGRATED_NOTE, cc.notes)
		self.assertIsNone(_cycle(gh, other))  # no plants
		self.assertIsNone(_cycle(gh, plain))  # not a rose
