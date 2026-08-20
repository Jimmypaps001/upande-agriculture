"""Production Plan Task was once autoincrement-named, leaving name as BIGINT.

The doctype now uses hash names (like every other child table here), so any
insert fails with 'Incorrect integer value'. Widen the column back to the
standard varchar; existing integer names survive as their string forms.
"""

import frappe


def execute():
    if not frappe.db.has_table("Production Plan Task"):
        return
    coltype = frappe.db.sql("SHOW COLUMNS FROM `tabProduction Plan Task` LIKE 'name'")
    if not coltype or "int" not in (coltype[0][1] or "").lower():
        return
    frappe.db.sql_ddl(
        "ALTER TABLE `tabProduction Plan Task` MODIFY `name` varchar(140) NOT NULL"
    )
    frappe.reload_doc("upande_agriculture", "doctype", "production_plan_task")
