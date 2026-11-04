"""Custom fields this app mirrors onto the Bed master.

Bed belongs to another app (upande_scp or upande_core depending on the site),
so the Greenhouse ledger's status, variety and plant count land there as
Custom Fields. The v1_0 patches add them on sites that migrate; a fresh
install marks those patches done without running them, so after_install
calls ensure_bed_fields() too. Idempotent: only missing fields are created.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

BED_STATUS_OPTIONS = "Empty\nPlanted\nProducing\nHarvesting\nTransplanted\nPartially Uprooted\nUprooted"

MIRRORED = "Mirrored from the Greenhouse bed ledger on every save there."

BED_FIELDS = [
	{
		"fieldname": "variety",
		"fieldtype": "Link",
		"label": "Variety",
		"options": "Item",
		"insert_after": "bed",
		"read_only": 1,
		"description": MIRRORED,
	},
	{
		"fieldname": "status",
		"fieldtype": "Select",
		"label": "Status",
		"options": BED_STATUS_OPTIONS,
		"insert_after": "variety",
		"in_list_view": 1,
		"in_standard_filter": 1,
		"read_only": 1,
		"description": MIRRORED,
	},
	{
		"fieldname": "plant_count",
		"fieldtype": "Int",
		"label": "Plant Count",
		"insert_after": "status",
		"in_list_view": 1,
		"read_only": 1,
		"non_negative": 1,
		"description": MIRRORED,
	},
]


def ensure_bed_fields() -> None:
	if not frappe.db.exists("DocType", "Bed"):
		return
	meta = frappe.get_meta("Bed")
	missing = [f for f in BED_FIELDS if not meta.has_field(f["fieldname"])]
	if not missing:
		return
	create_custom_fields({"Bed": missing})
	frappe.clear_cache(doctype="Bed")
