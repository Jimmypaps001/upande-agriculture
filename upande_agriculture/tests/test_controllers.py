import datetime

import frappe
from frappe.tests.utils import FrappeTestCase


class TestCropCycleController(FrappeTestCase):
    def _make_warehouse(self, name="TEST GH 1", supervisor="Administrator"):
        existing = frappe.db.get_value("Warehouse", {"warehouse_name": name}, "name")
        if existing:
            return existing
        doc = frappe.get_doc({
            "doctype": "Warehouse",
            "warehouse_name": name,
            "company": frappe.db.get_single_value("Global Defaults", "default_company"),
            "is_group": 0,
            "custom_supervisor": supervisor,
        }).insert(ignore_permissions=True)
        return doc.name

    def _make_item(self, name="TEST-VARIETY-A"):
        if not frappe.db.exists("Item", name):
            frappe.get_doc({
                "doctype": "Item",
                "item_code": name,
                "item_name": name,
                "item_group": "All Item Groups",
                "stock_uom": "Nos",
            }).insert(ignore_permissions=True, ignore_mandatory=True)
        return name

    def _make_protocol(self):
        if frappe.db.exists("Crop Protocol", "TEST-PROTO-1"):
            return "TEST-PROTO-1"
        return frappe.get_doc({
            "doctype": "Crop Protocol",
            "protocol_name": "TEST-PROTO-1",
            "variety_item": self._make_item(),
            "weeks_to_pinch": 4,
            "weeks_pinch_to_first_harvest": 8,
            "total_weeks_in_ground": 52,
            "total_stems_per_plant_life": 120.0,
            "plants_per_sqm": 7,
        }).insert(ignore_permissions=True).name

    def test_cycle_creates_projection_and_todos(self):
        gh = self._make_warehouse()
        proto = self._make_protocol()
        variety = self._make_item()

        cycle = frappe.get_doc({
            "doctype": "Crop Cycle",
            "greenhouse": gh,
            "custom_crop_protocol": proto,
            "variety": variety,
            "planting_date": datetime.date(2026, 1, 5),
            "cycle_status": "Active",
            "custom_total_expected_stems": 0,
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        # A Projection now exists.
        proj_name = frappe.db.get_value("Production Projection",
                                         {"crop_cycle": cycle.name}, "name")
        self.assertIsNotNone(proj_name, "Projection should be auto-created")
        proj = frappe.get_doc("Production Projection", proj_name)
        self.assertEqual(proj.source, "Hybrid")
        self.assertEqual(len(proj.weeks), 52)

        # 3 ToDos exist.
        todos = frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle",
            "reference_name": cycle.name,
        })
        self.assertEqual(len(todos), 3)

    def _make_cycle(self, gh, proto, variety, planting_date, status="Active", suffix=""):
        # Use a distinct warehouse per test by creating a unique one when suffix given
        actual_gh = gh
        if suffix:
            actual_gh = self._make_warehouse(name=f"TEST GH {suffix}", supervisor="Administrator")
        return frappe.get_doc({
            "doctype": "Crop Cycle",
            "greenhouse": actual_gh,
            "custom_crop_protocol": proto,
            "variety": variety,
            "planting_date": planting_date,
            "cycle_status": status,
            "custom_total_expected_stems": 0,
        }).insert(ignore_permissions=True, ignore_mandatory=True)

    def test_on_trash_deletes_cycle_todos(self):
        proto = self._make_protocol()
        variety = self._make_item()

        cycle = self._make_cycle(None, proto, variety, datetime.date(2026, 1, 5), suffix="TRASH")

        # Confirm 3 ToDos were created by on_update
        todos_before = frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle",
            "reference_name": cycle.name,
        })
        self.assertEqual(len(todos_before), 3)

        # Remove linked Projection so Frappe's referential check doesn't block deletion
        proj_name = frappe.db.get_value(
            "Production Projection", {"crop_cycle": cycle.name}, "name"
        )
        if proj_name:
            frappe.delete_doc("Production Projection", proj_name, ignore_permissions=True, force=True)

        # Delete the cycle — on_trash should remove its ToDos
        cycle.delete(ignore_permissions=True)

        todos_after = frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle",
            "reference_name": cycle.name,
        })
        self.assertEqual(len(todos_after), 0)

    def test_autoseed_pinch_milestone_into_plan_form(self):
        """A Plan Form saved for the week a cycle's pinch falls in should
        auto-receive a 'Pinch <variety>' task row."""
        proto = self._make_protocol()
        variety = self._make_item()
        # Use a unique greenhouse so this cycle doesn't collide with other tests
        gh = self._make_warehouse(name="TEST GH SEED", supervisor="Administrator")

        # Protocol: weeks_to_pinch=4. Planting 2026-06-01 (Mon) ->
        # pinch_date = 2026-06-01 + 4 weeks = 2026-06-29.
        # ISO week of 2026-06-29: week 27 of 2026.
        planting = datetime.date(2026, 6, 1)
        cycle = self._make_cycle(gh, proto, variety, planting)

        plan = frappe.get_doc({
            "doctype": "Production Plan Form",
            "company": frappe.db.get_single_value("Global Defaults", "default_company"),
            "greenhouse": gh,
            "plan_year": 2026, "plan_week": 27,
            "plan_period": "2026-W27",
            "tasks": [],
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        task_names = [t.task_name for t in plan.tasks]
        pinch_task = [n for n in task_names if n and n.startswith("Pinch ")]
        self.assertTrue(pinch_task, f"Expected a 'Pinch ...' task; got {task_names}")

    def test_plan_form_submit_creates_todos(self):
        gh = self._make_warehouse()
        plan = frappe.get_doc({
            "doctype": "Production Plan Form",
            "company": frappe.db.get_single_value("Global Defaults", "default_company"),
            "greenhouse": gh,
            "plan_year": 2026, "plan_week": 27,
            "plan_period": "2026-W27",
            "tasks": [
                {"task_name": "Pinch top buds", "due_day": "Tuesday",
                 "assigned_to": "Administrator", "status": "Pending"},
                {"task_name": "Spray fungicide", "due_day": "Friday",
                 "assigned_to": "Administrator", "status": "Pending"},
            ],
        }).insert(ignore_permissions=True, ignore_mandatory=True)

        todos = frappe.db.get_all("ToDo", filters={
            "reference_type": "Production Plan Form",
            "reference_name": plan.name,
        })
        self.assertGreaterEqual(len(todos), 2)
