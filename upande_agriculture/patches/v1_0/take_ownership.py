import frappe


ABSORBED = [
    "Crop Protocol",
    "Crop Protocol Flush",
    "Crop Protocol Growth Stage",
    "Greenhouse",
    "Crop Cycle",
    # Crop Cycle family added in Task 5
    "Flower Trial",
    # Flower Trial family added in Task 6
    "Production Projection",
    "Projection Week",
    # Production Projection family added in Task 7
    "Production Plan Form",
    "Production Plan Task",
    "Production Plan Variety",
    # Production Plan Form family added in Task 9
]


def execute():
    """Flip custom=0 + module=Upande Agriculture for absorbed DocTypes."""
    for dt in ABSORBED:
        if not frappe.db.exists("DocType", dt):
            continue
        frappe.db.set_value("DocType", dt, {
            "custom": 0,
            "module": "Upande Agriculture",
        }, update_modified=False)
    frappe.db.commit()
