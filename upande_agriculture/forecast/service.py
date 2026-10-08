"""A section's forecast: database reads -> engine -> error bands and flags."""
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import curves, data, engine

HORIZON = 21
STALE_DAYS = 7


def forecast_section(row, start=None, days=HORIZON, as_of=None):
	start = getdate(start or nowdate())
	as_of = getdate(as_of or start)
	stages = data.stages(row.variety)
	prm = data.params(row.variety)
	rnd = data.latest_round(row.greenhouse, row.section, as_of)
	plants = int(row.plants or 0)
	tf = curves.temperature_factor(prm["ref_temp"], data.recent_temp(row.greenhouse, as_of))
	history = data.cuts(row.greenhouse, row.section, add_days(as_of, -(prm["regrowth_days"] + 45)), as_of)
	res = engine.section_forecast(start, days, plants, rnd, stages, history, prm, tf)
	daily = []
	for i, stems in enumerate(res["stems"]):
		lo, hi = curves.band((start - as_of).days + i, prm["error_bands"])
		daily.append({"date": add_days(start, i), "stems": stems, "low": stems * lo, "high": stems * hi,
			"buds": res["buds"][i], "regrowth": res["regrowth"][i]})
	return {
		"greenhouse": row.greenhouse, "warehouse_name": row.warehouse_name, "section": row.section,
		"variety": row.variety, "employee": row.employee, "employee_name": row.employee_name,
		"plants": plants, "last_count": rnd["date"] if rnd else None, "model_version": prm["model_version"],
		"flags": {"no_plants": not plants, "no_count": rnd is None,
			"stale_count": bool(rnd and (as_of - rnd["date"]).days > STALE_DAYS)},
		"daily": daily,
	}


def forecast_all(farm=None, greenhouse=None, variety=None, start=None, days=HORIZON):
	return [forecast_section(r, start, days) for r in data.sections(farm, greenhouse, variety) if r.variety]
