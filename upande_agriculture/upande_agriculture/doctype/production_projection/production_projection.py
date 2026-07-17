# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


def _actuals_map(greenhouse, crop_variety, year):
	"""Actual harvested stems per ISO week for a greenhouse/variety/year.

	Sums submitted 'Harvesting' Stock Entries. YEARWEEK(..., 3) is ISO-week
	aware, so FLOOR(YEARWEEK/100) is the true ISO year and MOD(...,100) the
	ISO week number.
	"""
	if not (greenhouse and crop_variety and year):
		return {}
	rows = frappe.db.sql(
		"""
		SELECT
			MOD(YEARWEEK(se.posting_date, 3), 100) AS week_no,
			SUM(sed.qty)                           AS actual_stems
		FROM `tabStock Entry` se
		JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.docstatus = 1
			AND se.stock_entry_type = 'Harvesting'
			AND se.custom_greenhouse = %(greenhouse)s
			AND sed.item_code = %(crop_variety)s
			AND FLOOR(YEARWEEK(se.posting_date, 3) / 100) = %(year)s
		GROUP BY MOD(YEARWEEK(se.posting_date, 3), 100)
		""",
		{"greenhouse": greenhouse, "crop_variety": crop_variety, "year": int(year)},
		as_dict=True,
	)
	return {int(r.week_no): (r.actual_stems or 0) for r in rows}


def refresh_projection_actuals(name, commit=False):
	"""Recompute and store actual_harvest for every week of one projection."""
	proj = frappe.db.get_value(
		"Production Projection", name, ["greenhouse", "crop_variety", "year"], as_dict=True
	)
	if not proj:
		return 0
	amap = _actuals_map(proj.greenhouse, proj.crop_variety, proj.year)
	weeks = frappe.get_all(
		"Projection Week",
		filters={"parent": name, "parenttype": "Production Projection"},
		fields=["name", "week_no"],
	)
	updated = 0
	for w in weeks:
		val = amap.get(int(w.week_no or 0), 0)
		frappe.db.set_value("Projection Week", w.name, "actual_harvest", val, update_modified=False)
		if val:
			updated += 1
	if commit:
		frappe.db.commit()
	return updated


def update_projections_from_stock_entry(doc, method=None):
	"""Stock Entry hook: on submit/cancel of a Harvesting entry, refresh the
	actuals of every Production Projection that could be affected — real time."""
	if getattr(doc, "stock_entry_type", None) != "Harvesting":
		return
	greenhouse = doc.get("custom_greenhouse")
	if not greenhouse:
		return
	items = {d.item_code for d in (doc.items or []) if d.item_code}
	if not items:
		return
	names = frappe.get_all(
		"Production Projection",
		filters={"greenhouse": greenhouse, "crop_variety": ["in", list(items)]},
		pluck="name",
	)
	for name in names:
		# each projection's own year filter keeps the week math correct
		refresh_projection_actuals(name)


class ProductionProjection(Document):
	def onload(self):
		"""Fill actuals live when the form is opened — no button needed."""
		amap = _actuals_map(self.greenhouse, self.crop_variety, self.year)
		for w in self.projection_week:
			w.actual_harvest = amap.get(int(w.week_no or 0), 0)
