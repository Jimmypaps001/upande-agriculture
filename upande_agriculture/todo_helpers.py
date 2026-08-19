"""
Idempotent ToDo upsert keyed on (reference_type, reference_name, tag).

The tag is stored in the ToDo's description prefix `[tag] ` so the unique
key works without schema additions. (Frappe ToDo has no native "tag" field.)
"""

from __future__ import annotations

import datetime

import frappe


def upsert_todo(
    reference_type: str,
    reference_name: str,
    tag: str,
    description: str,
    assigned_to: str | None,
    due_date: datetime.date | None,
) -> str | None:
    if not assigned_to or not due_date:
        return None

    full_description = f"[{tag}] {description}"

    existing = frappe.db.get_value(
        "ToDo",
        filters={
            "reference_type": reference_type,
            "reference_name": reference_name,
            "description": ("like", f"[{tag}]%"),
        },
        fieldname="name",
    )
    if existing:
        td = frappe.get_doc("ToDo", existing)
        td.description = full_description
        td.date = due_date
        td.allocated_to = assigned_to
        td.save(ignore_permissions=True)
        return td.name

    td = frappe.get_doc({
        "doctype": "ToDo",
        "reference_type": reference_type,
        "reference_name": reference_name,
        "description": full_description,
        "date": due_date,
        "allocated_to": assigned_to,
        "status": "Open",
    })
    td.insert(ignore_permissions=True)
    return td.name
