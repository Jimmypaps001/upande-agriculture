import frappe
from frappe.model.document import Document


class BedSample(Document):
	def validate(self):
		if (self.plants_counted or 0) <= 0:
			frappe.throw("Plants Counted must be at least 1.")
		# validate() runs before link fetching on insert, so copy these here.
		self.greenhouse, self.variety, self.crop_protocol = frappe.db.get_value(
			"Crop Cycle", self.crop_cycle, ["greenhouse", "variety", "crop_protocol"]
		)
		self.total_count = sum(r.count or 0 for r in self.stages)
		self.title = f"{self.greenhouse} bed {self.bed_number} · {self.variety}"
		self.bed = self.bed or frappe.db.get_value("Bed", {"greenhouse": self.greenhouse, "bed": self.bed_number})
