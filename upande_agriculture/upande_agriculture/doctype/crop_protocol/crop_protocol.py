# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


def stem_length_cm(stem_length):
	"""Numeric cm from a Stem Length name like '62cm' (or a bare '62')."""
	m = re.search(r"\d+", stem_length or "")
	return int(m.group()) if m else 0


class CropProtocol(Document):
	def validate(self):
		self.roll_up_flushes()
		self.roll_up_length_distribution()

	def roll_up_flushes(self):
		"""Count the flushes and, once a lifetime yield is known, spread it across
		them evenly. Rows a grower has typed a figure into are left alone."""
		self.total_flushes = len(self.flush_schedule)

		lifetime_stems = flt(self.total_stems_per_plant_life)
		if not (lifetime_stems and self.total_flushes):
			return
		if any(flt(row.stems_per_plant) for row in self.flush_schedule):
			return
		per_flush = lifetime_stems / self.total_flushes
		for row in self.flush_schedule:
			row.stems_per_plant = per_flush

	def roll_up_length_distribution(self):
		"""Total the grade shares, split the plant-life yield across them, and
		derive the weighted mean stem length.

		The share is warned about rather than blocked: a protocol is often part-
		entered while the grower is still gathering grade data, and refusing to
		save would just push people to keep it in a spreadsheet.
		"""
		total = 0.0
		weighted = 0.0
		lifetime_stems = flt(self.total_stems_per_plant_life)

		for row in self.length_distribution:
			pct = flt(row.percentage)
			total += pct
			weighted += pct * stem_length_cm(row.stem_length)
			row.stems_per_plant_life = lifetime_stems * pct / 100.0

		self.length_distribution_total = total
		self.average_stem_length_cm = (weighted / total) if total else 0

		if self.length_distribution and abs(total - 100) > 0.5:
			frappe.msgprint(
				_("Length distribution totals {0}%, not 100%.").format(round(total, 1)),
				title=_("Check length distribution"),
				indicator="orange",
			)
