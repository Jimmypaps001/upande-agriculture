# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

"""Targeted DocType sync for this app.

`bench migrate` cannot run on karenroses -- it dies on unrelated Module Def data
("Module  not found") before reaching any schema work. This imports just this
app's DocType JSONs, which is all that is needed after a schema edit here.

	bench --site <site> execute upande_agriculture.setup.sync_doctypes.sync
"""

import os

import frappe
from frappe.modules.import_file import import_file_by_path

TARGETS = [
	"crop_protocol",
	"crop_protocol_growth_stage",
	"crop_protocol_flush",
	"crop_protocol_length_distribution",
]


def doctype_dir():
	app = frappe.get_app_path("upande_agriculture")
	return os.path.join(app, "upande_agriculture", "doctype")


def sync(targets=None):
	names = targets.split(",") if isinstance(targets, str) else (targets or TARGETS)
	base = doctype_dir()
	for slug in names:
		slug = slug.strip()
		path = os.path.join(base, slug, "%s.json" % slug)
		if not os.path.exists(path):
			print("missing: %s" % path)
			continue
		import_file_by_path(path, force=True, reset_permissions=False)
		print("synced: %s" % slug)
	frappe.db.commit()
	print("done")
