# Stems per variety per week, from Bed Samples.
#
#   buds per plant at each stage (a sample counts N plants in a row)
#   x plants standing in the crop cycle         -> buds in the greenhouse
#   landing on sampling date + stage's days to harvest (+-3 days)
#   x correction factor                         -> stems actually harvested
#   split by the variety's length mix
#
# The correction factor is learned per lead time: what was harvested in each
# finished week divided by what the counts made L weeks earlier predicted.
# Old predictions are recomputed from the stored samples (each sample keeps its
# own days-to-harvest), so nothing has to be snapshotted.
# The margin is the spread between sampled beds (95%, 1.96 x standard error).

import datetime
import math
from collections import defaultdict

import frappe
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.weekcal import get_week_rule, week_key, week_start

SPREAD_DAYS = 3  # ponytail: a stage lands +-3 days around its days-to-harvest; per-stage spread if weeks look spiky
ROUND_DAYS = 7  # samples within a week of a cycle's latest sample are one sampling round
STALE_DAYS = 14  # an older round still forecasts, but is flagged
MIN_FACTOR_WEEKS = 3  # finished weeks needed before the learned factor replaces the protocol's
FACTOR_WEEKS = 8  # how far back the factor learns
MAX_LEAD = 10  # weeks ahead a count can reach (longest days-to-harvest is ~8 weeks)
MIN_MEASURED_STEMS = 500  # harvested stems needed before the measured length mix replaces the protocol's


