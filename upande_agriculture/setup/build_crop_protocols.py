# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

"""Build a Crop Protocol for every variety grown on this site.

Timeline and density come from the grower workbook (see forecasting_cycles).
The stem length distribution is measured from this site's own Harvest records,
falling back to a pooled crop-class average for varieties with too little
history -- every distribution row records which of the two it is, so a borrowed
figure is never mistaken for a measured one.

	bench --site <site> execute upande_agriculture.setup.build_crop_protocols.report
	bench --site <site> execute upande_agriculture.setup.build_crop_protocols.build
"""

import re

import frappe

from upande_agriculture.setup import forecasting_cycles as fc

# A variety needs at least this many harvested stems before its own grade mix is
# trusted over the crop-class average. Below it, one odd week would skew the
# whole protocol.
MIN_SAMPLE_STEMS = 2000

# Harvest.stem_length is dirty -- some rows carry a bare number, some the padded
# name. Fold both onto the Stem Length record.
LENGTH_ALIAS = {"57": "57cm", "62": "62cm", "72": "72cm", "82": "82cm", "42": "42cm", "92": "92cm"}

VARIETY_SOURCES = [
	("Bed Range", "select distinct variety from `tabBed Range` where variety is not null and variety <> ''"),
	("Crop Cycle Bed", "select distinct variety from `tabCrop Cycle Bed` where variety is not null and variety <> ''"),
	("Crop Cycle Variety", "select distinct variety from `tabCrop Cycle Variety` where variety is not null and variety <> ''"),
	("Production Projection", "select distinct crop_variety from `tabProduction Projection` where crop_variety is not null and crop_variety <> ''"),
	("Harvest", "select distinct item_code from tabHarvest where item_code is not null and item_code <> ''"),
]

# The workbook's "red-4" note. Judged on the Item's recorded colour, not on the
# variety name -- names catch cerises that are not red ("Bellalinda Cerise") and
# miss reds that are not named for it ("Dominica", "Furiosa", "Upper Class").
# Bicolours ("Red Bi", "Orange Red Bi") are left on the normal life.
RED_COLOUR = "RED"

# Swatches for the colour names the Items carry. Bi-colours take the hue of the
# first colour named. Cosmetic only -- the grower-meaningful value is the name.
COLOUR_SWATCH = {
	"PINK": "#f472b6",
	"WHITE": "#f8fafc",
	"RED": "#dc2626",
	"CERISE": "#be185d",
	"ORANGE": "#f97316",
	"YELLOW": "#facc15",
	"PURPLE": "#9333ea",
	"PEACH": "#fdba74",
	"CORAL": "#fb7185",
	"GREEN": "#22c55e",
	"LILAC": "#c4b5fd",
	"BLUE": "#3b82f6",
	"CREAM": "#fef3c7",
}
DEFAULT_SWATCH = "#94a3b8"


# --------------------------------------------------------------------- helpers
def _canonical_length(raw):
	value = (raw or "").strip()
	if not value:
		return None
	return LENGTH_ALIAS.get(value, value)


def grown_varieties():
	"""{variety: [source, ...]} for everything planted, projected or harvested."""
	found = {}
	for label, query in VARIETY_SOURCES:
		for row in frappe.db.sql(query):
			name = (row[0] or "").strip()
			if not name:
				continue
			found.setdefault(name, [])
			if label not in found[name]:
				found[name].append(label)
	return found


def ensure_colours():
	"""Crop Protocol fetches its colour from the Item, but the Color doctype is
	empty on this site, so every fetch fails link validation. Seed it from the
	colours the Items actually carry."""
	rows = frappe.db.sql(
		"select distinct custom_color from tabItem where custom_color is not null and custom_color <> ''"
	)
	made = 0
	for row in rows:
		colour = (row[0] or "").strip()
		if not colour or frappe.db.exists("Color", colour):
			continue
		doc = frappe.new_doc("Color")
		# Color is prompt-named, so the name carries the value.
		doc.name = colour
		doc.color = COLOUR_SWATCH.get(colour.split()[0].upper(), DEFAULT_SWATCH)
		doc.flags.ignore_permissions = True
		doc.insert()
		made += 1
	if made:
		frappe.db.commit()
	print("colours created: %d" % made)
	return made


def crop_type_for(variety):
	"""The variety's Item Group -- the site's own classification.

	Crop Protocol.crop_type is a Link to Item Group here, and every variety
	grown on this site already sits in one of Standard Roses, Spray Roses,
	Summer Flowers or Chrysanthemums. That beats matching names against the
	workbook, which covers only part of the estate.
	"""
	return frappe.db.get_value("Item", variety, "item_group")


