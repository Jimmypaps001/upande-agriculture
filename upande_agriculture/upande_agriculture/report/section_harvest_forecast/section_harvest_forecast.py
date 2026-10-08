"""Expected stems per section per day: what each bay's harvester will cut."""
from frappe import _
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import service

FLAG_TEXT = {"no_plants": "Plants not set on the section (regrowth only)",
	"no_count": "No plot count in 35 days (regrowth only)", "stale_count": "Last count over a week old"}


def execute(filters=None):
	f = filters or {}
	days = int(f.get("days") or 14)
	start = getdate(nowdate())
	forecasts = service.forecast_all(farm=f.get("farm"), greenhouse=f.get("greenhouse"),
		variety=f.get("variety"), start=start, days=days)
	columns = [
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Data", "width": 120},
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 70},
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 120},
		{"fieldname": "harvester", "label": _("Harvester"), "fieldtype": "Data", "width": 130},
		*[{"fieldname": f"d{i}", "label": add_days(start, i).strftime("%a %d"), "fieldtype": "Int", "width": 64}
			for i in range(days)],
		{"fieldname": "total", "label": _("Total"), "fieldtype": "Int", "width": 80},
		{"fieldname": "low", "label": _("Low"), "fieldtype": "Int", "width": 70},
		{"fieldname": "high", "label": _("High"), "fieldtype": "Int", "width": 70},
		{"fieldname": "last_count", "label": _("Last Count"), "fieldtype": "Date", "width": 100},
		{"fieldname": "basis", "label": _("Basis"), "fieldtype": "Data", "width": 300},
	]
	rows = []
	for fc in forecasts:
		d = fc["daily"]
		warn = [FLAG_TEXT[k] for k, v in fc["flags"].items() if v]
		row = {"greenhouse": fc["warehouse_name"], "section": fc["section"], "variety": fc["variety"],
			"harvester": fc["employee_name"], "total": round(sum(x["stems"] for x in d)),
			"low": round(sum(x["low"] for x in d)), "high": round(sum(x["high"] for x in d)),
			"last_count": fc["last_count"], "warn": bool(warn),
			"basis": "; ".join(warn + [f"model {fc['model_version']}"])}
		for i, x in enumerate(d):
			row[f"d{i}"] = round(x["stems"])
		rows.append(row)
	return columns, rows
