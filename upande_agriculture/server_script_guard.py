"""After every migrate: repair Server Scripts that read the request body with
frappe.request.get_json().

On Frappe Cloud v16 the Server Script sandbox hands scripts `frappe.request`
as a small dict, so `frappe.request.get_json` is None and every such script
fails with "'NoneType' object is not callable" (receiving, grading, packing,
discards...). `frappe.form_dict` is the parsed JSON body, so it is a drop-in
replacement. upande_harvest's fixture carries the fix, but a deploy that ships
an older upande_harvest re-imports the old scripts; this runs after fixtures
are synced, so whatever version arrives, the scripts end up working.
"""
import frappe

# Order matters: the longer forms first, so "x or frappe.form_dict" collapses cleanly.
REPLACEMENTS = (
	("frappe.request.get_json() or frappe.local.form_dict", "frappe.form_dict"),
	("frappe.request.get_json() or frappe.form_dict", "frappe.form_dict"),
	("frappe.request.get_json()", "frappe.form_dict"),
)
MARKER = "frappe.request.get_json()"


def repair(script):
	for old, new in REPLACEMENTS:
		script = script.replace(old, new)
	return script


def repair_server_scripts():
	fixed = []
	for name in frappe.get_all("Server Script", filters={"script": ["like", f"%{MARKER}%"]}, pluck="name"):
		doc = frappe.get_doc("Server Script", name)
		new = repair(doc.script or "")
		if new != doc.script:
			doc.script = new
			doc.flags.ignore_permissions = True
			doc.save()
			fixed.append(name)
	if fixed:
		frappe.db.commit()
		print(f"upande_agriculture: repaired frappe.request.get_json() in {len(fixed)} Server Scripts: {', '.join(fixed)}")
	return fixed
