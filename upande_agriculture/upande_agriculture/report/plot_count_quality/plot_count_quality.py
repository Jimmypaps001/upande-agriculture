"""Are the spot counts real? Per section per day: spots due vs counted, the
quality flags raised, who counted, how long a spot took, how far apart two
people's counts of the same spot were, and the section's ratio R."""
import statistics

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import service
from upande_agriculture.forecast.api import COUNT_INTERVAL_DAYS


def execute(filters=None):
	f = filters or {}
	to_date = getdate(f.get("to_date") or nowdate())
	from_date = getdate(f.get("from_date") or add_days(to_date, -13))
	cond = {"active": 1}
	if f.get("greenhouse"):
		cond["greenhouse"] = f["greenhouse"]
	# ponytail: today's active spots stand in for the spots active on each past day
	spots = frappe.get_all("Sample Plot", filters=cond, fields=["name", "greenhouse", "section"])
	counts = frappe.get_all("Bed Sample", order_by="sampling_date, creation",
		filters={"sample_plot": ["in", [s.name for s in spots] or [""]],
			"sampling_date": ["between", [add_days(from_date, -COUNT_INTERVAL_DAYS), to_date]]},
		fields=["sample_plot", "sampling_date", "counted_by", "duration_seconds", "quality_flags", "total_count"])
	by_spot = {}
	for c in counts:
		by_spot.setdefault(c.sample_plot, []).append(c)
	names = {u.name: u.full_name for u in frappe.get_all("User", fields=["name", "full_name"],
		filters={"name": ["in", list({c.counted_by for c in counts if c.counted_by}) or [""]]})}

	groups = {}
	for s in spots:
		mine = by_spot.get(s.name, [])
		for i in range((to_date - from_date).days + 1):
			day = add_days(from_date, i)
			before = [getdate(c.sampling_date) for c in mine if getdate(c.sampling_date) < day]
			today = [c for c in mine if getdate(c.sampling_date) == day]
			due = not before or (day - before[-1]).days >= COUNT_INTERVAL_DAYS
			if not (due or today):
				continue
			g = groups.setdefault((s.greenhouse, s.section, day), {"due": 0, "counted": 0, "flagged": 0,
				"flags": [], "counters": set(), "seconds": [], "recounts": []})
			g["due"] += due
			g["counted"] += bool(today)
			for c in today:
				if c.quality_flags:
					g["flagged"] += 1
					g["flags"].append(f"{s.name}: {'; '.join(c.quality_flags.splitlines())}")
				if c.counted_by:
					g["counters"].add(names.get(c.counted_by) or c.counted_by)
				if c.duration_seconds:
					g["seconds"].append(c.duration_seconds)
			people = {}
			for c in today:
				people.setdefault(c.counted_by, c.total_count or 0)  # each person's first count of the day
			if len(people) > 1:
				vals = list(people.values())
				g["recounts"].append(f"{s.name}: {' vs '.join(map(str, vals))} ({max(vals) - min(vals)} apart)")

	ratios = {}
	gh_names = {w.name: w.warehouse_name for w in frappe.get_all("Warehouse", fields=["name", "warehouse_name"],
		filters={"name": ["in", list({s.greenhouse for s in spots}) or [""]]})}
	rows = []
	for (gh, section, day), g in sorted(groups.items(), key=lambda kv: (gh_names.get(kv[0][0], ""), kv[0][1], kv[0][2])):
		if (gh, section) not in ratios:
			ratios[(gh, section)] = service.section_ratio(gh, section, to_date)[0]
		r = ratios[(gh, section)]
		rows.append({"greenhouse": gh_names.get(gh, gh), "section": section, "date": day, "due": g["due"],
			"counted": g["counted"], "flagged": g["flagged"], "flags": " | ".join(g["flags"]),
			"counters": ", ".join(sorted(g["counters"])),
			"seconds": round(statistics.median(g["seconds"])) if g["seconds"] else None,
			"recounts": " | ".join(g["recounts"]), "ratio": round(r, 2) if r else None})
	columns = [
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Data", "width": 120},
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 70},
		{"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "due", "label": _("Spots Due"), "fieldtype": "Int", "width": 85},
		{"fieldname": "counted", "label": _("Counted"), "fieldtype": "Int", "width": 80},
		{"fieldname": "flagged", "label": _("Flagged"), "fieldtype": "Int", "width": 75},
		{"fieldname": "flags", "label": _("Flags"), "fieldtype": "Data", "width": 300},
		{"fieldname": "counters", "label": _("Counted By"), "fieldtype": "Data", "width": 150},
		{"fieldname": "seconds", "label": _("Median s / Spot"), "fieldtype": "Int", "width": 110},
		{"fieldname": "recounts", "label": _("Recount Differences"), "fieldtype": "Data", "width": 220},
		{"fieldname": "ratio", "label": _("Section Ratio R"), "fieldtype": "Float", "width": 110},
	]
	return columns, rows
