import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.todo_helpers import upsert_todo


class TestTodoHelpers(FrappeTestCase):
    def setUp(self):
        self.user = "Administrator"
        # Clean test ToDos
        frappe.db.delete("ToDo", {"reference_type": "Crop Cycle",
                                   "reference_name": "TEST-CYCLE-001"})
        frappe.db.commit()

        # Create test Greenhouse if it doesn't exist
        if not frappe.db.exists("Greenhouse", "TEST-GH"):
            gh = frappe.get_doc({
                "doctype": "Greenhouse",
                "name": "TEST-GH",
                "greenhouse_code": "TEST-GH",
            })
            gh.db_insert()

        # Create test Crop Cycle if it doesn't exist
        if not frappe.db.exists("Crop Cycle", "TEST-CYCLE-001"):
            cc = frappe.get_doc({
                "doctype": "Crop Cycle",
                "name": "TEST-CYCLE-001",
                "crop": "Maize",
                "status": "Active",
                "greenhouse": "TEST-GH",
            })
            cc.db_insert()

        frappe.db.commit()

    def test_creates_when_missing(self):
        name = upsert_todo(
            reference_type="Crop Cycle",
            reference_name="TEST-CYCLE-001",
            tag="pinch",
            description="Pinch reminder",
            assigned_to=self.user,
            due_date=datetime.date(2026, 7, 1),
        )
        self.assertIsNotNone(name)
        self.assertTrue(frappe.db.exists("ToDo", name))

    def test_idempotent(self):
        kwargs = dict(
            reference_type="Crop Cycle",
            reference_name="TEST-CYCLE-001",
            tag="pinch",
            description="Pinch reminder",
            assigned_to=self.user,
            due_date=datetime.date(2026, 7, 1),
        )
        first = upsert_todo(**kwargs)
        second = upsert_todo(**kwargs)
        self.assertEqual(first, second)
        # Only one ToDo exists for this (ref, tag)
        rows = frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle",
            "reference_name": "TEST-CYCLE-001",
        })
        self.assertEqual(len(rows), 1)

    def test_updates_due_date_on_resave(self):
        kwargs = dict(
            reference_type="Crop Cycle",
            reference_name="TEST-CYCLE-001",
            tag="pinch",
            description="Pinch reminder",
            assigned_to=self.user,
        )
        upsert_todo(due_date=datetime.date(2026, 7, 1), **kwargs)
        name = upsert_todo(due_date=datetime.date(2026, 8, 1), **kwargs)
        td = frappe.get_doc("ToDo", name)
        self.assertEqual(str(td.date), "2026-08-01")

    def test_returns_none_without_assignee(self):
        name = upsert_todo(
            reference_type="Crop Cycle",
            reference_name="TEST-CYCLE-001",
            tag="pinch",
            description="Pinch reminder",
            assigned_to=None,
            due_date=datetime.date(2026, 7, 1),
        )
        self.assertIsNone(name)

    def tearDown(self):
        frappe.db.delete("ToDo", {"reference_type": "Crop Cycle",
                                   "reference_name": "TEST-CYCLE-001"})
        frappe.db.delete("Crop Cycle", "TEST-CYCLE-001")
        frappe.db.delete("Greenhouse", "TEST-GH")
        frappe.db.commit()
