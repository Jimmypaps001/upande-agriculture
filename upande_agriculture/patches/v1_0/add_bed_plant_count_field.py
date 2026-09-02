"""Give the Bed master a live plant count, mirrored from the Greenhouse ledger.

Same reasoning as add_bed_status_field: Bed belongs to another app, so the
field is a Custom Field rather than a fork of their doctype. Idempotent:
skipped when the field already exists, safe on sites without Bed at all.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Bed"):
        return

    if not frappe.get_meta("Bed").has_field("plant_count"):
        create_custom_fields({
            "Bed": [{
                "fieldname": "plant_count",
                "fieldtype": "Int",
                "label": "Plant Count",
                "insert_after": "status",
                "in_list_view": 1,
                "read_only": 1,
                "non_negative": 1,
                "description": "Mirrored from the Greenhouse bed ledger on every save there.",
            }]
        })
        frappe.clear_cache(doctype="Bed")

    # Backfill from every existing ledger so old beds don't sit blank.
    from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
        sync_bed_master,
    )

    for name in frappe.get_all("Greenhouse", pluck="name"):
        sync_bed_master(frappe.get_doc("Greenhouse", name))
