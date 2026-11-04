import frappe
from frappe import _
from frappe.model.document import Document

from upande_agriculture.greenhouse_audit_templates import AUDIT_TYPES


class GreenhouseAuditTemplate(Document):
	def validate(self):
		sections = AUDIT_TYPES[self.audit_type][1]
		wrong = [f"#{r.idx} {r.section}" for r in self.items if r.section not in sections]
		if wrong:
			frappe.throw(_("Not a {0} section: {1}").format(self.audit_type, ", ".join(wrong)))
