import frappe
from frappe.model.document import Document


class BedSample(Document):
	def validate(self):
		if not (self.sample_plot or self.crop_cycle):
			frappe.throw("Pick the Sample Plot (or Crop Cycle) that was counted.")
		if (self.plants_counted or 0) <= 0:
			frappe.throw("Plants Counted must be at least 1.")
		# validate() runs before link fetching on insert, so copy these here.
		if self.sample_plot:
			plot = frappe.db.get_value("Sample Plot", self.sample_plot,
				["greenhouse", "section", "variety", "bed"], as_dict=True)
			self.greenhouse, self.section, self.variety = plot.greenhouse, plot.section, plot.variety
			self.bed_number = self.bed_number or plot.bed
		else:
			self.greenhouse, self.variety, self.crop_protocol = frappe.db.get_value(
				"Crop Cycle", self.crop_cycle, ["greenhouse", "variety", "crop_protocol"])
		self.crop_protocol = self.crop_protocol or frappe.db.get_value(
			"Crop Protocol", {"variety_item": self.variety})
		self.total_count = sum(r.count or 0 for r in self.stages)
		where = f"{self.section}" if self.section else f"bed {self.bed_number}"
		self.title = f"{self.greenhouse} {where} · {self.variety}"
		if self.bed_number:
			self.bed = self.bed or frappe.db.get_value("Bed", {"greenhouse": self.greenhouse, "bed": self.bed_number})
