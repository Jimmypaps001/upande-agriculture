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
