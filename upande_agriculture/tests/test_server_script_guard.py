import unittest

import frappe

from upande_agriculture.server_script_guard import repair, repair_server_scripts


class TestServerScriptGuard(unittest.TestCase):
	def test_repair_text(self):
		self.assertEqual(repair("data = frappe.request.get_json() or frappe.local.form_dict"), "data = frappe.form_dict")
		self.assertEqual(repair("data = frappe.request.get_json() or frappe.form_dict"), "data = frappe.form_dict")
		self.assertEqual(repair("try:\r\n    data = frappe.request.get_json()\r\n"), "try:\r\n    data = frappe.form_dict\r\n")
		# a guarded call (silent=True) never crashes, so it is left alone
		self.assertEqual(repair("frappe.request.get_json(silent=True)"), "frappe.request.get_json(silent=True)")

	def test_repairs_saved_scripts(self):
		name = "TEST guard get_json"
		if frappe.db.exists("Server Script", name):
			frappe.delete_doc("Server Script", name, force=True)
		frappe.get_doc({"doctype": "Server Script", "name": name, "script_type": "API", "api_method": "test_guard_get_json",
			"script": "data = frappe.request.get_json()\nfrappe.response['message'] = data.get('x')"}).insert()
		self.assertIn(name, repair_server_scripts())
		self.assertEqual(frappe.db.get_value("Server Script", name, "script"),
			"data = frappe.form_dict\nfrappe.response['message'] = data.get('x')")
		self.assertNotIn(name, repair_server_scripts())  # idempotent
