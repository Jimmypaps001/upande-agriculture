# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

"""Forecasting cycle definitions for roses and summer flowers.

Transcribed from the grower workbook "Forecasting Cycles Roses & Summer
Flowers.xlsx" (sheets: Std Roses, Spray Roses, Summer Flowers).

Growth-stage day windows are CUMULATIVE FROM THE CUT, not stage durations --
"Ball to Colour Break 36-42" means a bed sits in that stage between day 36 and
day 42 after the cut. They must never be summed.

The Std Roses sheet carries a weeks column alongside the day windows; the Spray
Roses sheet does not, so spray weeks are derived as days_to / 7 and flagged by
WEEKS_DERIVED so nobody mistakes them for grower-supplied figures.
"""

# --------------------------------------------------------------- shared params
# Std Roses sheet, F23:H32 -- one block governing both rose classes.
CLASS_PARAMS = {
	"Standard Roses": {
		"plants_per_sqm": 8,
		"weeks_to_bending": 8,
		"weeks_pinch_to_first_harvest": 7,
		"life_expectancy_years": 6.0,
	},
	"Spray Roses": {
		"plants_per_sqm": 9,
		"weeks_to_bending": 8,
		"weeks_pinch_to_first_harvest": 8,
		"life_expectancy_years": 6.0,
	},
}
# Item Groups outside these keys (Summer Flowers, Chrysanthemums) get no timeline
# or density: the workbook says nothing about them and nothing is invented. They
# still get a length distribution from harvest history.

ROLLING_FORECAST_WEEKS = 1.5
SPACING_PLANT_CM = 15.0
SPACING_ROW_CM = 30.0

# "red-4" on the sheet: reds are pulled after 4 years, not the usual 6.
RED_LIFE_EXPECTANCY_YEARS = 4.0

WEEKS_DERIVED = "Spray Roses"

# ------------------------------------------------------------- growth stages
# (stage_name, days_from, days_to, weeks or None to derive)
STAGE_ORDER = [
	"Cut to Rice",
	"Rice to Ball",
	"Ball to Colour Break",
	"Colour Break to Full Colour",
	"Cut to Harvest",
]

GROUPS = {
	"Standard Roses": {
		"Group 1": [
			("Cut to Harvest", 48, 52, 7.0),
			("Cut to Rice", 0, 28, 4.0),
			("Rice to Ball", 29, 35, 5.0),
			("Ball to Colour Break", 36, 42, 5.5),
			("Colour Break to Full Colour", 43, 50, 7.0),
		],
		"Group 2": [
			("Cut to Harvest", 53, 57, 7.6),
			("Cut to Rice", 0, 28, 4.0),
			("Rice to Ball", 29, 35, 4.5),
			("Ball to Colour Break", 36, 48, 6.0),
			("Colour Break to Full Colour", 49, 56, 8.0),
		],
		"Group 3": [
			("Cut to Harvest", 58, 63, 9.0),
			("Cut to Rice", 0, 35, 6.0),
			("Rice to Ball", 36, 45, 6.5),
			("Ball to Colour Break", 46, 55, 7.0),
			("Colour Break to Full Colour", 56, 62, 8.4),
		],
	},
	"Spray Roses": {
		"Group 1": [
			("Cut to Harvest", 52, 56, None),
			("Cut to Rice", 0, 28, None),
			("Rice to Ball", 29, 35, None),
			("Ball to Colour Break", 36, 48, None),
			("Colour Break to Full Colour", 49, 56, None),
		],
		"Group 2": [
			("Cut to Harvest", 57, 60, None),
			("Cut to Rice", 0, 35, None),
			("Rice to Ball", 36, 45, None),
			("Ball to Colour Break", 46, 55, None),
			("Colour Break to Full Colour", 56, 62, None),
		],
		"Group 3": [
			("Cut to Harvest", 61, 65, None),
			("Cut to Rice", 0, 35, None),
			("Rice to Ball", 36, 49, None),
			("Ball to Colour Break", 50, 58, None),
			("Colour Break to Full Colour", 59, 64, None),
		],
		"Group 4": [
			("Cut to Harvest", 66, 72, None),
			("Cut to Rice", 0, 35, None),
			("Rice to Ball", 36, 49, None),
			("Ball to Colour Break", 50, 60, None),
			("Colour Break to Full Colour", 60, 70, None),
		],
	},
}