def group_for(variety):
	mapped = fc.variety_group_map().get(fc.normalise(variety))
	return mapped[1] if mapped else None


def life_expectancy_for(variety, crop_type):
	"""Reds come out after 4 years, everything else after 6."""
	base = fc.CLASS_PARAMS.get(crop_type, {}).get("life_expectancy_years", 0.0)
	if not base:
		return base
	colour = (frappe.db.get_value("Item", variety, "custom_color") or "").strip().upper()
	if colour == RED_COLOUR:
		return fc.RED_LIFE_EXPECTANCY_YEARS
	return base


# ---------------------------------------------------------- length distribution
def harvest_grade_mix():
	"""{variety: {stem_length: stems}} from this site's Harvest records."""
	mix = {}
	rows = frappe.db.sql(
		"""
		select item_code, stem_length, sum(quantity) as stems
		from tabHarvest
		where item_code is not null and item_code <> ''
		group by item_code, stem_length
		""",
		as_dict=True,
	)
	for row in rows:
		length = _canonical_length(row.stem_length)
		if not length:
			continue
		bucket = mix.setdefault(row.item_code, {})
		bucket[length] = bucket.get(length, 0) + float(row.stems or 0)
	return mix


def pooled_defaults(mix, classifier):
	"""Crop-class and site-wide grade mixes, pooled from varieties with enough
	history. Used for varieties that cannot speak for themselves yet."""
	by_class = {}
	overall = {}
	for variety, grades in mix.items():
		if sum(grades.values()) < MIN_SAMPLE_STEMS:
			continue
		crop_type = classifier(variety)
		target = by_class.setdefault(crop_type, {}) if crop_type else None
		for length, stems in grades.items():
			overall[length] = overall.get(length, 0) + stems
			if target is not None:
				target[length] = target.get(length, 0) + stems
	return by_class, overall


def as_percentages(grades):
	"""Grade counts -> rows of {stem_length, percentage, sample_stems} summing to
	exactly 100.0, the rounding residual landing on the largest grade."""
	total = sum(grades.values())
	if not total:
		return []
	rows = []
	for length, stems in grades.items():
		rows.append({"stem_length": length, "percentage": round(100.0 * stems / total, 1), "sample_stems": int(stems)})
	rows.sort(key=lambda r: -r["percentage"])
	residual = round(100.0 - sum(r["percentage"] for r in rows), 1)
	if rows and residual:
		rows[0]["percentage"] = round(rows[0]["percentage"] + residual, 1)
	rows.sort(key=lambda r: _length_cm(r["stem_length"]))
	return rows


def _length_cm(stem_length):
	match = re.search(r"\d+", stem_length or "")
	return int(match.group()) if match else 0


def distribution_for(variety, crop_type, mix, by_class, overall):
	"""Rows for the length_distribution table, plus the basis they rest on."""
	own = mix.get(variety) or {}
	if sum(own.values()) >= MIN_SAMPLE_STEMS:
		rows = as_percentages(own)
		for row in rows:
			row["basis"] = "Actual"
		return rows
	fallback = by_class.get(crop_type) or overall
	rows = as_percentages(dict(fallback))
	for row in rows:
		row["basis"] = "Group Default"
		row["sample_stems"] = 0
	return rows


# ------------------------------------------------------------------- the build
def _plan():
	"""Everything needed to write the protocols, computed without touching data."""
	varieties = grown_varieties()
	mix = harvest_grade_mix()
	by_class, overall = pooled_defaults(mix, crop_type_for)
	conflicts = fc.conflicted_varieties()

	known_lengths = set(frappe.get_all("Stem Length", pluck="name"))
	items = set(frappe.get_all("Item", pluck="name"))

	plan = []
	for variety in sorted(varieties):
		crop_type = crop_type_for(variety)
		group = group_for(variety)
		params = fc.CLASS_PARAMS.get(crop_type, {})
		rows = distribution_for(variety, crop_type, mix, by_class, overall)
		rows = [r for r in rows if r["stem_length"] in known_lengths]
		life = life_expectancy_for(variety, crop_type)
		weeks_in_ground = int(round((life or 0) * 52))
		stages = fc.stages_for(crop_type, group) if group else []
		flushes = fc.flush_schedule(crop_type, stages, weeks_in_ground)
		plan.append(
			{
				"variety": variety,
				"is_item": variety in items,
				"sources": varieties[variety],
				"crop_type": crop_type,
				"protocol_group": group,
				"conflict": fc.normalise(variety) in conflicts,
				"harvested_stems": int(sum((mix.get(variety) or {}).values())),
				"params": params,
				"life_expectancy_years": life,
				"total_weeks_in_ground": weeks_in_ground,
				"stages": stages,
				"flush_schedule": flushes,
				"flush_interval": fc.flush_interval_weeks(stages),
				"weeks_to_first_harvest": fc.weeks_to_first_harvest(crop_type),
				"distribution": rows,
			}
		)
	return plan


