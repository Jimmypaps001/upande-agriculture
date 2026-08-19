"""Shared test fixtures.

Warehouse mandatory fields vary by site (upande_core adds `custom_farm`, and
Global Defaults may carry no default company), so warehouse creation lives
here rather than being copy-pasted into every test module.

`frappe` is imported inside each helper, not at module scope, so the pure-maths
tests in test_projection_calc can run without a bench.
"""

def default_company() -> str:
    import frappe

    return (
        frappe.db.get_single_value("Global Defaults", "default_company")
        or frappe.db.get_value("Company", {}, "name")
    )


def default_uom() -> str:
    """Not every site ships the ERPNext default 'Nos'."""
    import frappe

    for candidate in ("Nos", "Piece", "Unit"):
        if frappe.db.exists("UOM", candidate):
            return candidate
    return frappe.db.get_value("UOM", {}, "name")


def ensure_supervisor_field() -> None:
    """Milestone ToDos need Warehouse.custom_supervisor (shipped by upande_core).

    Create it when absent so the milestone tests exercise the real path on a
    bare site instead of silently asserting nothing.
    """
    import frappe

    if frappe.get_meta("Warehouse").has_field("custom_supervisor"):
        return
    frappe.get_doc({
        "doctype": "Custom Field",
        "dt": "Warehouse",
        "fieldname": "custom_supervisor",
        "label": "Supervisor",
        "fieldtype": "Link",
        "options": "User",
        "insert_after": "warehouse_name",
    }).insert(ignore_permissions=True)
    frappe.clear_cache(doctype="Warehouse")


def farm_with_beds() -> str | None:
    """A Farm whose Farm Type includes 'Has Beds'.

    upande_core refuses to create a Bed under a farm that isn't configured for
    them, so bed tests need the right farm rather than whichever comes first.
    """
    import frappe

    row = frappe.db.get_value(
        "Farm Type Item", {"parenttype": "Farm", "farm_type": "Has Beds"}, "parent"
    )
    return row or frappe.db.get_value("Farm", {}, "name")


def default_employee(user: str = "Administrator") -> str:
    """An Employee linked to `user`, creating one if none exists yet.

    Production Plan Task assigns work to an Employee, not a User directly --
    tests that exercise that path need one to hand.
    """
    import frappe

    existing = frappe.db.get_value("Employee", {"user_id": user}, "name")
    if existing:
        return existing
    return frappe.get_doc({
        "doctype": "Employee",
        "first_name": user.split("@")[0] if "@" in user else user,
        "gender": frappe.db.get_value("Gender", {}, "name"),
        "date_of_birth": "1990-01-01",
        "date_of_joining": frappe.utils.nowdate(),
        "company": default_company(),
        "status": "Active",
        "user_id": user,
    }).insert(ignore_permissions=True).name


def make_warehouse(name: str, supervisor: str | None = None) -> str:
    """Return a non-group Warehouse typed as a Greenhouse, creating it if needed."""
    import frappe

    if supervisor:
        ensure_supervisor_field()
    existing = frappe.db.get_value("Warehouse", {"warehouse_name": name}, "name")
    if existing:
        return existing

    doc = frappe.get_doc({
        "doctype": "Warehouse",
        "warehouse_name": name,
        "company": default_company(),
        "is_group": 0,
    })
    # Only set fields this site actually has — upande_core adds custom_farm
    # and custom_supervisor; a bare ERPNext site has neither.
    if doc.meta.has_field("custom_farm"):
        doc.custom_farm = farm_with_beds()
    # upande_core checks the warehouse role when validating bed structure.
    if doc.meta.has_field("warehouse_type") and frappe.db.exists(
        "Warehouse Type", "Greenhouse"
    ):
        doc.warehouse_type = "Greenhouse"
    if supervisor and doc.meta.has_field("custom_supervisor"):
        doc.custom_supervisor = supervisor
    return doc.insert(ignore_permissions=True).name
