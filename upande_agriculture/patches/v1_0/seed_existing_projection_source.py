import frappe


def execute():
    """
    Existing 123 Production Projection records were populated by hand
    before this app shipped. Mark them source=Manual so the new calc
    engine never overwrites them.

    The schema migration sets the column default to 'Hybrid', so newly-added
    rows get 'Hybrid'. All pre-existing rows (created before Task 7) must be
    set to 'Manual'. We identify them as any row whose source is NOT already
    'Calculated from Protocol' (idempotent — safe to re-run).
    """
    if not frappe.db.has_table("tabProduction Projection"):
        return
    # Set source=Manual for all rows that are not yet explicitly set to
    # 'Calculated from Protocol'. Covers NULL, '', and the migration-applied
    # default of 'Hybrid' equally.
    frappe.db.sql("""
        UPDATE `tabProduction Projection`
        SET source = 'Manual'
        WHERE source IS NULL OR source = '' OR source = 'Hybrid'
    """)
    frappe.db.commit()
