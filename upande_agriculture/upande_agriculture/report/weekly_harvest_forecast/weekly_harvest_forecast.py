"""Stems per variety per week for sales: this week's actual picks so far plus
the forecast for the rest of the week, then whole forecast weeks, split by
length. Bands add in quadrature (sections vary independently)."""
import math

from frappe import _
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data, service
from upande_agriculture.weekcal import get_week_rule, week_key, week_start


def execute(filters=None):
	f = filters or {}
	weeks = max(1, int(f.get("weeks") or 3))
	by_gh = f.get("group_by") == "Greenhouse"
	today = getdate(nowdate())
	rule = get_week_rule()
	first = week_start(*week_key(today, rule), rule)
	end = add_days(first, weeks * 7 - 1)
	forecasts = service.forecast_all(farm=f.get("farm"), variety=f.get("variety"), start=today,
		days=(end - today).days + 1)

	groups = {}

	def group(fc, wk):
		key = (fc["variety"], fc["warehouse_name"] if by_gh else None, wk)
		return groups.setdefault(key, {"forecast": 0.0, "hw2": 0.0, "actual": 0.0, "sections": set(), "warn": set()})

	for fc in forecasts:
		for d in fc["daily"]:
			g = group(fc, week_key(d["date"], rule))
			g["forecast"] += d["stems"]
			g["hw2"] += ((d["high"] - d["low"]) / 2) ** 2
			g["sections"].add((fc["greenhouse"], fc["section"]))
			g["warn"].update(k for k, v in fc["flags"].items() if v)
		if today > first:
			so_far = data.cuts(fc["greenhouse"], fc["section"], first, add_days(today, -1))
			group(fc, week_key(first, rule))["actual"] += sum(so_far.values())

	mixes = {v: data.length_mix(v, today) for v in {k[0] for k in groups}}
	lengths = sorted({ln for m, _b in mixes.values() for ln in m}, key=lambda s: (len(s), s))
	rows = []
	for (variety, gh, wk), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
		total = g["actual"] + g["forecast"]
		mix, basis = mixes[variety]
		row = {"variety": variety, "greenhouse": gh or "", "week": f"{wk[0]}-W{wk[1]:02d}",
			"starts": week_start(*wk, rule), "actual": round(g["actual"]), "forecast": round(g["forecast"]),
			"total": round(total), "margin": round(math.sqrt(g["hw2"])), "sections": len(g["sections"]),
			"basis": "; ".join(sorted(g["warn"]) + [f"lengths: {basis}"])}
		for ln in lengths:
			row["len_" + ln] = round(total * mix.get(ln, 0))
		rows.append(row)

	columns = [
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Data", "width": 120, "hidden": 0 if by_gh else 1},
		{"fieldname": "week", "label": _("Week"), "fieldtype": "Data", "width": 90},
		{"fieldname": "starts", "label": _("Starts"), "fieldtype": "Date", "width": 100},
		{"fieldname": "actual", "label": _("Picked So Far"), "fieldtype": "Int", "width": 105},
		{"fieldname": "forecast", "label": _("Forecast Rest"), "fieldtype": "Int", "width": 105},
		{"fieldname": "total", "label": _("Week Total"), "fieldtype": "Int", "width": 95},
		{"fieldname": "margin", "label": _("± Stems"), "fieldtype": "Int", "width": 80},
		*[{"fieldname": "len_" + ln, "label": ln, "fieldtype": "Int", "width": 75} for ln in lengths],
		{"fieldname": "sections", "label": _("Sections"), "fieldtype": "Int", "width": 75},
		{"fieldname": "basis", "label": _("Basis"), "fieldtype": "Data", "width": 300},
	]
	return columns, rows