# --------------------------------------------------------- variety -> group
# Straight from the sheets' Variety Listing columns. Keys are normalised by
# normalise() below, so workbook spellings (TAPDANCE, COBACABANA, YELLOW WEEN)
# resolve onto the Item names in the database.
STANDARD_GROUPS = {
	"Group 1": ["CORAL SPRING", "AQUA", "MOONWALK", "TROPICAL AMAZON", "NIGHTINGALE", "MANDARINE"],
	"Group 2": [
		"SILANTOI", "FUCHSIANA", "SUMMER FIELD", "RED CALYPSO", "FURIOSA", "UPPER CLASS",
		"TAPDANCE", "SMOOTHIE", "COBACABANA", "MADAM RED", "DIVALICOUS", "SNOWSTORM", "PINK FLAME",
	],
	"Group 3": [
		"CONFIDENTIAL", "ESPANA", "THRILLER", "PINK ICE", "YELLOW WEEN", "PRICILLA",
		"HIGH & MAGIC", "WHAM", "MADAM CERISE",
	],
}

SPRAY_GROUPS = {
	"Group 1": ["SWEET SARAH", "ALICIA", "SNOWFLAKE"],
	"Group 2": [
		"SNOW FLAKE", "MIRABEL", "WEDDING INVITE", "LAND OF FIRE", "ALICIA", "DINARA", "MARISA",
		"ODILIA", "REFLEX", "NAMASKAR", "SNOWDRIFT", "JOLENE", "BIRD NEST", "DOMINICA", "BARBADOS",
		"ROSANELLA", "LEILA", "EYE LINER", "ROYAL PORCELINA", "SNOW BUBBLES", "SILVER SHADOW",
	],
	"Group 3": [
		"MADAM BOMBASTIC", "BOMBASTIC", "SILVER PINK", "RADIANT REBECCA", "DIMA BOMBASTIC",
		"DARLENE", "FEMKE", "AMAZING MAGIC", "JULIETA", "SWEET HARPER", "LOVELY HARPER", "HARPER",
		"LADY ELLA", "BRITNEY", "CAMELIANA", "FANCY BLOSSOMS", "ARIYA", "SANCERRE", "IN LOVE",
		"SALINERO", "HOLY", "CHEYENNE", "RED TRENDSETTER", "FAIR FLOW", "MISTY BUBBLES", "LYRICA",
		"SOFIE", "FIREWORKS", "SMASHING", "CLASSIC SENSATION", "GREEN GLOW", "NIKITA", "BANDOLERO",
		"PINK DIMENSION", "JULIETTA CERISE", "AZORE",
	],
	"Group 4": [
		"SUMMER ROSE", "GISELLE", "SWEET GISSEI", "GOOD MOOD", "BELLALINDA CERISE",
		"BELLALINDA MOSTAZA",
	],
}

# Varieties the workbook lists in two groups with different day windows. Until
# the grower rules, the earlier (faster) group wins and the protocol is tagged
# so the conflict stays visible instead of being silently resolved.
GROUP_CONFLICTS = {
	"Spray Roses": {
		"ALICIA": ("Group 1", "Group 2"),
		"SNOWFLAKE": ("Group 1", "Group 2"),
		"DINARA": ("Group 2", "Group 3"),
		"LAND OF FIRE": ("Group 2", "Group 3"),
	}
}

# Workbook spellings that differ from the Item name in the database.
SPELLING = {
	"MANDARINE": "MANDARIN",
	"COBACABANA": "COPACABANA",
	"YELLOWWEEN": "YELLOWEEN",
	"TAPDANCE": "TAPDANCE",
	"ROYALPORCELINA": "ROYALPORCELLINA",
	"HOLY": "HOLLY",
	"SWEETGISSEI": "SWEETGISELLE",
	"JULIETTACERISE": "JULIETACERISE",
	"SNOWFLAKE": "SNOWFLAKE",
	"BIRDNEST": "BIRDNEST",
}

