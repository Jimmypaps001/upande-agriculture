import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.tests import make_warehouse
from upande_agriculture.todo_helpers import upsert_todo

REF_TYPE = "Greenhouse"


class TestTodoHelpers(FrappeTestCase):
    def setUp(self):
        self.user = "Administrator"
        self.ref = self._make_greenhouse()
        frappe.db.delete("ToDo", {"reference_type": REF_TYPE,
                                   "reference_name": self.ref})
        frappe.db.commit()

    def _make_greenhouse(self, warehouse_name="TEST GH TODO"):
        """ToDo validates its dynamic link, so the reference must really exist."""
        wh = make_warehouse(warehouse_name)
        if not frappe.db.exists(REF_TYPE, wh):
            frappe.get_doc({"doctype": REF_TYPE, "greenhouse": wh}).insert(
                ignore_permissions=True
            )
        return wh

    def test_creates_when_missing(self):
        name = upsert_todo(
            reference_type=REF_TYPE,
            reference_name=self.ref,
            tag="bending",
            description="Bending reminder",
            assigned_to=self.user,
            due_date=datetime.date(2026, 7, 1),
        )
        self.assertIsNotNone(name)
        self.assertTrue(frappe.db.exists("ToDo", name))

    def test_idempotent(self):
        kwargs = dict(
            reference_type=REF_TYPE,
            reference_name=self.ref,
            tag="bending",
            description="Bending reminder",
            assigned_to=self.user,
            due_date=datetime.date(2026, 7, 1),
        )
        first = upsert_todo(**kwargs)
        second = upsert_todo(**kwargs)
        self.assertEqual(first, second)
        rows = frappe.db.get_all("ToDo", filters={
            "reference_type": REF_TYPE,
            "reference_name": self.ref,
        })
        self.assertEqual(len(rows), 1)

    def test_updates_due_date_on_resave(self):
        kwargs = dict(
            reference_type=REF_TYPE,
            reference_name=self.ref,
            tag="bending",
            description="Bending reminder",
            assigned_to=self.user,
        )
        upsert_todo(due_date=datetime.date(2026, 7, 1), **kwargs)
        name = upsert_todo(due_date=datetime.date(2026, 8, 1), **kwargs)
        td = frappe.get_doc("ToDo", name)
        self.assertEqual(str(td.date), "2026-08-01")

    def test_returns_none_without_assignee(self):
        name = upsert_todo(
            reference_type=REF_TYPE,
            reference_name=self.ref,
            tag="bending",
            description="Bending reminder",
            assigned_to=None,
            due_date=datetime.date(2026, 7, 1),
        )
        self.assertIsNone(name)

    def tearDown(self):
        frappe.db.delete("ToDo", {"reference_type": REF_TYPE,
                                   "reference_name": self.ref})
        frappe.db.commit()
