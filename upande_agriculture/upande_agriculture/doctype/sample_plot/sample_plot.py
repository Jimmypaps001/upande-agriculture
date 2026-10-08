import frappe
from frappe.model.document import Document


class SamplePlot(Document):
	def validate(self):
		dup = frappe.db.get_value("Sample Plot", {"greenhouse": self.greenhouse, "section": self.section,
			"plot_no": self.plot_no, "name": ["!=", self.name]})
		if dup:
			frappe.throw(f"{self.greenhouse} section {self.section} already has plot {self.plot_no} ({dup}).")
		self.variety = frappe.db.get_value("Warehouse Section",
			{"parent": self.greenhouse, "parenttype": "Warehouse", "section": self.section}, "custom_variety") or self.variety
		gh = frappe.db.get_value("Warehouse", self.greenhouse, "warehouse_name") or self.greenhouse
		self.title = f"{gh} · {self.section} · plot {self.plot_no}"
