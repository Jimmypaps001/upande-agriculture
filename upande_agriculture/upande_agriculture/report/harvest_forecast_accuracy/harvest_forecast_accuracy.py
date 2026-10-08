"""Each day's snapshot forecast vs the stems actually picked that day, by
section and by how far ahead the forecast was made."""
from frappe.utils import add_days, getdate, nowdate

import frappe
from frappe import _

from upande_agriculture.forecast import curves, data


def execute(filters=None):
	f = filters or {}
	to_date = getdate(f.get("to_date") or add_days(nowdate(), -1))
	from_date = getdate(f.get("from_date") or add_days(to_date, -27))
	cond = {"target_date": ["between", [from_date, to_date]]}
	if f.get("variety"):
		cond["variety"] = f["variety"]
	snaps = frappe.get_all("Harvest Forecast Snapshot", filters=cond,
		fields=["made_on", "target_date", "greenhouse", "section", "variety", "stems"])
	picked = {}
	groups = {}
	for s in snaps:
		key = (s.greenhouse, s.section)
		if key not in picked:
			picked[key] = data.cuts(s.greenhouse, s.section, from_date, to_date)
		actual = picked[key].get(getdate(s.target_date), 0.0)
		h = curves.bucket((getdate(s.target_date) - getdate(s.made_on)).days)
		g = groups.setdefault((s.greenhouse, s.section, s.variety, h), {"f": 0.0, "a": 0.0, "abs_err": 0.0, "days": 0})
		g["f"] += s.stems or 0
		g["a"] += actual
		g["days"] += 1
		g["abs_err"] += abs(actual - (s.stems or 0))
	order = [b[2] for b in curves.BUCKETS]
	rows = []
	for (gh, section, variety, h), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], order.index(kv[0][3]))):
		rows.append({"greenhouse": gh, "section": section, "variety": variety, "horizon": h,
			"forecast": round(g["f"]), "actual": round(g["a"]),
			"bias": round(100 * (g["f"] - g["a"]) / g["a"], 1) if g["a"] else None,
			"wape": round(100 * g["abs_err"] / g["a"], 1) if g["a"] else None, "days": g["days"]})
	columns = [
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 80},
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "horizon", "label": _("Days Ahead"), "fieldtype": "Data", "width": 90},
		{"fieldname": "forecast", "label": _("Forecast"), "fieldtype": "Int", "width": 90},
		{"fieldname": "actual", "label": _("Picked"), "fieldtype": "Int", "width": 90},
		{"fieldname": "bias", "label": _("Bias %"), "fieldtype": "Float", "width": 80},
		{"fieldname": "wape", "label": _("Error % (WAPE)"), "fieldtype": "Float", "width": 120},
		{"fieldname": "days", "label": _("Days"), "fieldtype": "Int", "width": 60},
	]
	return columns, rows
