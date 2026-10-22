"""Database reads for the harvest forecast. Everything Frappe lives here, so
curves.py and engine.py stay pure and testable without a site."""
import json
import re

import frappe
from frappe.utils import add_days, getdate

from upande_agriculture.forecast import curves

ROUND_DAYS = 3        # counts within 3 days of a section's latest count are one round
MAX_COUNT_AGE = 60    # older counts have all been cut by now (an eye pop-up is ~56-59 days out)
# Cut -> eye pop-up 8-10 d -> maroon ~35 d -> pea -> knife 21 d: ~65 days (Bilashaka farm manager).
DEFAULT_REGROWTH_DAYS, DEFAULT_REGROWTH_YIELD = 65, 0.8
MIN_MEASURED_STEMS = 500  # harvested stems before the measured length mix is trusted


def _bed_span(spec):
	"""(lowest, highest) bed in a range like "12-40" or "1-10, 15-20"; (None, None) if none."""
	nums = [int(float(n)) for n in re.findall(r"\d+(?:\.\d+)?", spec or "")]
	return (min(nums), max(nums)) if nums else (None, None)


def _int(text):
	try:
		return int(float(str(text or "").replace(",", "").replace(" ", "") or 0))
	except ValueError:
		return 0


def _table_units(rows):
	"""[(variety, plants, shared_with)] from one greenhouse's Varieties per GH rows
	(in idx order). The client's sheet had merged cells: consecutive rows with the
	same (no_of_plants, size_m2) and different varieties are ONE block shared by
	those varieties, so its plants are split equally between them."""
	blocks = []
	for r in rows:
		key = (_int(r.no_of_plants), (r.size_m2 or "").replace(",", "").strip())
		b = blocks[-1] if blocks else None
		if r.variety and b and b["key"] == key and key[0] and r.variety not in b["varieties"]:
			b["varieties"].append(r.variety)
		else:  # a blank variety still breaks the run of merged rows
			blocks.append({"key": key, "varieties": [r.variety] if r.variety else []})
	out = []
	for b in blocks:
		vs, total = b["varieties"], b["key"][0]
		for i, v in enumerate(vs):  # the odd stem goes to the last varieties: 161723 -> 80861, 80862
			out.append((v, total // len(vs) + (1 if i >= len(vs) - total % len(vs) else 0), [x for x in vs if x != v]))
	return out


def sections(farm=None, greenhouse=None, variety=None):
	"""The forecast units: one per greenhouse x variety. `section` is the variety
	(the unit key on Sample Plot, Bed Sample and Harvest Forecast Snapshot).
	Plants come from the greenhouse's Active Crop Cycles, or when it has none
	from its Varieties per GH table. Only rose-like varieties (Item templates
	with length variants) are forecast."""
	cond, args = ["warehouse_type = 'Greenhouse'", "disabled = 0"], {}
	for key, col, val in (("farm", "custom_farm", farm), ("greenhouse", "name", greenhouse)):
		if val:
			cond.append(f"{col} = %({key})s")
			args[key] = val
	houses = {w.name: w for w in frappe.db.sql(
		f"SELECT name, warehouse_name, custom_farm AS farm FROM `tabWarehouse` WHERE {' AND '.join(cond)}",
		args, as_dict=True)}
	if not houses:
		return []
	units = {}

	def add(gh, v, plants, beds, shared, source):
		u = units.setdefault((gh, v), {"plants": 0, "beds": [], "shared_with": [], "source": source})
		u["plants"] += plants
		u["beds"] += [b for b in beds if b is not None]
		u["shared_with"] += [x for x in shared if x not in u["shared_with"]]

	for c in frappe.db.sql(
			"""SELECT greenhouse, variety, plants_standing, qty_planted, bed_range FROM `tabCrop Cycle`
			   WHERE status = 'Active' AND greenhouse IN %s""", (tuple(houses),), as_dict=True):
		add(c.greenhouse, c.variety, int(c.plants_standing or c.qty_planted or 0), _bed_span(c.bed_range), [], "Crop Cycle")
	if frappe.db.table_exists("Varieties per GH"):
		done = {gh for gh, _v in units}
		rows = {}
		for r in frappe.db.sql(
				"""SELECT parent, variety, no_of_plants, size_m2 FROM `tabVarieties per GH`
				   WHERE parenttype = 'Warehouse' AND parentfield = 'custom_varieties_grown' AND parent IN %s
				   ORDER BY parent, idx""", (tuple(houses),), as_dict=True):
			if r.parent not in done:
				rows.setdefault(r.parent, []).append(r)
		for gh, rs in rows.items():
			for v, plants, shared in _table_units(rs):
				add(gh, v, plants, [], shared, "Greenhouse varieties")

	roses = set(frappe.get_all("Item", pluck="name",
		filters={"has_variants": 1, "name": ["in", list({v for _g, v in units} or [""])]}))
	out = []
	for (gh, v), u in units.items():
		if v not in roses or (variety and v != variety):
			continue
		w = houses[gh]
		out.append(frappe._dict(greenhouse=gh, warehouse_name=w.warehouse_name, farm=w.farm, section=v, variety=v,
			plants=u["plants"], from_bed=min(u["beds"]) if u["beds"] else None,
			to_bed=max(u["beds"]) if u["beds"] else None, employee=None, employee_name=None,
			shared_with=u["shared_with"], source=u["source"]))
	return sorted(out, key=lambda r: (r.warehouse_name or "", r.variety))


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
	"""{day: stems} cut in a unit (`section` is the variety): the greenhouse's
	submitted Harvesting entries of the variety's length variants."""
	rows = frappe.db.sql(
		"""SELECT se.posting_date AS d, SUM(sed.qty) AS q
		   FROM `tabStock Entry` se JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
		     AND se.custom_greenhouse = %s AND (i.variant_of = %s OR i.name = %s)
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY se.posting_date""",
		(greenhouse, section, section, getdate(since), getdate(until)), as_dict=True)
	return {getdate(r.d): float(r.q) for r in rows}


def variety_history(variety, since, until, max_ahead=2):
	"""{(greenhouse, section): {day: (raw forecast, picked)}} for every day a
	snapshot made 0..max_ahead days ahead forecast for one of the variety's
	units (section = the variety; older section-keyed rows are left out): the
	forecast before any section ratio (averaged over those snapshots) and the
	stems actually picked."""
	fc = frappe.db.sql(
		"""SELECT greenhouse, section, target_date AS d, AVG(stems / IF(IFNULL(ratio, 0) > 0, ratio, 1)) AS f
		   FROM `tabHarvest Forecast Snapshot`
		   WHERE variety = %s AND section = variety AND target_date BETWEEN %s AND %s
		     AND DATEDIFF(target_date, made_on) BETWEEN 0 AND %s
		   GROUP BY greenhouse, section, target_date""",
		(variety, getdate(since), getdate(until), max_ahead), as_dict=True)
	if not fc:
		return {}
	picked = {(r.g, variety, getdate(r.d)): float(r.q) for r in frappe.db.sql(
		"""SELECT se.custom_greenhouse AS g, se.posting_date AS d, SUM(sed.qty) AS q
		   FROM `tabStock Entry` se JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
		     AND se.custom_greenhouse IN %s AND se.posting_date BETWEEN %s AND %s
		     AND (i.variant_of = %s OR i.name = %s)
		   GROUP BY se.custom_greenhouse, se.posting_date""",
		(tuple({r.greenhouse for r in fc}), getdate(since), getdate(until), variety, variety), as_dict=True)}
	out = {}
	for r in fc:
		d = getdate(r.d)
		out.setdefault((r.greenhouse, r.section), {})[d] = (float(r.f or 0), picked.get((r.greenhouse, r.section, d), 0.0))
	return out


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


def _bands(raw):
	try:
		return json.loads(raw or "{}")
	except ValueError:  # hand-edited in Desk: fall back to the default bands
		return {}


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
			"error_bands": _bands(cal.error_bands), "model_version": f"{variety}@{cal.fitted_on}"})
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
