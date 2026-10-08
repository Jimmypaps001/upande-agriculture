"""Weekly per-variety calibration from Bilashaka's own counts and picks.

For every past counting round (with at least a week of picks after it) the
forecast that could have been made that day is rebuilt from the stored
counts and cuts, then compared with what was actually picked, in 3-day sums.
A grid over the timing scale and regrowth days, with bud survival and regrowth
yield solved by least squares at each grid point, keeps the best fit. The
spread of actual / forecast by horizon becomes the error bands, with the
weighted absolute % error (sum |actual - forecast| / sum actual) as the third value."""
import json

import frappe
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import curves, data, engine

WINDOW_DAYS = 70
HORIZON = 21
MIN_ROUNDS, MIN_STEMS = 3, 200
K_GRID = [round(0.7 + 0.05 * i, 2) for i in range(15)]  # 0.70 .. 1.40
R_OFFSETS = (-14, -7, 0, 7, 14)


def _cases(variety, today):
	cases = []
	until = add_days(today, -7)  # a round needs a week of picks after it
	for row in data.sections(variety=variety):
		if not row.plants:
			continue
		dates = sorted({getdate(d) for d in frappe.get_all("Bed Sample", pluck="sampling_date",
			filters={"greenhouse": row.greenhouse, "section": row.section,
				"sampling_date": ["between", [add_days(today, -WINDOW_DAYS), until]]})})
		if not dates:
			continue
		history = data.cuts(row.greenhouse, row.section, add_days(dates[0], -120), add_days(today, -1))
		for d in dates:
			rnd = data.latest_round(row.greenhouse, row.section, d)
			if not rnd:
				continue
			n = min(HORIZON, (add_days(today, -1) - d).days)
			cases.append({
				"plants": int(row.plants), "round": rnd, "date": d, "greenhouse": row.greenhouse,
				"cuts": {k: v for k, v in history.items() if k <= d},
				"actual": [history.get(add_days(d, i + 1), 0.0) for i in range(n)],
			})
	return cases


def _sum3(xs):
	return [sum(xs[i:i + 3]) for i in range(0, len(xs), 3)]


def _solve(B, G, A):
	"""Non-negative least squares for A ≈ s·B + g·G (two unknowns)."""
	bb = sum(b * b for b in B); gg = sum(g * g for g in G); bg = sum(b * g for b, g in zip(B, G))
	ab = sum(a * b for a, b in zip(A, B)); ag = sum(a * g for a, g in zip(A, G))
	det = bb * gg - bg * bg
	if det > 1e-9:
		s, g = (ab * gg - ag * bg) / det, (ag * bb - ab * bg) / det
		if s >= 0 and g >= 0:
			return s, g
	err = lambda sg: sum((a - sg[0] * b - sg[1] * x) ** 2 for a, b, x in zip(A, B, G))
	return min([(ab / bb if bb else 0.0, 0.0), (0.0, ag / gg if gg else 0.0)], key=err)


def _curves(case, stages, k, R, tf):
	start, n = add_days(case["date"], 1), len(case["actual"])
	first = max((v[0] for v in stages.values()), default=0.0)
	b = [case["plants"] * x for x in
		engine.bud_curve(case["round"]["rates"], stages, case["round"]["date"], k * tf, start, n)]
	g = engine.regrowth_curve(case["cuts"], R, start, n, case["round"]["date"], k * tf, first)
	return b, g


def calibrate(variety, today=None):
	today = getdate(today or nowdate())
	name = frappe.db.exists("Harvest Forecast Calibration", variety)
	doc = frappe.get_doc("Harvest Forecast Calibration", name) if name else \
		frappe.get_doc({"doctype": "Harvest Forecast Calibration", "variety": variety})
	cases = _cases(variety, today)
	picked = sum(sum(c["actual"]) for c in cases)
	if len(cases) < MIN_ROUNDS or picked < MIN_STEMS:
		doc.status = (f"Not enough data: {len(cases)} count rounds and {int(picked)} stems picked after them "
			f"(needs {MIN_ROUNDS} rounds and {MIN_STEMS} stems). Using defaults.")
		doc.save(ignore_permissions=True)
		return doc

	stages = data.stages(variety)
	r0 = data.default_regrowth_days(variety)
	ref = data.mean_temp(sorted({c["greenhouse"] for c in cases}), add_days(today, -WINDOW_DAYS), today)
	for c in cases:
		c["tf"] = curves.temperature_factor(ref, data.recent_temp(c["greenhouse"], c["date"]))
	A = [a for c in cases for a in _sum3(c["actual"])]

	best = None
	for k in K_GRID:
		for off in R_OFFSETS:
			R = r0 + off
			B, G = [], []
			for c in cases:
				b, g = _curves(c, stages, k, R, c["tf"])
				B += _sum3(b); G += _sum3(g)
			s, g = _solve(B, G, A)
			err = sum(abs(a - s * b - g * x) for a, b, x in zip(A, B, G))
			if best is None or err < best[0]:
				best = (err, k, R, s, g)
	_, k, R, s, g = best

	ratios, errs = {}, {}
	for c in cases:
		b, gr = _curves(c, stages, k, R, c["tf"])
		f = [s * x + g * y for x, y in zip(b, gr)]
		for i in range(0, len(f), 3):
			fa, aa = sum(f[i:i + 3]), sum(c["actual"][i:i + 3])
			label = curves.bucket(i + 1)
			if fa > 0:
				ratios.setdefault(label, []).append(aa / fa)
			if aa > 0:
				e = errs.setdefault(label, [0.0, 0.0])
				e[0] += abs(aa - fa); e[1] += aa
	bands = {label: [round(curves.quantile(r, 0.1), 3), round(curves.quantile(r, 0.9), 3),
		round(errs[label][0] / errs[label][1], 3) if label in errs else 0.0] for label, r in ratios.items()}

	doc.update({"time_scale": k, "bud_survival": round(s, 4), "regrowth_days": R, "regrowth_yield": round(g, 4),
		"ref_temp": ref, "rounds_used": len(cases), "error_bands": json.dumps(bands, indent=1),
		"fitted_on": frappe.utils.now(),
		"status": f"Fitted on {len(cases)} count rounds and {int(picked)} picked stems."})
	doc.save(ignore_permissions=True)
	return doc
