"""Database reads for the harvest forecast. Everything Frappe lives here, so
curves.py and engine.py stay pure and testable without a site."""
import json

import frappe
from frappe.utils import add_days, getdate

from upande_agriculture.forecast import curves

ROUND_DAYS = 3        # counts within 3 days of a section's latest count are one round
MAX_COUNT_AGE = 35    # older counts have all been cut by now
DEFAULT_REGROWTH_DAYS, DEFAULT_REGROWTH_YIELD = 56, 0.8
MIN_MEASURED_STEMS = 500  # harvested stems before the measured length mix is trusted


def sections(farm=None, greenhouse=None, variety=None):
	"""Every greenhouse section (upande_core Warehouse Section) with its
	variety, harvester and plants."""
	cond = ["w.warehouse_type = 'Greenhouse'", "w.disabled = 0", "ws.parenttype = 'Warehouse'"]
	args = {}
	for key, col in (("farm", "w.custom_farm"), ("greenhouse", "w.name"), ("variety", "ws.custom_variety")):
		if locals()[key]:
			cond.append(f"{col} = %({key})s")
			args[key] = locals()[key]
	return frappe.db.sql(
		f"""SELECT ws.parent AS greenhouse, w.warehouse_name, w.custom_farm AS farm, ws.section,
		       ws.from_bed, ws.to_bed, ws.custom_variety AS variety, ws.custom_employee AS employee,
		       ws.custom_employee_name AS employee_name, ws.custom_plants AS plants
		   FROM `tabWarehouse Section` ws JOIN `tabWarehouse` w ON w.name = ws.parent
		   WHERE {" AND ".join(cond)} ORDER BY w.warehouse_name, ws.section""",
		args, as_dict=True)


def stages(variety):
	protocol = variety and frappe.db.get_value("Crop Protocol", {"variety_item": variety})
	rows = protocol and frappe.get_all("Crop Protocol Growth Stage",
		filters={"parent": protocol, "parenttype": "Crop Protocol"},
		fields=["stage_name", "days_to_harvest", "spread_days", "survival"], order_by="idx")
	rows = [dict(r, survival=(r.survival / 100 if r.survival else None)) for r in (rows or [])]
	return curves.stage_params(rows)


def latest_round(greenhouse, section, as_of):
	"""The section's latest counting round up to `as_of`: each plot's latest
	count within ROUND_DAYS of the newest one, as buds per plant per stage."""
	as_of = getdate(as_of)
	rows = frappe.get_all("Bed Sample",
		filters={"greenhouse": greenhouse, "section": section,
			"sampling_date": ["between", [add_days(as_of, -MAX_COUNT_AGE), as_of]]},
		fields=["name", "sample_plot", "sampling_date", "plants_counted"],
		order_by="sampling_date desc, creation desc")
	if not rows:
		return None
	last = getdate(rows[0].sampling_date)
	per_plot = {}
	for r in rows:
		if (last - getdate(r.sampling_date)).days >= ROUND_DAYS:
			break
		per_plot.setdefault(r.sample_plot or r.name, r)
	picked = list(per_plot.values())
	plants = sum(r.plants_counted or 0 for r in picked)
	if not plants:
		return None
	totals = {}
	for s in frappe.get_all("Bed Sample Stage", fields=["stage_name", "count"],
			filters={"parent": ["in", [r.name for r in picked]], "parenttype": "Bed Sample"}):
		totals[s.stage_name] = totals.get(s.stage_name, 0) + (s.count or 0)
	return {"date": last, "rates": {k: v / plants for k, v in totals.items()},
		"plants_counted": plants, "plots": len(picked)}


def cuts(greenhouse, section, since, until):
	"""{day: stems} cut in a section: its submitted Harvesting entries."""
	rows = frappe.db.sql(
		"""SELECT se.posting_date AS d, SUM(sed.qty) AS q
		   FROM `tabStock Entry` se JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
		     AND se.custom_greenhouse = %s AND se.custom_section = %s
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY se.posting_date""",
		(greenhouse, section, getdate(since), getdate(until)), as_dict=True)
	return {getdate(r.d): float(r.q) for r in rows}


def recent_temp(greenhouse, as_of, days=7):
	return mean_temp([greenhouse], add_days(as_of, -(days - 1)), as_of)


def mean_temp(greenhouses, since, until):
	if not greenhouses:
		return None
	v = frappe.db.sql(
		"""SELECT AVG(mean_temp) FROM `tabGreenhouse Temperature`
		   WHERE greenhouse IN %s AND date BETWEEN %s AND %s""",
		(tuple(greenhouses), getdate(since), getdate(until)))[0][0]
	return float(v) if v is not None else None


def default_regrowth_days(variety):
	weeks = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "weeks_between_cuts")
	return int(weeks * 7) if weeks else DEFAULT_REGROWTH_DAYS


def params(variety):
	p = {"time_scale": 1.0, "bud_survival": 1.0, "regrowth_days": default_regrowth_days(variety),
		"regrowth_yield": DEFAULT_REGROWTH_YIELD, "ref_temp": None, "error_bands": {}, "model_version": "defaults"}
	cal = frappe.db.get_value("Harvest Forecast Calibration", variety,
		["time_scale", "bud_survival", "regrowth_days", "regrowth_yield", "ref_temp", "error_bands", "fitted_on"],
		as_dict=True)
	if cal and cal.fitted_on:
		p.update({"time_scale": float(cal.time_scale or 1), "bud_survival": float(cal.bud_survival or 0),
			"regrowth_days": int(cal.regrowth_days or p["regrowth_days"]),
			"regrowth_yield": float(cal.regrowth_yield or 0), "ref_temp": cal.ref_temp,
			"error_bands": json.loads(cal.error_bands or "{}"), "model_version": f"{variety}@{cal.fitted_on}"})
	return p


def length_mix(variety, as_of):
	"""({length: share}, basis): the last 4 weeks' harvest if there is enough
	of it, else the Crop Protocol's grade mix."""
	rows = frappe.db.sql(
		"""SELECT iva.attribute_value AS length, SUM(sed.qty) AS qty
		   FROM `tabStock Entry` se
		   JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   JOIN `tabItem Variant Attribute` iva ON iva.parent = i.name
		        AND iva.attribute IN ('Length', 'Stem Length')
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting' AND i.variant_of = %s
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY iva.attribute_value""",
		(variety, add_days(as_of, -28), as_of), as_dict=True)
	total = sum(r.qty for r in rows)
	if total >= MIN_MEASURED_STEMS:
		return {r.length: float(r.qty / total) for r in rows}, f"Harvested, last 4 weeks ({int(total)} stems)"
	protocol = frappe.db.get_value("Crop Protocol", {"variety_item": variety})
	mix = protocol and frappe.get_all("Crop Protocol Grade Mix",
		filters={"parent": protocol, "parenttype": "Crop Protocol"}, fields=["length_cm", "pct"])
	if mix:
		return {f"{m.length_cm}cm": (m.pct or 0) / 100 for m in mix}, "Crop Protocol grade mix"
	return {}, "No length mix yet"
