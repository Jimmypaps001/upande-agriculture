"""Give the Bed master a live status, mirrored from the Greenhouse ledger.

Bed belongs to another app (upande_scp or upande_core depending on the site),
so the field is a Custom Field rather than a fork of their doctype. Idempotent:
skipped when the field already exists, safe on sites without Bed at all.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
    if not frappe.db.exists("DocType", "Bed"):
        return

    if not frappe.get_meta("Bed").has_field("status"):
        create_custom_fields({
            "Bed": [{
                "fieldname": "status",
                "fieldtype": "Select",
                "label": "Status",
                "options": "Empty\nPlanted\nProducing\nHarvesting\nTransplanted\nUprooted",
                "insert_after": "variety",
                "in_list_view": 1,
                "in_standard_filter": 1,
                "read_only": 1,
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