# ------------------------------------------------------------ summer flowers
# Sheet 3, transcribed but NOT YET APPLIED: karenroses grows Enchante, Green
# Dragon and Xlence under Summer Flowers, none of which is one of these crops.
# Kept so the figures are in the repo for when they are planted.
#
# These crops are forecast by spreading one planting across several harvest
# weeks, so each carries a week -> ratio distribution rather than the roses' day
# windows. Crop Protocol has nowhere to store that yet -- it needs a harvest
# week distribution child table, which is a separate piece of work.
SUMMER_FLOWERS = {
	"Lepidium": {
		"stages": [("Planting to Harvest", None, None, 10.0)],
		"week_distribution": [],
	},
	"Gypsophila": {
		"stages": [("Planting to Harvest and Cutback to Harvest", None, None, 12.0)],
		"week_distribution": [(12, 0.10), (13, 0.40), (14, 0.35), (15, 0.10), (16, 0.05)],
	},
	"Limonium": {
		"stages": [
			("Planting to Harvest (First)", None, None, 16.0),
			("Shoots Regrow to Harvest", None, None, 14.0),
			("Shoots Regrow to Floret Formation", None, None, 10.0),
			("Floret Formation to Full Colour at 70%", None, None, 4.0),
		],
		# NOTE: the sheet's ratios total 1.1, not 1.0 -- left exactly as written
		# so the grower can correct the source rather than inherit a silent fudge.
		"week_distribution": [(16, 0.20), (17, 0.60), (18, 0.20), (19, 0.10)],
	},
}


def flush_interval_weeks(stages):
	"""Weeks between flushes = the Cut to Harvest span.

	The workbook states this outright: against "weeks between flush" it writes
	"growth stages". A flush is one cut-to-harvest cycle, so the interval is that
	stage's span and nothing needs to be assumed.
	"""
	for stage in stages:
		if stage["stage_name"] == "Cut to Harvest":
			if stage.get("weeks"):
				return float(stage["weeks"])
			if stage.get("days_to"):
				return round(stage["days_to"] / 7.0, 1)
	return 0.0


def weeks_to_first_harvest(crop_type):
	"""Planting -> first cut: bending, then bending to first harvest."""
	params = CLASS_PARAMS.get(crop_type)
	if not params:
		return 0
	return int(params["weeks_to_bending"]) + int(params["weeks_pinch_to_first_harvest"])


def flush_schedule(crop_type, stages, total_weeks_in_ground):
	"""Deduce every flush across the plant's life.

	Flush 1 lands at planting + weeks_to_first_harvest; each later flush one
	cut-to-harvest cycle after the last, until the plant is pulled.

	Crop Protocol Flush stores weeks_after_previous as an Int, but the interval
	is fractional (Spray Group 4 runs 10.3 weeks). Gaps are therefore taken as
	the difference between *rounded cumulative* weeks rather than a rounded
	constant, so the schedule does not drift by a week every few flushes. The
	exact interval stays visible on the growth stage's Weeks field.
	"""
	interval = flush_interval_weeks(stages)
	first = weeks_to_first_harvest(crop_type)
	if not (interval and first and total_weeks_in_ground) or first > total_weeks_in_ground:
		return []

	rows = []
	exact = float(first)
	previous_rounded = 0
	number = 1
	while exact <= total_weeks_in_ground:
		rounded = int(round(exact))
		rows.append(
			{
				"flush_number": number,
				"weeks_after_previous": rounded - previous_rounded,
				"stems_per_plant": 0,
			}
		)
		previous_rounded = rounded
		exact += interval
		number += 1
	return rows


def normalise(name):
	"""Fold a variety name for matching: upper, alphanumeric only, & -> AND."""
	folded = "".join(ch for ch in (name or "").upper() if ch.isalnum())
	return SPELLING.get(folded, folded)


def variety_group_map():
	"""{normalised variety: (crop_type, group)} from both rose sheets.

	Where a variety appears twice, the lower-numbered group wins (see
	GROUP_CONFLICTS).
	"""
	out = {}
	for crop_type, groups in (("Standard Roses", STANDARD_GROUPS), ("Spray Roses", SPRAY_GROUPS)):
		for group in sorted(groups):
			for variety in groups[group]:
				key = normalise(variety)
				if key not in out:
					out[key] = (crop_type, group)
	return out


def conflicted_varieties():
	out = set()
	for varieties in GROUP_CONFLICTS.values():
		for variety in varieties:
			out.add(normalise(variety))
	return out


def stages_for(crop_type, group):
	"""Growth-stage rows for a group, ordered from planting towards harvest."""
	raw = GROUPS.get(crop_type, {}).get(group)
	if not raw:
		return []
	rows = []
	for stage_name, days_from, days_to, weeks in raw:
		if weeks is None and days_to:
			weeks = round(days_to / 7.0, 1)
		rows.append(
			{
				"stage_name": stage_name,
				"days_from": days_from,
				"days_to": days_to,
				"days_to_harvest": days_to,
				"weeks": weeks,
				"stage_order": STAGE_ORDER.index(stage_name) + 1
				if stage_name in STAGE_ORDER
				else 99,
			}
		)
	rows.sort(key=lambda r: r["stage_order"])
	return rows
