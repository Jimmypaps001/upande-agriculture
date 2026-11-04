"""Every DocType this app ships a definition for must be custom=0 so Frappe
loads its controller. A site restored from an older backup can carry them
back as custom=1 -- Greenhouse then saves as a bare Document and the
Crop Cycle -> Greenhouse sync dies on a missing method. Idempotent."""

import os

import frappe


def execute():
	doctype_dir = frappe.get_app_path("upande_agriculture", "upande_agriculture", "doctype")
	for folder in os.listdir(doctype_dir):
		path = os.path.join(doctype_dir, folder, folder + ".json")
		if not os.path.exists(path):
			continue
		# nosemgrep: frappe-security-file-traversal -- path is built from this app's own doctype folder
		name = frappe.parse_json(open(path).read()).get("name")
		if name and frappe.db.get_value("DocType", name, "custom"):
			frappe.db.set_value(
				"DocType", name, {"custom": 0, "module": "Upande Agriculture"}, update_modified=False
			)
			frappe.clear_cache(doctype=name)
