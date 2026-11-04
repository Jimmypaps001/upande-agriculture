"""Shared controller for Greenhouse 5S Audit and Greenhouse DMS Audit: one
checklist table per section, filled from the chosen Greenhouse Audit
Template and scored per section."""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

from upande_agriculture.greenhouse_audit_templates import AUDIT_TYPES

CHECKLIST_FIELDS = ("look_at", "look_for", "observation", "max_score")


class GreenhouseAudit(Document):
    AUDIT_TYPE = None

    @property
    def sections(self):
        return AUDIT_TYPES[self.AUDIT_TYPE][1]

    def before_validate(self):
        if not self.audit_template:
            self.audit_template = default_template(self.AUDIT_TYPE)
        if self.audit_template and frappe.db.get_value(
            "Greenhouse Audit Template", self.audit_template, "audit_type") != self.AUDIT_TYPE:
            frappe.throw(_("{0} is not a {1} template.").format(self.audit_template, self.AUDIT_TYPE))
        # API / mobile inserts send only the header; the checklist always
        # comes from the template.
        if self.audit_template and not any(self.get(t) for t in self.sections.values()):
            self.load_checklist()

    def validate(self):
        self.compute_scores()

    def before_submit(self):
        unscored = [
            f"{section} #{row.idx}"
            for section, table in self.sections.items()
            for row in self.get(table)
            if row.score in (None, "")
        ]
        if unscored:
            frappe.throw(_("Score every row before submitting. Unscored: {0}").format(", ".join(unscored)))

    def load_checklist(self):
        for table in self.sections.values():
            self.set(table, [])
        template = frappe.get_doc("Greenhouse Audit Template", self.audit_template)
        for row in template.items:
            self.append(self.sections[row.section], {f: row.get(f) for f in CHECKLIST_FIELDS})

    def compute_scores(self):
        self.set("section_scores", [])
        self.total_score = self.max_score = 0
        for section, table in self.sections.items():
            rows = self.get(table)
            if not rows:
                continue
            score = sum(cint(r.score) for r in rows)
            max_score = sum(cint(r.max_score) for r in rows)
            self.append("section_scores", {
                "section": section, "score": score, "max_score": max_score,
                "percentage": flt(score * 100 / max_score, 2) if max_score else 0,
            })
            self.total_score += score
            self.max_score += max_score
        self.percentage = flt(self.total_score * 100 / self.max_score, 2) if self.max_score else 0


def default_template(audit_type):
    names = frappe.get_all("Greenhouse Audit Template",
        filters={"audit_type": audit_type, "disabled": 0}, pluck="name",
        order_by="creation asc", limit=1)
    return names[0] if names else None


@frappe.whitelist()
def get_checklist(audit_template):
    """{table fieldname: [rows]} for the form to load when the template changes."""
    template = frappe.get_doc("Greenhouse Audit Template", audit_template)
    template.check_permission("read")
    sections = AUDIT_TYPES[template.audit_type][1]
    out = {table: [] for table in sections.values()}
    for row in template.items:
        out[sections[row.section]].append({f: row.get(f) for f in CHECKLIST_FIELDS})
    return out


@frappe.whitelist()
def get_default_template(audit_type):
    return default_template(audit_type)
