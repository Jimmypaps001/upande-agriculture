# Copyright (c) 2026, Upande and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.tests import default_uom


class TestCropProtocol(FrappeTestCase):
    def _item(self, name="TEST-GRADEMIX-VARIETY"):
        if not frappe.db.exists("Item", name):
            frappe.get_doc({
                "doctype": "Item", "item_code": name, "item_name": name,
                "item_group": "All Item Groups", "stock_uom": default_uom(),
            }).insert(ignore_permissions=True, ignore_mandatory=True)
        return name

    def _protocol(self, name, grade_mix):
        if frappe.db.exists("Crop Protocol", name):
            frappe.delete_doc("Crop Protocol", name, force=True, ignore_permissions=True)
        return frappe.get_doc({
            "doctype": "Crop Protocol", "protocol_name": name,
            "variety_item": self._item(), "grade_mix": grade_mix,
        })

    def test_grade_mix_over_100_percent_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            self._protocol("TEST-GRADEMIX-OVER", [
                {"length_cm": 60, "pct": 70}, {"length_cm": 50, "pct": 40},
            ]).insert(ignore_permissions=True)

    def test_grade_mix_at_100_percent_is_allowed(self):
        doc = self._protocol("TEST-GRADEMIX-EXACT", [
            {"length_cm": 60, "pct": 63.8}, {"length_cm": 50, "pct": 36.2},
        ]).insert(ignore_permissions=True)
        self.assertEqual(doc.name, "TEST-GRADEMIX-EXACT")
