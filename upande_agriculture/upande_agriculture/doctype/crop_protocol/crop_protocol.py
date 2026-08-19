from frappe.model.document import Document


class CropProtocol(Document):
	def validate(self):
		# Growers think in years; the model works in weeks. Keep one source.
		if self.productive_life_years:
			self.productive_life_weeks = int(round(float(self.productive_life_years) * 52))
		elif self.productive_life_weeks:
			self.productive_life_years = round(self.productive_life_weeks / 52.0, 1)

		if self.crop_type == "Summer Flower":
			self.set_flush_schedule()

	def set_flush_schedule(self):
		"""Number the flushes in order and fill in any blank offset.

		A summer flower doesn't compound off a cut stem like a rose — it
		flushes on a schedule, and that same schedule repeats every year for
		the rest of the plant's life (see projection_calc._flush_weekly_stems).
		"""
		rows = sorted(self.flush_schedule or [], key=lambda r: r.flush_number or 0)
		interval = self.flush_interval_weeks or 0
		for idx, row in enumerate(rows, start=1):
			row.idx = idx
			row.flush_number = idx
			if not row.weeks_after_first_flush and interval:
				row.weeks_after_first_flush = interval * (idx - 1)
		self.flush_schedule = rows

		self.total_flushes = len(rows)
		self.total_stems_per_plant_life = sum(float(r.stems_per_plant or 0) for r in rows)
