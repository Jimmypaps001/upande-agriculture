import frappe
from frappe.model.rename_doc import rename_doc


def execute():
    """Forecasting Form → Production Forecast. 0 records, safe."""
    if frappe.db.exists("DocType", "Forecasting Form") and not frappe.db.exists("DocType", "Production Forecast"):
        rename_doc("DocType", "Forecasting Form", "Production Forecast",
                   force=True, merge=False, ignore_permissions=True)
    if frappe.db.exists("DocType", "Forecasting Item"):
        # Old child not used in new schema; drop after the rename so
        # the table can be re-created with the new structure.
        frappe.delete_doc("DocType", "Forecasting Item",
                          force=True, ignore_permissions=True)
    frappe.db.commit()
