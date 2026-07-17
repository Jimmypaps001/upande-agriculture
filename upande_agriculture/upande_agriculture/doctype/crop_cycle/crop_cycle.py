# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate


class CropCycle(Document):
	def validate(self):
		self.validate_bed_ranges()
		self.roll_up_varieties()
		self.sync_individual_beds()
		self.apply_uprooting_logs()
		self.apply_replanting_logs()
		self.roll_up_geometry()

	# ------------------------------------------------------------------ helpers
	def _plants_per_sqm(self, row):
		"""Crop Protocol density wins over the greenhouse default."""
		if row.crop_protocol:
			pps = frappe.db.get_value("Crop Protocol", row.crop_protocol, "plants_per_sqm")
			if pps:
				return flt(pps)
		return flt(self.plants_per_sqm)

	def _bed_area(self, row):
		return flt(row.bed_length) * flt(row.bed_width)

	@staticmethod
	def _beds_in(row):
		return range(int(row.from_bed), int(row.to_bed) + 1)

	# --------------------------------------------------------------- validation
	def validate_bed_ranges(self):
		"""Ranges must be well-formed; a bed should belong to only one range.

		Bed numbers are unique within a greenhouse, so an overlap means dirty
		data. We flag it with a warning (not a hard block) so legacy records
		can still be saved and cleaned up over time.
		"""
		seen = {}
		overlaps = []
		for row in self.bed_range:
			if not row.from_bed or not row.to_bed:
				frappe.throw(_("Greenhouse crop cycle row {0}: From Bed and To Bed are required.").format(row.idx))
			if row.from_bed > row.to_bed:
				frappe.throw(
					_("Greenhouse crop cycle row {0}: From Bed ({1}) cannot be greater than To Bed ({2}).").format(
						row.idx, row.from_bed, row.to_bed
					)
				)
			if not row.variety:
				frappe.throw(_("Greenhouse crop cycle row {0}: Variety is required.").format(row.idx))
			for bed in self._beds_in(row):
				if bed in seen:
					overlaps.append((bed, seen[bed], row.idx))
				else:
					seen[bed] = row.idx
			# keep the per-row area in sync
			row.total_beds_area = (row.to_bed - row.from_bed + 1) * self._bed_area(row)

		if overlaps:
			lines = [
				_("Bed {0} appears in rows {1} and {2}").format(bed, a, b)
				for bed, a, b in overlaps[:10]
			]
			frappe.msgprint(
				_("Overlapping beds detected (a bed should belong to only one range):<br>{0}").format(
					"<br>".join(lines)
				),
				title=_("Check bed ranges"),
				indicator="orange",
			)

	# ------------------------------------------------------------------ rollups
	def roll_up_varieties(self):
		"""Read-only Varieties Grown, aggregated from the bed ranges."""
		agg = {}
		for row in self.bed_range:
			n = row.to_bed - row.from_bed + 1
			area = self._bed_area(row)
			bucket = agg.setdefault(row.variety, {"beds": 0, "area_m2": 0.0, "plants": 0})
			bucket["beds"] += n
			bucket["area_m2"] += area * n
			bucket["plants"] += round(area * self._plants_per_sqm(row)) * n

		self.set("varieties_grown", [])
		for variety, data in agg.items():
			self.append(
				"varieties_grown",
				{"variety": variety, "beds": data["beds"], "area_m2": data["area_m2"], "plants": data["plants"]},
			)

	def roll_up_geometry(self):
		self.number_of_beds = sum((r.to_bed - r.from_bed + 1) for r in self.bed_range)
		self.varieties = len({r.variety for r in self.bed_range if r.variety})
		self.area_planted = sum(flt(r.total_beds_area) for r in self.bed_range)
		self.number_of_plants = sum(v.plants for v in self.varieties_grown)

	# ------------------------------------------------------- individual beds
	def sync_individual_beds(self):
		"""Rebuild Individual Beds from the ranges, preserving manual per-bed data."""
		prev = {b.bed_number: b.as_dict() for b in self.individual_beds}
		self.set("individual_beds", [])
		empty_states = ("Empty", "Uprooted")
		for row in self.bed_range:
			area = self._bed_area(row)
			plants = round(area * self._plants_per_sqm(row))
			for bed in self._beds_in(row):
				old = prev.get(bed, {})
				# A bed cleared/uprooted earlier stays empty until it is replanted,
				# rather than getting the range's variety refilled on every save.
				is_empty = old.get("status") in empty_states
				self.append(
					"individual_beds",
					{
						"bed_number": bed,
						"variety": None if is_empty else row.variety,
						"length_m": row.bed_length,
						"width_m": row.bed_width,
						"area_m2": area,
						"plant_count": 0 if is_empty else plants,
						"status": old.get("status") or "Planted",
						"plant_date": old.get("plant_date") or row.planting_date,
						"transplant_date": old.get("transplant_date"),
						"source_type": old.get("source_type"),
						"propagation_batch": old.get("propagation_batch"),
						"purchase_order": old.get("purchase_order"),
						"breeder": old.get("breeder"),
						"cost_per_plant": old.get("cost_per_plant"),
						"performance_notes": old.get("performance_notes"),
					},
				)

	def _beds_by_number(self):
		return {b.bed_number: b for b in self.individual_beds}

	def apply_uprooting_logs(self):
		"""Uprooting empties the affected beds (cleared)."""
		beds = self._beds_by_number()
		for log in self.uprooting_logs:
			if not log.from_bed or not log.to_bed:
				continue
			for n in range(int(log.from_bed), int(log.to_bed) + 1):
				bed = beds.get(n)
				if not bed:
					continue
				bed.status = "Uprooted"
				bed.variety = None
				bed.plant_count = 0
				bed.performance_notes = _("Uprooted {0}: {1}").format(log.uproot_date or "", log.reason or "")

	def apply_replanting_logs(self):
		"""Replanting plants the affected beds with the new variety."""
		beds = self._beds_by_number()
		last_date = None
		for log in self.replanting_logs:
			if not log.from_bed or not log.to_bed:
				continue
			for n in range(int(log.from_bed), int(log.to_bed) + 1):
				bed = beds.get(n)
				if not bed:
					continue
				bed.status = "Planted"
				if log.new_variety:
					bed.variety = log.new_variety
				bed.plant_date = log.replant_date
				bed.propagation_batch = log.propagation_batch
				if bed.area_m2:
					bed.plant_count = round(flt(bed.area_m2) * flt(self.plants_per_sqm))
			if log.replant_date and (not last_date or getdate(log.replant_date) > getdate(last_date)):
				last_date = log.replant_date
		self.last_replanting_date = last_date
