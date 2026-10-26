import frappe
from frappe.tests import IntegrationTestCase

from upande_agriculture import bilashaka_deployment as bd


class TestBilashakaDeployment(IntegrationTestCase):
	def test_apply_is_idempotent_and_wins_over_shared_workflow(self):
		if not frappe.db.exists("DocType", bd.DOCTYPE):
			self.skipTest("upande_dev_tools not installed")
		bd.apply()
		bd.apply()
		self.assertEqual(frappe.db.get_value("Workflow", {"document_type": bd.DOCTYPE, "is_active": 1}), bd.WORKFLOW)
		self.assertEqual(frappe.db.count("Workflow", {"document_type": bd.DOCTYPE, "is_active": 1}), 1)
		wf = frappe.get_doc("Workflow", bd.WORKFLOW)
		self.assertEqual([(t.state, t.action, t.next_state, t.allowed) for t in wf.transitions],
			[(s, a, n, bd.ROLE) for s, a, n in bd.TRANSITIONS])
		self.assertTrue(frappe.has_permission(bd.DOCTYPE, "write", user="Administrator"))
		perms = frappe.get_all("Custom DocPerm", filters={"parent": bd.DOCTYPE, "role": bd.ROLE}, fields=["read", "write"])
		self.assertEqual([(p.read, p.write) for p in perms], [(1, 1)])
		self.assertTrue(frappe.db.exists("Custom DocPerm", {"parent": bd.DOCTYPE, "role": "Dev Team"}))  # standard rows kept
		for spec in bd.NOTIFICATIONS:
			self.assertEqual(frappe.db.get_value("Notification", spec["name"], "channel"), "System Notification")