def forecast(weeks_ahead=8, as_of=None, variety=None, greenhouse=None):
	"""One row per crop cycle per week: stems, margin, length split, basis."""
	as_of = getdate(as_of or nowdate())
	rule = get_week_rule()
	this_week = week_key(as_of, rule)
	horizon = {week_key(add_days(week_start(*this_week, rule), 7 * i), rule) for i in range(int(weeks_ahead))}

	cycles = _cycles(variety, greenhouse)
	samples = _samples([c.name for c in cycles], add_days(as_of, -7 * (FACTOR_WEEKS + 10)), as_of)
	factors = {v: _factor(v, [c for c in cycles if c.variety == v], samples, as_of, rule) for v in {c.variety for c in cycles}}
	mixes = {v: _length_mix(v, as_of) for v in factors}

	rows = []
	for c in cycles:
		rnd = _round(samples.get(c.name, []), as_of)
		if not rnd:
			continue
		plants = c.plants_standing or c.qty_planted or 0
		pred = _predict(rnd, plants, rule)
		last = max(s.sampling_date for s in rnd)
		round_week = week_start(*week_key(last, rule), rule)
		for wk in sorted(horizon):
			mean, se = pred.get(wk, (0, 0))
			lead = min(MAX_LEAD, max(0, (week_start(*wk, rule) - round_week).days // 7))
			factor, basis = factors[c.variety][lead]
			stems, margin = mean * factor, 1.96 * se * factor
			rows.append(frappe._dict(
				variety=c.variety, greenhouse=c.greenhouse, crop_cycle=c.name, week=wk,
				week_start=week_start(*wk, rule), stems=stems, margin=margin,
				lengths={k: stems * p for k, p in mixes[c.variety][0].items()}, length_basis=mixes[c.variety][1],
				beds=len(rnd), plants=plants, last_sampled=last, stale=(as_of - last).days > STALE_DAYS,
				factor=factor, factor_basis=basis,
			))
	return rows


def _cycles(variety=None, greenhouse=None):
	filters = {"status": "Active"}
	if variety:
		filters["variety"] = variety
	if greenhouse:
		filters["greenhouse"] = greenhouse
	return frappe.get_all(
		"Crop Cycle", filters=filters,
		fields=["name", "greenhouse", "variety", "crop_protocol", "plants_standing", "qty_planted"],
	)


def _samples(cycle_names, since, until):
	"""{crop_cycle: [sample with .stages]} between two dates."""
	if not cycle_names:
		return {}
	heads = frappe.get_all(
		"Bed Sample",
		filters={"crop_cycle": ["in", cycle_names], "sampling_date": ["between", [since, until]]},
		fields=["name", "crop_cycle", "sampling_date", "plants_counted"],
	)
	stages = defaultdict(list)
	for r in frappe.get_all(
		"Bed Sample Stage",
		filters={"parenttype": "Bed Sample", "parent": ["in", [h.name for h in heads] or [""]]},
		fields=["parent", "count", "days_to_harvest"],
	):
		stages[r.parent].append(r)
	out = defaultdict(list)
	for h in heads:
		h.sampling_date = getdate(h.sampling_date)
		h.stages = stages[h.name]
		out[h.crop_cycle].append(h)
	return out


def _round(samples, before):
	"""The latest sampling round on or before a date: that day's samples and
	any in the week before it (one walk over the beds may take days)."""
	before = getdate(before)
	usable = [s for s in samples if s.sampling_date <= before]
	if not usable:
		return []
	latest = max(s.sampling_date for s in usable)
	return [s for s in usable if (latest - s.sampling_date).days < ROUND_DAYS]


def _predict(rnd, plants, rule):
	"""{week: (mean stems, standard error)} for the whole crop cycle. Each
	sampled bed gives its own estimate; their spread is the error."""
	per_bed = []
	for s in rnd:
		weeks = defaultdict(float)
		per_plant = 1 / (s.plants_counted or 1)
		for st in s.stages:
			if st.days_to_harvest is None or not st.count:
				continue  # a stage with no days to harvest cannot be placed in a week
			share = st.count * per_plant * plants / (2 * SPREAD_DAYS + 1)
			for off in range(-SPREAD_DAYS, SPREAD_DAYS + 1):
				weeks[week_key(add_days(s.sampling_date, st.days_to_harvest + off), rule)] += share
		per_bed.append(weeks)
	n = len(per_bed)
	out = {}
	for wk in {w for b in per_bed for w in b}:
		vals = [b.get(wk, 0.0) for b in per_bed]
		mean = sum(vals) / n
		sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else mean  # one bed: no spread known, say +-100%
		out[wk] = (mean, sd / math.sqrt(n))
	return out


def _factor(variety, cycles, samples, as_of, rule):
	"""lead -> (factor, basis) for turning counted buds into harvested stems.

	A count made L weeks before a harvest week is scored only against other
	counts made L weeks ahead: a Pea bud three weeks out loses more on the way
	than a bud already showing colour, so one factor for all leads would be
	wrong at both ends. Leads short of history use all leads pooled, then the
	protocol's reject %."""
	this_week = week_key(as_of, rule)
	pred, actual, used = defaultdict(float), defaultdict(float), defaultdict(int)
	for i in range(1, FACTOR_WEEKS + 1):
		start = week_start(*week_key(add_days(week_start(*this_week, rule), -7 * i), rule), rule)
		wk = week_key(start, rule)
		harvest = {c.name: harvested(c.greenhouse, variety, start, add_days(start, 6)) for c in cycles}
		for lead in range(MAX_LEAD + 1):
			round_end = add_days(start, -7 * lead + 6)  # last day of the week the count was made in
			p = a = 0.0
			for c in cycles:
				rnd = _round(samples.get(c.name, []), round_end)
				if rnd and (round_end - max(s.sampling_date for s in rnd)).days < 7:
					p += _predict(rnd, c.plants_standing or c.qty_planted or 0, rule).get(wk, (0, 0))[0]
					a += harvest[c.name]
			if p > 0:
				pred[lead], actual[lead], used[lead] = pred[lead] + p, actual[lead] + a, used[lead] + 1

	reject = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "reject_pct")
	pooled_weeks = sum(used.values())
	if pooled_weeks >= MIN_FACTOR_WEEKS:
		fallback = (sum(actual.values()) / sum(pred.values()), f"Learned, all leads pooled ({pooled_weeks} week-scores)")
	elif reject:
		fallback = (1 - reject / 100, "Protocol reject % (too little harvest history yet)")
	else:
		fallback = (1.0, "None yet (too little harvest history)")
	return {
		lead: (actual[lead] / pred[lead], f"Learned at {lead} weeks ahead ({used[lead]} weeks)")
		if used[lead] >= MIN_FACTOR_WEEKS else fallback
		for lead in range(MAX_LEAD + 1)
	}


def harvested(greenhouse, variety, start, end):
	"""Stems of a variety (any length) harvested into a greenhouse."""
	return frappe.db.sql(
		"""SELECT COALESCE(SUM(sed.qty), 0)
		   FROM `tabStock Entry` se
		   JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
		     AND sed.t_warehouse = %s AND (i.variant_of = %s OR i.name = %s)
		     AND se.posting_date BETWEEN %s AND %s""",
		(greenhouse, variety, variety, start, end),
	)[0][0]


def _length_mix(variety, as_of):
	"""({length: share}, basis): the last 4 weeks' harvest if there is enough
	of it, else the Crop Protocol's grade mix."""
	rows = frappe.db.sql(
		"""SELECT iva.attribute_value AS length, SUM(sed.qty) AS qty
		   FROM `tabStock Entry` se
		   JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   JOIN `tabItem Variant Attribute` iva ON iva.parent = i.name AND iva.attribute = 'Length'
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting' AND i.variant_of = %s
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY iva.attribute_value""",
		(variety, add_days(as_of, -28), as_of), as_dict=True,
	)
	total = sum(r.qty for r in rows)
	if total >= MIN_MEASURED_STEMS:
		return {r.length: float(r.qty / total) for r in rows}, f"Harvested, last 4 weeks ({int(total)} stems)"
	protocol = frappe.db.get_value("Crop Protocol", {"variety_item": variety})
	mix = protocol and frappe.get_all(
		"Crop Protocol Grade Mix", filters={"parent": protocol, "parenttype": "Crop Protocol"}, fields=["length_cm", "pct"]
	)
	if mix:
		return {f"{m.length_cm}cm": (m.pct or 0) / 100 for m in mix}, "Crop Protocol grade mix"
	return {}, "No length mix (no harvests or protocol grade mix yet)"


def _demo():
	"""Self-check of the arithmetic, no database: 2 beds, 10 plants each."""
	rule = "iso"
	d = datetime.date(2026, 10, 5)  # a Monday
	s = lambda counts: frappe._dict(sampling_date=d, plants_counted=10,
		stages=[frappe._dict(count=c, days_to_harvest=14) for c in counts])
	pred = _predict([s([10]), s([20])], 1000, rule)  # 1 and 2 buds/plant -> 1000 and 2000 stems
	total = sum(m for m, _ in pred.values())
	assert abs(total - 1500) < 1e-6, total  # the mean of the two beds
	se = math.sqrt(sum(e ** 2 for _, e in pred.values()))
	assert 0 < se < 1500, se
	assert _round([s([1])], d - datetime.timedelta(days=1)) == []
	print("bed_forecast demo OK", {k: round(m) for k, (m, _) in sorted(pred.items())})
