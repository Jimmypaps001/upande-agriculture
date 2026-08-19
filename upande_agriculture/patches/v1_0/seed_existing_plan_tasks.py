import frappe


VALID_STATUSES = ("Open", "In Progress", "Done", "Skipped")


def execute():
    """Set status='Open' on Production Plan Task rows that pre-date this app.

    Covers both NULL/empty rows and any legacy values not in the new select options.
    """
    if not frappe.db.has_table("tabProduction Plan Task"):
        return
    placeholders = ", ".join(["%s"] * len(VALID_STATUSES))
    frappe.db.sql(
        f"""
        UPDATE `tabProduction Plan Task`
        SET status = 'Open'
        WHERE status IS NULL OR status = '' OR status NOT IN ({placeholders})
        """,
        VALID_STATUSES,
    )
    frappe.db.commit()
