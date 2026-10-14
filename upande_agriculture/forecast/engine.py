"""The section forecast, pure: no Frappe.

Two sources of stems for a day:
- buds counted on the sample plots, per plant, each stage spread over days by
  its curve and scaled to the section's plants;
- regrowth: every stem cut grows back one cycle later. A cut whose new bud had
  already reached the earliest counted stage by the last count is part of that
  count, so it is not added twice. A cut shows an eye only ~9 days later, so
  younger cuts always count as regrowth.
"""
from datetime import timedelta

from upande_agriculture.forecast.curves import LEGACY_STAGES, day_mass, stage_mass, stage_params

# a counted stage the protocol does not list falls back to these, by lower-case name
DEFAULTS = {n.lower(): v for n, v in {**stage_params(LEGACY_STAGES), **stage_params(None)}.items()}

REGROWTH_SPREAD = 0.12  # sd of the regrowth timing as a share of its days
SHOOT_VISIBLE_DAYS = 9  # a cut shows a new eye 8-10 days later; younger cuts are not in any count


def bud_curve(rates, stages, count_date, k, start, days):
	"""Expected stems per plant per day, from `start` for `days` days."""
	out = [0.0] * days
	first = (start - count_date).days
	known = {**DEFAULTS, **{n.lower(): v for n, v in stages.items()}}
	for name, rate in rates.items():
		if not rate or name.lower() not in known:
			continue
		mean, spread, survival = known[name.lower()]
		for i in range(days):
			out[i] += rate * survival * stage_mass(first + i, k * mean, k * spread)
	return out


def regrowth_curve(cuts, regrowth_days, start, days, visible_after=None, k=1.0, first_stage_days=0.0):
	"""Expected new stems per day (one per stem cut) from past cuts {date: stems}."""
	sd = max(1.0, REGROWTH_SPREAD * regrowth_days)
	out = [0.0] * days
	for cut_on, stems in cuts.items():
		# the earliest stage starts regrowth - first days after the cut, but never before the eye shows
		seen = max(regrowth_days - k * first_stage_days, k * SHOOT_VISIBLE_DAYS)
		if visible_after and cut_on + timedelta(days=seen) <= visible_after:
			continue
		first = (start - cut_on).days
		for i in range(days):
			out[i] += stems * day_mass(first + i, regrowth_days, sd)
	return out


def section_forecast(start, days, plants, round_, stages, cuts, params, temp_factor=1.0):
	"""{"buds", "regrowth", "stems"}: lists of expected stems per day from `start`."""
	k = params["time_scale"] * temp_factor
	buds, visible_after = [0.0] * days, None
	if round_ and plants:
		per_plant = bud_curve(round_["rates"], stages, round_["date"], k, start, days)
		buds = [plants * params["bud_survival"] * x for x in per_plant]
		visible_after = round_["date"]
	first = max((v[0] for v in stages.values()), default=0.0)
	regrowth = [params["regrowth_yield"] * x for x in
		regrowth_curve(cuts, params["regrowth_days"], start, days, visible_after, k, first)]
	return {"buds": buds, "regrowth": regrowth, "stems": [a + b for a, b in zip(buds, regrowth)]}
