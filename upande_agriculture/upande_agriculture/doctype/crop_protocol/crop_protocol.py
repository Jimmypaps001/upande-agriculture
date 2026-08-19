import frappe
from frappe.model.document import Document


class CropProtocol(Document):
	def validate(self):
		# Growers think in years; the model works in weeks. Keep one source.
		if self.productive_life_years:
			self.productive_life_weeks = int(round(float(self.productive_life_years) * 52))
		elif self.productive_life_weeks:
			self.productive_life_years = round(self.productive_life_weeks / 52.0, 1)