def report():
	"""Dry run: print what build() would do, change nothing."""
	plan = _plan()
	print("varieties grown on this site: %d" % len(plan))
	print("  not an Item (protocol cannot be created): %d" % len([p for p in plan if not p["is_item"]]))
	print("  with a workbook group: %d" % len([p for p in plan if p["protocol_group"]]))
	print("  with a crop type but no group: %d" % len([p for p in plan if p["crop_type"] and not p["protocol_group"]]))
	print("  crop type unknown: %d" % len([p for p in plan if not p["crop_type"]]))
	print("  length distribution measured (Actual): %d" % len([p for p in plan if p["distribution"] and p["distribution"][0]["basis"] == "Actual"]))
	print("  length distribution borrowed (Group Default): %d" % len([p for p in plan if p["distribution"] and p["distribution"][0]["basis"] == "Group Default"]))
	print("  no length distribution at all: %d" % len([p for p in plan if not p["distribution"]]))
	print()
	for p in plan:
		mark = "" if p["is_item"] else "  [NO ITEM]"
		conflict = "  [GROUP CONFLICT]" if p["conflict"] else ""
		basis = p["distribution"][0]["basis"] if p["distribution"] else "none"
		dist = " ".join("%s:%s%%" % (r["stem_length"], r["percentage"]) for r in p["distribution"])
		print(
			"%-24s %-15s %-8s stems=%-9d %-14s %s%s%s"
			% (
				p["variety"],
				p["crop_type"] or "-",
				p["protocol_group"] or "-",
				p["harvested_stems"],
				basis,
				dist,
				mark,
				conflict,
			)
		)
	return plan


def build(overwrite_manual=False):
	"""Create or refresh a Crop Protocol per variety.

	Rows a grower has hand-edited (basis "Manual") are left alone unless
	overwrite_manual is set, so a re-run never silently discards their work.
	"""
	ensure_colours()
	plan = _plan()
	created = updated = skipped = 0

	for p in plan:
		if not p["is_item"]:
			skipped += 1
			continue

		name = p["variety"]
		if frappe.db.exists("Crop Protocol", name):
			doc = frappe.get_doc("Crop Protocol", name)
			was_manual = any(r.basis == "Manual" for r in doc.length_distribution)
			updated += 1
		else:
			doc = frappe.new_doc("Crop Protocol")
			doc.variety = name
			was_manual = False
			created += 1

		# variety_item and crop_type are mandatory Custom Fields on this site,
		# carried over from the Kaitet schema; variety_item duplicates variety.
		doc.variety_item = name
		doc.crop_type = p["crop_type"]
		doc.protocol_group = p["protocol_group"]
		doc.plants_per_sqm = p["params"].get("plants_per_sqm") or 0
		doc.weeks_to_bending = p["params"].get("weeks_to_bending") or 0
		# The workbook gives weeks to bending, not to pinching -- they are
		# different operations, so weeks_to_pinch is left for the grower.
		doc.weeks_pinch_to_first_harvest = p["params"].get("weeks_pinch_to_first_harvest") or 0
		doc.life_expectancy_years = p["life_expectancy_years"]
		doc.total_weeks_in_ground = p["total_weeks_in_ground"]
		doc.rolling_forecast_weeks = fc.ROLLING_FORECAST_WEEKS
		doc.spacing_plant_cm = fc.SPACING_PLANT_CM
		doc.spacing_row_cm = fc.SPACING_ROW_CM
		doc.weeks_between_flushes = int(round(p["flush_interval"] or 0))

		if p["stages"]:
			doc.set("growth_stages", [])
			for stage in p["stages"]:
				doc.append("growth_stages", stage)

		if p["flush_schedule"]:
			doc.set("flush_schedule", [])
			for flush in p["flush_schedule"]:
				doc.append("flush_schedule", flush)

		if p["distribution"] and not (was_manual and not overwrite_manual):
			doc.set("length_distribution", [])
			for row in p["distribution"]:
				doc.append("length_distribution", row)

		doc.flags.ignore_permissions = True
		doc.save()

	frappe.db.commit()
	print("created %d, updated %d, skipped %d (no Item)" % (created, updated, skipped))
	return {"created": created, "updated": updated, "skipped": skipped}
