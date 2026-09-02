"""Add "Partially Uprooted" to the Bed master's mirrored status options.

A bed that's only had some of its stems removed shouldn't have to read as
fully gone (or sit silently stuck at its last real status). Idempotent, and
safe on a site with no Bed status Custom Field at all yet (add_bed_status_field
hasn't run, or Bed doesn't exist on this site).
"""

import frappe

OPTIONS = "Empty\nPlanted\nProducing\nHarvesting\nTransplanted\nPartially Uprooted\nUprooted"


def execute():
    if not frappe.db.exists("Custom Field", "Bed-status"):
        return
    current = frappe.db.get_value("Custom Field", "Bed-status", "options") or ""
    if "Partially Uprooted" in current:
        return
    frappe.db.set_value("Custom Field", "Bed-status", "options", OPTIONS)
    frappe.clear_cache(doctype="Bed")
