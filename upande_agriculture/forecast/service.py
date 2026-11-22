"""A section's forecast: database reads -> engine -> error bands and flags."""
import frappe
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import curves, data, engine

HORIZON = 63  # 9 weeks: the counts see the whole regrowth cycle, eye pop-up ~56-59 days out
STALE_DAYS = 7
RATIO_WINDOW, RATIO_MIN_DAYS, RATIO_MIN_STEMS = 21, 10, 300
RATIO_CLAMP, RATIO_OK = (0.5, 2.0), (0.8, 1.25)
# A unit with no fresh bed count still has a harvest rate: its last RATE_DAYS
# of cuts carry it RATE_SPAN days ahead (backtest Nov 2026: 45% WAPE per unit
# over 7 days, vs a blank 0 before).
# ponytail: flat run-rate, misses flush turns; the regrowth curve takes over
# once 65+ days of cuts exist.
RATE_DAYS, RATE_SPAN = 3, 14


def section_ratio(greenhouse, section, variety, as_of):
	"""(R, days): how far the section's picks ran from its forecast compared with
	the whole variety's, over the last RATIO_WINDOW days of snapshots made 0-2
	days ahead, clamped: (section actual ÷ forecast) ÷ (variety actual ÷ forecast).
	The variety-wide error is the weekly calibration's job; leaving it out keeps
	R from correcting it a second time after each refit, so R carries only how
	(un)representative the section's spots are. None until there are enough
	days and stems to trust it."""
	since, until = add_days(as_of, -RATIO_WINDOW), add_days(as_of, -1)
	hist = data.variety_history(variety, since, until) if variety else {}
	mine = hist.get((greenhouse, section), {})
	f = sum(x[0] for x in mine.values())
	if len(mine) < RATIO_MIN_DAYS or f < RATIO_MIN_STEMS:
		return None, len(mine)
	vf = sum(x[0] for days in hist.values() for x in days.values())
	va = sum(x[1] for days in hist.values() for x in days.values())
	if not va:
		return None, len(mine)
	r = (sum(x[1] for x in mine.values()) / f) / (va / vf)
	return min(max(r, RATIO_CLAMP[0]), RATIO_CLAMP[1]), len(mine)


def forecast_section(row, start=None, days=HORIZON, as_of=None):
	start = getdate(start or nowdate())
	as_of = getdate(as_of or start)
	stages = data.stages(row.variety, row.greenhouse)
	prm = data.params(row.variety)
	rnd = data.latest_round(row.greenhouse, row.section, as_of)
	plants = int(row.plants or 0)
	tf = curves.temperature_factor(prm["ref_temp"], data.recent_temp(row.greenhouse, as_of))
	history = data.cuts(row.greenhouse, row.section, add_days(as_of, -(prm["regrowth_days"] + 45)), as_of)
	res = engine.section_forecast(start, days, plants, rnd, stages, history, prm, tf)
	ratio, ratio_days = section_ratio(row.greenhouse, row.section, row.variety, as_of)
	m = ratio or 1.0
	stale = bool(rnd and (as_of - rnd["date"]).days > STALE_DAYS)
	rate = sum(history.get(add_days(as_of, -k), 0) for k in range(1, RATE_DAYS + 1)) / RATE_DAYS
	by_rate = rate > 0 and (rnd is None or stale)
	daily = []
	for i, stems in enumerate(res["stems"]):
		lo, hi = curves.band((start - as_of).days + i, prm["error_bands"])
		stems *= m
		if by_rate and (start - as_of).days + i < RATE_SPAN:
			stems = max(stems, rate)
		daily.append({"date": add_days(start, i), "stems": stems, "low": stems * lo, "high": stems * hi,
			"buds": res["buds"][i] * m, "regrowth": res["regrowth"][i] * m})
	return {
		"greenhouse": row.greenhouse, "warehouse_name": row.warehouse_name, "section": row.section,
		"variety": row.variety, "employee": row.employee, "employee_name": row.employee_name,
		"plants": plants, "last_count": rnd["date"] if rnd else None, "model_version": prm["model_version"],
		"ratio": ratio, "ratio_days": ratio_days,
		"basis": "harvest rate" if by_rate else "bed counts",
		"flags": {"no_plants": not plants, "no_count": rnd is None, "stale_count": stale,
			"unrepresentative": bool(ratio and not RATIO_OK[0] <= ratio <= RATIO_OK[1])},
		"daily": daily,
	}


def forecast_all(farm=None, greenhouse=None, variety=None, start=None, days=HORIZON):
	out = []
	for r in data.sections(farm, greenhouse, variety):
		if not r.variety:
			continue
		try:
			out.append(forecast_section(r, start, days))
		except Exception:  # one bad section must not sink the rest
			frappe.log_error(title=f"Harvest forecast failed for {r.greenhouse} {r.section}",
				message=frappe.get_traceback())
	return out
