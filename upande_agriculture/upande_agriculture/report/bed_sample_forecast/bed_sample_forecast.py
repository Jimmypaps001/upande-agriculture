# Stems per variety (or greenhouse) per week from Bed Samples; the method is
# in upande_agriculture/bed_forecast.py.

import math
from collections import defaultdict

from frappe import _

from upande_agriculture.bed_forecast import forecast


def execute(filters=None):
	f = filters or {}
	by_gh = f.get("group_by") == "Greenhouse"
	rows = forecast(f.get("weeks_ahead") or 8, f.get("as_of"), f.get("variety"), f.get("greenhouse"))

	groups = defaultdict(list)
	for r in rows:
		groups[(r.variety, r.greenhouse if by_gh else None, r.week)].append(r)
	lengths = sorted({k for r in rows for k in r.lengths}, key=lambda k: (len(k), k))

	data = []
	for (variety, gh, wk), rs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
		stems = sum(r.stems for r in rs)
		margin = math.sqrt(sum(r.margin ** 2 for r in rs))  # independent cycles: errors add in quadrature
		row = {
			"variety": variety, "greenhouse": gh or ", ".join(sorted({r.greenhouse for r in rs})),
			"week": f"{wk[0]}-W{wk[1]:02d}", "week_start": rs[0].week_start,
			"stems": round(stems), "margin": round(margin),
			"pct": round(100 * margin / stems) if stems else None,
			"beds": sum(r.beds for r in rs), "last_sampled": min(r.last_sampled for r in rs),
			"stale": any(r.stale for r in rs),
			"factor": rs[0].factor, "basis": f"{rs[0].factor_basis}. Lengths: {rs[0].length_basis}",
		}
		for k in lengths:
			row[_key(k)] = round(sum(r.lengths.get(k, 0) for r in rs))
		data.append(row)

	columns = [
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 140},
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Data", "width": 160},
		{"fieldname": "week", "label": _("Week"), "fieldtype": "Data", "width": 90},
		{"fieldname": "week_start", "label": _("Starts"), "fieldtype": "Date", "width": 100},
		{"fieldname": "stems", "label": _("Stems"), "fieldtype": "Int", "width": 90},
		{"fieldname": "margin", "label": _("± Stems (95%)"), "fieldtype": "Int", "width": 110},
		{"fieldname": "pct", "label": _("± %"), "fieldtype": "Int", "width": 60},
		*[{"fieldname": _key(k), "label": k, "fieldtype": "Int", "width": 80} for k in lengths],
		{"fieldname": "beds", "label": _("Beds Sampled"), "fieldtype": "Int", "width": 100},
		{"fieldname": "last_sampled", "label": _("Last Sampled"), "fieldtype": "Date", "width": 105},
		{"fieldname": "factor", "label": _("Bud → Stem"), "fieldtype": "Percent", "width": 95},
		{"fieldname": "basis", "label": _("Basis"), "fieldtype": "Data", "width": 320},
	]
	for r in data:
		r["factor"] = round(r["factor"] * 100, 1)
	return columns, data


def _key(length):
	return "len_" + "".join(ch if ch.isalnum() else "_" for ch in length)
