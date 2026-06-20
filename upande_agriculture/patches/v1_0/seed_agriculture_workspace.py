"""Patch: ensure the Agriculture workspace exists and is tagged with module=Upande Agriculture.

If the workspace already exists (created manually), update its module field so the
fixtures filter [["module", "=", "Upande Agriculture"]] picks it up on export.
If it does not exist, create a minimal stub.
"""

import frappe


def execute():
    ws_name = "Agriculture"

    if frappe.db.exists("Workspace", ws_name):
        # Just tag the module so fixtures export works; don't overwrite user content.
        frappe.db.set_value(
            "Workspace", ws_name, "module", "Upande Agriculture", update_modified=False
        )
        frappe.db.commit()
        return

    # Create a minimal workspace if it doesn't exist at all.
    shortcuts = [
        {"label": "Crop Protocols", "link_to": "Crop Protocol",         "type": "DocType"},
        {"label": "Crop Cycles",    "link_to": "Crop Cycle",            "type": "DocType"},
        {"label": "Flower Trials",  "link_to": "Flower Trial",          "type": "DocType"},
        {"label": "Budget",         "link_to": "Production Projection", "type": "DocType"},
        {"label": "Forecast",       "link_to": "Production Forecast",   "type": "DocType"},
        {"label": "Plans",          "link_to": "Production Plan Form",  "type": "DocType"},
    ]

    doc = frappe.get_doc({
        "doctype": "Workspace",
        "name": ws_name,
        "label": "Agriculture",
        "module": "Upande Agriculture",
        "title": "Agriculture",
        "type": "Workspace",
        "public": 1,
        "is_hidden": 0,
        "icon": "leaf",
        "content": "[]",
        "shortcuts": shortcuts,
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
