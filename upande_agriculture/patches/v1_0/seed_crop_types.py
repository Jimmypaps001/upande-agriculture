"""Seed Crop Type records matching the Select options Crop Protocol.crop_type
used to offer, so existing data stays a valid Link once the field switches
over. Idempotent.
"""

import frappe

CROP_TYPES = ("Rose", "Spray Rose", "Summer Flower", "Other")


def execute():
    for name in CROP_TYPES:
        if not frappe.db.exists("Crop Type", name):
            frappe.get_doc({
                "doctype": "Crop Type", "crop_type_name": name,
            }).insert(ignore_permissions=True)
