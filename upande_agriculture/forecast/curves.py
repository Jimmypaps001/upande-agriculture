"""Stage timing maths for the harvest forecast. Pure Python: no Frappe.

A bud counted at a stage is cut, on average, `days` later, about uniformly within +-`spread` days (a size band), blurred a little, if it survives.
Sources: Monroy, Pérez & Cure (2003) via La Salle (2008), days to harvest
by stage and temperature; Rodríguez & Flórez (2006), base temperature 5.3 °C.
"""
import math

BASE_TEMP = 5.3  # °C: roses do not develop below this

# Days to harvest at ~18 °C (pea and chickpea interpolated) and the share of
# buds at that stage that reach the knife. Each variety's Crop Protocol
# overrides these.
DEFAULT_STAGES = [
	{"stage_name": "Rice", "days_to_harvest": 28, "survival": 0.85},
	{"stage_name": "Pea", "days_to_harvest": 22, "survival": 0.88},
	{"stage_name": "Chickpea", "days_to_harvest": 15, "survival": 0.92},
	{"stage_name": "Showing colour", "days_to_harvest": 8, "survival": 0.97},
	# Petals loosening, a few days from the knife. Without it the latest count
	# would miss the buds due in the next ~5 days: they are past colour stage.
	{"stage_name": "Opening", "days_to_harvest": 3, "survival": 0.99},
]
SPREAD_FRACTION, MIN_SPREAD = 0.2, 1.5
BLUR_FRACTION, MIN_BLUR = 0.1, 0.5
DEFAULT_SURVIVAL = 0.9

# Error bands are kept per horizon bucket (days ahead of the forecast date).
BUCKETS = [(0, 2, "0-2"), (3, 6, "3-6"), (7, 13, "7-13"), (14, 20, "14-20")]


def stage_params(rows):
	"""{stage: (band centre, half width, survival)} from protocol rows, survival
	as a fraction. With a blank spread the band runs between the midpoints to the
	neighbouring stages (by days), so the centre may differ from the days. Blank cells take the default for a stage of that name; a stage
	with no days anywhere cannot be placed in time and is dropped."""
	defaults = {s["stage_name"].lower(): s for s in DEFAULT_STAGES}
	placed = []  # (name, days, row, default) for stages that can be placed in time
	for r in rows or DEFAULT_STAGES:
		d = defaults.get(r["stage_name"].lower(), {})
		days = r.get("days_to_harvest")
		if days in (None, ""):
			days = d.get("days_to_harvest")
		if days not in (None, ""):
			placed.append((r["stage_name"], float(days), r, d))
	out = {}
	order = sorted(p[1] for p in placed)
	for name, days, r, d in placed:
		i = order.index(days)
		below, above = order[i - 1] if i else None, order[i + 1] if i + 1 < len(order) else None
		if below is None and above is None:
			lo, hi = days - SPREAD_FRACTION * days, days + SPREAD_FRACTION * days
		else:
			lo = (days + below) / 2 if below is not None else None
			hi = (days + above) / 2 if above is not None else None
			lo = days - (hi - days) if lo is None else lo
			hi = days + (days - lo) if hi is None else hi
		explicit = r.get("spread_days")
		if explicit:
			centre, half = days, float(explicit)
		else:
			centre, half = (lo + hi) / 2, (hi - lo) / 2
		centre, spread = round(centre, 10), max(MIN_SPREAD, round(half, 10))
		survival = r.get("survival")
		if survival in (None, ""):
			survival = d.get("survival", DEFAULT_SURVIVAL)
		out[name] = (centre, spread, float(survival))
	return out


def day_mass(offset, mean, sd):
	"""Share of a stage's buds cut on whole day `offset` after the count."""
	if sd <= 0:
		return 1.0 if round(mean) == offset else 0.0
	cdf = lambda x: 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))
	return cdf(offset + 0.5) - cdf(offset - 0.5)


def stage_mass(offset, mean, half_width, blur=None, floor_at_zero=True):
	"""Share of a stage's buds cut on day `offset`: uniform over mean +- half_width, blurred by a normal (sd = blur, default max(MIN_BLUR, BLUR_FRACTION*mean))."""
	blur = max(MIN_BLUR, BLUR_FRACTION * mean) if blur is None else blur
	centres = [mean + half_width * (2 * i / 8 - 1) for i in range(9)]
	if floor_at_zero and offset < 0:  # a counted bud cannot be cut before the count
		return 0.0
	if floor_at_zero and offset == 0:  # ... so what would land earlier lands on day 0
		return sum(0.5 * (1 + math.erf((0.5 - c) / (blur * math.sqrt(2)))) for c in centres) / 9
	return sum(day_mass(offset, c, blur) for c in centres) / 9


def temperature_factor(ref_temp, recent_temp):
	"""Multiplier on every timing: warmer than the calibration period -> < 1 (faster)."""
	if not ref_temp or not recent_temp:
		return 1.0
	a, b = ref_temp - BASE_TEMP, recent_temp - BASE_TEMP
	if a <= 0 or b <= 0:
		return 1.0
	return a / b


def bucket(h):
	for lo, hi, label in BUCKETS:
		if lo <= h <= hi:
			return label
	return BUCKETS[-1][2]


def default_band(h):
	"""Before calibration: ±15%, widening 1 point per day ahead."""
	w = 0.15 + 0.01 * h
	return (round(max(0.0, 1 - w), 6), round(1 + w, 6))


def band(h, bands):
	"""(low, high) multipliers on the forecast for `h` days ahead."""
	b = (bands or {}).get(bucket(h))
	return (b[0], b[1]) if b else default_band(h)


def quantile(values, q):
	xs = sorted(values)
	if not xs:
		return None
	pos = (len(xs) - 1) * q
	lo = int(pos)
	hi = min(lo + 1, len(xs) - 1)
	return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
