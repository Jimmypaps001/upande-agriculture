"""Templates seeded before Greenhouse Audit Template had an Audit Type have
none, so neither audit form offers them. Infer it from their sections."""

import frappe

from upande_agriculture.greenhouse_audit_templates import AUDIT_TYPES, seed


def execute():
    for name in frappe.get_all("Greenhouse Audit Template",
            filters={"audit_type": ("in", ("", None))}, pluck="name"):
        sections = set(frappe.get_all("Greenhouse Audit Template Item",
            filters={"parent": name, "parenttype": "Greenhouse Audit Template"}, pluck="section"))
        for audit_type, (_, type_sections) in AUDIT_TYPES.items():
            if sections and sections <= set(type_sections):
                frappe.db.set_value("Greenhouse Audit Template", name, "audit_type", audit_type,
                    update_modified=False)
                break
    seed()
