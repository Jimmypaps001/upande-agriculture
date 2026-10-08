# Bilashaka Harvest Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daily, per-section (bay) harvest forecast for 21 days ahead, built from repeated counts of permanent sample plots and from regrowth of stems already cut. It calibrates itself weekly against actual picks, and feeds a phone "your bay" card, a daily per-section report and a weekly per-variety sales report.

**Architecture:** The new package `upande_agriculture/forecast/` splits pure maths (`curves.py`, `engine.py`, testable without Frappe) from database reads (`data.py`), composition (`service.py`), fitting (`calibrate.py`), scheduled jobs (`jobs.py`), endpoints (`api.py`) and the photo dataset export (`dataset.py`). Four new doctypes hold plots, temperatures, per-variety calibration and daily snapshots. The bilashaka-harvest Expo app gets a Plot Count screen and a Bay Forecast card. upande_harvest's `createHarvestEntry` starts stamping the section on harvest stock entries, which is the "actual picks per bay" source.

**Tech Stack:** Frappe/ERPNext v16 (Python 3.14, `FrappeTestCase`, Script Reports, scheduler hooks), React Native / Expo SDK (TypeScript), EAS Update.

**Spec:** `docs/superpowers/specs/2026-10-08-bilashaka-harvest-forecast.md`

## Global Constraints
- Mona never changes. Commit only to branch `bilashaka` of upande_agriculture (remote `origin`) and of upande_harvest (remote `upstream`). The app lives at `/home/teddy5456/bilashaka-harvest` (no git history).
- Never commit `upande_agriculture/upande_agriculture/doctype/production_plan_form/production_plan_form.json`, `production_plan_task.json` or `upande_agriculture/coffee_plan.py` (someone else's uncommitted work). In upande_harvest, never commit `upande_harvest/api.py` hunks containing `_cs_locked` / `cs_lock`, `fixtures/print_format.json`, or the Control Sheet lock hunks (Teddy's uncommitted Mona fixes). Stage only your own hunks.
- No new Python dependencies. Use `math`; `numpy` is allowed but not needed.
- Commit messages end with `Co-Authored-By: claude-flow <ruv@ruv.net>`.
- Test site: `bhcloud.local` (Frappe Cloud's app set plus upande_agriculture). Never run tests or migrate on `mona.local` / `mona2.local`.
- Database tests: `cd /home/teddy5456/frappe-bench && bench --site bhcloud.local run-tests --app upande_agriculture --module <module>`. Run `bench --site bhcloud.local set-config allow_tests true` once.
- Pure tests: `cd /home/teddy5456/frappe-bench/apps/upande_agriculture && ../../env/bin/python -m unittest <module> -v`.
- Default stages at ~18 °C: Rice 28 d / 85%, Pea 22 d / 88%, Chickpea 15 d / 92%, Showing colour 8 d / 97%, Opening 3 d / 99%. Spread = 20% of the days (minimum 1.5). Base temperature 5.3 °C. Opening (petals loosening, 1–5 days from the knife) exists so the latest count covers the next few days: without it, buds due in under ~5 days fall between stages.
- Forecast horizon 21 days. Count interval 3 days. A round = counts within 3 days of the section's latest count. Counts older than 35 days are ignored. Calibration window 70 days; it needs ≥3 count rounds and ≥200 picked stems.
- App OTA: `NODE_OPTIONS="--dns-result-order=ipv4first --network-family-autoselection-attempt-timeout=10000" npx eas update --channel preview --message "<msg>" --non-interactive` (channel `preview`, runtime 1.1.0).

## Review Focus
1. **Section with no `custom_plants` set:** the forecast must still return regrowth-only numbers flagged `no_plants`, never crash or silently show 0 for counted buds. Pinned in Task 6.
2. **Section never counted, or last count older than 35 days:** regrowth-only, flagged `no_count`. A count older than 7 days is flagged `stale_count`. Pinned in Task 6.
3. **The same plot count replayed by the offline queue** (same `client_uuid`): one Bed Sample, not two. Pinned in Task 9.
4. **Stage names in a count that the protocol no longer lists** (protocol edited later): counts with a known default stage use its defaults; unknown names without days are ignored, not crashed on. Pinned in Task 2 (`stage_params`) and Task 5 (`latest_round` keeps every counted stage).
5. **Calibration with too little data:** keeps the defaults, and `status` explains how many rounds and stems it saw. Pinned in Task 7.

---

## File Structure

| File | Responsibility |
|---|---|
| `upande_agriculture/upande_agriculture/doctype/sample_plot/*` | Permanent plot (greenhouse, section, plot no, bed, plants) |
| `upande_agriculture/upande_agriculture/doctype/greenhouse_temperature/*` | Daily mean temperature per greenhouse |
| `upande_agriculture/upande_agriculture/doctype/harvest_forecast_calibration/*` | Per-variety fitted parameters and error bands |
| `upande_agriculture/upande_agriculture/doctype/harvest_forecast_snapshot/*` | One forecast day per section per made-on day (audit and accuracy) |
| `upande_agriculture/upande_agriculture/doctype/bed_sample/*` | (modify) A count can belong to a Sample Plot |
| `upande_agriculture/upande_agriculture/doctype/crop_protocol_growth_stage/*` | (modify) `spread_days`, `survival` |
| `upande_agriculture/upande_agriculture/custom/warehouse_section.json` | `custom_plants` on upande_core's Warehouse Section |
| `upande_agriculture/forecast/curves.py` | Pure: stage defaults, daily probability mass, temperature factor, error bands |
| `upande_agriculture/forecast/engine.py` | Pure: bud curve, regrowth curve, section forecast |
| `upande_agriculture/forecast/data.py` | Database reads: sections, stages, latest round, cuts, temperatures, params, length mix |
| `upande_agriculture/forecast/service.py` | `forecast_section`, `forecast_all` (data → engine → bands and flags) |
| `upande_agriculture/forecast/calibrate.py` | Weekly per-variety fit |
| `upande_agriculture/forecast/jobs.py` | Daily snapshot and weekly calibration (scheduler) |
| `upande_agriculture/forecast/api.py` | Phone endpoints: plot plan, submit count, bay forecast, recalibrate |
| `upande_agriculture/forecast/dataset.py` | Photo + stage-count dataset export (zip) |
| `upande_agriculture/upande_agriculture/report/section_harvest_forecast/*` | Daily per-section report |
| `upande_agriculture/upande_agriculture/report/weekly_harvest_forecast/*` | Weekly per-variety/length report (replaces `bed_sample_forecast`) |
| `upande_agriculture/upande_agriculture/report/harvest_forecast_accuracy/*` | Snapshots vs actual picks, by horizon |
| `upande_agriculture/tests/forecast_fixtures.py` | Shared DB fixtures for forecast tests |
| `upande_agriculture/tests/test_forecast_*.py` | Tests |
| Delete: `upande_agriculture/bed_sampling.py`, `upande_agriculture/bed_forecast.py`, `upande_agriculture/_check_bed_sampling.py`, `upande_agriculture/upande_agriculture/report/bed_sample_forecast/` | Replaced by the above |
| upande_harvest `upande_harvest/fixtures/server_script.json` (Create Harvest Entry) | Stamp `custom_section` |
| App `src/types/index.ts`, `src/services/api.ts`, `src/services/sync.ts` | Types, endpoints, queue action |
| App `src/screens/agriculture/PlotCountScreen.tsx` (replaces `BedSamplingScreen.tsx`) | Plot counting |
| App `src/components/BayForecastCard.tsx` | "Your bay" forecast card |
| App `App.tsx`, `src/screens/AgricultureScreen.tsx`, `src/screens/PhotoHarvestScreen.tsx` | Navigation and card placement |

---

### Task 1: Doctypes and fields

**Files:**
- Create: `upande_agriculture/upande_agriculture/doctype/{sample_plot,greenhouse_temperature,harvest_forecast_calibration,harvest_forecast_snapshot}/{__init__.py,<name>.json,<name>.py}`
- Modify: `upande_agriculture/upande_agriculture/doctype/bed_sample/bed_sample.json`, `bed_sample.py`
- Modify: `upande_agriculture/upande_agriculture/doctype/crop_protocol_growth_stage/crop_protocol_growth_stage.json`
- Create: `upande_agriculture/upande_agriculture/custom/warehouse_section.json`
- Create: `upande_agriculture/tests/forecast_fixtures.py`
- Test: `upande_agriculture/tests/test_forecast_doctypes.py`

**Interfaces:**
- Produces:
  - `Sample Plot` (fields `title, greenhouse, section, plot_no, bed, plants, active, variety`; autoname `PLOT-#####`).
  - `Greenhouse Temperature` (`greenhouse, date, mean_temp`).
  - `Harvest Forecast Calibration` (named by `variety`; `fitted_on, status, time_scale, bud_survival, regrowth_days, regrowth_yield, ref_temp, rounds_used, error_bands`).
  - `Harvest Forecast Snapshot` (`made_on, target_date, greenhouse, section, variety, stems, low, high, model_version`).
  - `Bed Sample.sample_plot`, `Bed Sample.section`.
  - `Crop Protocol Growth Stage.spread_days`, `.survival` (Percent).
  - `Warehouse Section.custom_plants`.
  - Fixtures: `make_variety(name) -> str`, `make_greenhouse(name, sections) -> str`, `make_plot(greenhouse, section, plot_no=1, plants=10) -> str`, `make_count(plot, day, counts, plants=10, uuid=None) -> str`, `make_harvest(greenhouse, section, item, day, qty) -> str`.

The doctype JSONs, `sample_plot.py`, `bed_sample.py` and `custom/warehouse_section.json` already exist in the working tree, written in the interrupted session on 2026-10-08. Verify they match the content below; write any that differ.

- [ ] **Step 1: Verify or write the four new doctypes**

`sample_plot/sample_plot.json` (fields in order):
```json
{"fieldname": "title", "fieldtype": "Data", "label": "Title", "read_only": 1, "in_list_view": 1},
{"fieldname": "greenhouse", "fieldtype": "Link", "label": "Greenhouse", "options": "Warehouse", "reqd": 1, "in_standard_filter": 1},
{"fieldname": "section", "fieldtype": "Data", "label": "Section", "reqd": 1, "in_list_view": 1, "in_standard_filter": 1},
{"fieldname": "plot_no", "fieldtype": "Int", "label": "Plot No", "reqd": 1, "default": "1"},
{"fieldname": "column_break_1", "fieldtype": "Column Break"},
{"fieldname": "bed", "fieldtype": "Int", "label": "Bed", "description": "The bed the plot is on. Mark its first plant with a tag."},
{"fieldname": "plants", "fieldtype": "Int", "label": "Plants in Plot", "default": "10", "reqd": 1, "non_negative": 1},
{"fieldname": "active", "fieldtype": "Check", "label": "Active", "default": "1", "in_list_view": 1},
{"fieldname": "variety", "fieldtype": "Link", "label": "Variety", "options": "Item", "read_only": 1, "in_list_view": 1, "description": "The section's variety, copied on save."}
```
Doctype-level settings: `"autoname": "format:PLOT-{#####}", "title_field": "title", "module": "Upande Agriculture"`. Permissions: System Manager and Agriculture Manager full; Agriculture User and Stock User read/write/create/report.

`sample_plot/sample_plot.py`:
```python
import frappe
from frappe.model.document import Document


class SamplePlot(Document):
	def validate(self):
		dup = frappe.db.get_value("Sample Plot", {"greenhouse": self.greenhouse, "section": self.section,
			"plot_no": self.plot_no, "name": ["!=", self.name]})
		if dup:
			frappe.throw(f"{self.greenhouse} section {self.section} already has plot {self.plot_no} ({dup}).")
		self.variety = frappe.db.get_value("Warehouse Section",
			{"parent": self.greenhouse, "parenttype": "Warehouse", "section": self.section}, "custom_variety") or self.variety
		gh = frappe.db.get_value("Warehouse", self.greenhouse, "warehouse_name") or self.greenhouse
		self.title = f"{gh} · {self.section} · plot {self.plot_no}"
```

`greenhouse_temperature.json`:
- fields: `greenhouse` (Link Warehouse, reqd), `date` (Date, reqd), `mean_temp` (Float, reqd, label "Mean Temperature (°C)").
- `"autoname": "format:{greenhouse}-{date}"`.

`harvest_forecast_calibration.json`:
- `"autoname": "field:variety"`, `track_changes: 1`.
- fields:
  - `variety` (Link Item, reqd, unique), `fitted_on` (Datetime, read_only), `status` (Data, read_only), `column_break_1`
  - `time_scale` (Float, default "1", precision 3), `bud_survival` (Float, default "1"), `regrowth_days` (Int, default "56"), `regrowth_yield` (Float, default "0.8")
  - `ref_temp` (Float, read_only), `section_errors` (Section Break "Accuracy"), `rounds_used` (Int, read_only), `error_bands` (Code, options JSON, read_only)

`harvest_forecast_snapshot.json`:
- `"autoname": "hash", "in_create": 1`; System Manager read/report/export/delete; Agriculture roles read/report.
- fields: `made_on` (Date, reqd), `target_date` (Date, reqd), `greenhouse` (Link Warehouse), `section` (Data), `variety` (Link Item), `stems`, `low`, `high` (Float), `model_version` (Data).

The other three `.py` controllers are `class X(Document): pass`.

- [ ] **Step 2: Verify or write the Bed Sample and Growth Stage changes**

In `bed_sample.json`:
- Insert `sample_plot` (Link Sample Plot, `in_standard_filter`) and `section` (Data, read_only, `in_list_view`) before `crop_cycle`.
- Drop `reqd` from `crop_cycle` and `bed_number`.
- Drop `fetch_from` from `greenhouse`, `variety` and `crop_protocol`.

`bed_sample.py`:
```python
import frappe
from frappe.model.document import Document


class BedSample(Document):
	def validate(self):
		if not (self.sample_plot or self.crop_cycle):
			frappe.throw("Pick the Sample Plot (or Crop Cycle) that was counted.")
		if (self.plants_counted or 0) <= 0:
			frappe.throw("Plants Counted must be at least 1.")
		# validate() runs before link fetching on insert, so copy these here.
		if self.sample_plot:
			plot = frappe.db.get_value("Sample Plot", self.sample_plot,
				["greenhouse", "section", "variety", "bed"], as_dict=True)
			self.greenhouse, self.section, self.variety = plot.greenhouse, plot.section, plot.variety
			self.bed_number = self.bed_number or plot.bed
		else:
			self.greenhouse, self.variety, self.crop_protocol = frappe.db.get_value(
				"Crop Cycle", self.crop_cycle, ["greenhouse", "variety", "crop_protocol"])
		self.crop_protocol = self.crop_protocol or frappe.db.get_value(
			"Crop Protocol", {"variety_item": self.variety})
		self.total_count = sum(r.count or 0 for r in self.stages)
		where = f"{self.section}" if self.section else f"bed {self.bed_number}"
		self.title = f"{self.greenhouse} {where} · {self.variety}"
		if self.bed_number:
			self.bed = self.bed or frappe.db.get_value("Bed", {"greenhouse": self.greenhouse, "bed": self.bed_number})
```

`crop_protocol_growth_stage.json`:
- `field_order` = `["stage_name","days_to_harvest","spread_days","survival","description"]`.
- New fields:
  - `spread_days` (Float, `in_list_view`, description "How far either side of Days to Harvest these buds are usually cut (one standard deviation). Blank = 20% of the days.")
  - `survival` (Percent, `in_list_view`, description "Share of buds at this stage that are cut. Blank = the built-in default for the stage.")

`custom/warehouse_section.json`: one Custom Field.
- `dt` "Warehouse Section", `fieldname` "custom_plants", `fieldtype` "Int", `insert_after` "to_bed", `label` "Plants".
- `description` "Plants standing in this section. The harvest forecast scales the sample plots' counts by it."
- `in_list_view` 1, `non_negative` 1, `module` "Upande Agriculture", `name` "Warehouse Section-custom_plants".
- `sync_on_migrate` 1.

- [ ] **Step 3: Write the shared fixtures**

`upande_agriculture/tests/forecast_fixtures.py`:
```python
"""DB fixtures for the harvest forecast tests (bhcloud.local: upande_core + upande_harvest + upande_agriculture)."""
import frappe

from upande_agriculture.tests import default_company, default_uom


def make_variety(name="FC-ROSE"):
	if not frappe.db.exists("Item", name):
		frappe.get_doc({"doctype": "Item", "item_code": name, "item_name": name,
			"item_group": "All Item Groups", "stock_uom": default_uom(), "is_stock_item": 1,
		}).insert(ignore_permissions=True)
	return name


def _farm():
	"""A farm allowed to have sections and beds (upande_core checks)."""
	for f in frappe.get_all("Farm", pluck="name"):
		types = set(frappe.get_all("Farm Type Item", filters={"parent": f}, pluck="farm_type"))
		if {"Has Sections", "Has Beds"} <= types:
			return f
	doc = frappe.get_doc({"doctype": "Farm", "farm_name": "FC-FARM", "abbreviation": "FCF",
		"company": default_company(),
		"farm_type": [{"farm_type": t} for t in ("Has Greenhouses", "Has Sections", "Has Beds")]})
	return doc.insert(ignore_permissions=True).name


def make_greenhouse(name="FC GH", sections=(("S1", 1, 9, "FC-ROSE", 1000),)):
	"""sections: (section, from_bed, to_bed, variety, plants) tuples."""
	existing = frappe.db.get_value("Warehouse", {"warehouse_name": name})
	if existing:
		return existing
	return frappe.get_doc({
		"doctype": "Warehouse", "warehouse_name": name, "company": default_company(),
		"warehouse_type": "Greenhouse", "custom_farm": _farm(),
		"custom_sections": [{"section": s, "from_bed": a, "to_bed": b, "custom_variety": v, "custom_plants": p}
			for s, a, b, v, p in sections],
	}).insert(ignore_permissions=True).name


def make_plot(greenhouse, section, plot_no=1, plants=10):
	name = frappe.db.get_value("Sample Plot", {"greenhouse": greenhouse, "section": section, "plot_no": plot_no})
	return name or frappe.get_doc({"doctype": "Sample Plot", "greenhouse": greenhouse, "section": section,
		"plot_no": plot_no, "bed": 1, "plants": plants}).insert(ignore_permissions=True).name


def make_count(plot, day, counts, plants=10, uuid=None):
	"""counts: {stage_name: buds counted on the plot's plants}."""
	return frappe.get_doc({"doctype": "Bed Sample", "sample_plot": plot, "sampling_date": day,
		"plants_counted": plants, "client_uuid": uuid or frappe.generate_hash(length=12),
		"stages": [{"stage_name": k, "count": v} for k, v in counts.items()],
	}).insert(ignore_permissions=True).name


def make_harvest(greenhouse, section, item, day, qty):
	se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Harvesting", "company": default_company(),
		"posting_date": day, "set_posting_time": 1, "custom_greenhouse": greenhouse, "custom_section": section,
		"items": [{"item_code": item, "qty": qty, "t_warehouse": greenhouse, "basic_rate": 1,
			"allow_zero_valuation_rate": 1}]})
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name
```

- [ ] **Step 4: Write the failing doctype test**

`upande_agriculture/tests/test_forecast_doctypes.py`:
```python
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from upande_agriculture.tests.forecast_fixtures import make_count, make_greenhouse, make_plot, make_variety


class TestForecastDoctypes(FrappeTestCase):
	def test_plot_takes_section_variety_and_title(self):
		make_variety()
		gh = make_greenhouse()
		plot = frappe.get_doc("Sample Plot", make_plot(gh, "S1"))
		self.assertEqual(plot.variety, "FC-ROSE")
		self.assertIn("S1 · plot 1", plot.title)

	def test_duplicate_plot_number_refused(self):
		make_variety()
		gh = make_greenhouse()
		make_plot(gh, "S1", 1)
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Sample Plot", "greenhouse": gh, "section": "S1", "plot_no": 1,
				"plants": 10}).insert(ignore_permissions=True)

	def test_count_on_plot_fills_section_and_totals(self):
		make_variety()
		gh = make_greenhouse()
		s = frappe.get_doc("Bed Sample", make_count(make_plot(gh, "S1"), nowdate(), {"Rice": 12, "Pea": 3}))
		self.assertEqual((s.greenhouse, s.section, s.variety, s.total_count), (gh, "S1", "FC-ROSE", 15))

	def test_section_has_plants_field(self):
		self.assertTrue(frappe.get_meta("Warehouse Section").has_field("custom_plants"))
```

- [ ] **Step 5: Migrate and run the tests**

Run:
```bash
cd /home/teddy5456/frappe-bench && bench --site bhcloud.local set-config allow_tests true && bench --site bhcloud.local migrate && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_doctypes
```
Expected: 4 tests OK. If `_farm()` fails because upande_core requires another field, read the error and add that field in the fixture.

- [ ] **Step 6: Commit**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture
git add upande_agriculture/upande_agriculture/doctype/{sample_plot,greenhouse_temperature,harvest_forecast_calibration,harvest_forecast_snapshot} \
  upande_agriculture/upande_agriculture/doctype/bed_sample upande_agriculture/upande_agriculture/doctype/crop_protocol_growth_stage \
  upande_agriculture/upande_agriculture/custom/warehouse_section.json upande_agriculture/tests/forecast_fixtures.py \
  upande_agriculture/tests/test_forecast_doctypes.py docs/superpowers
git reset -q -- '*__pycache__*'
git commit -m "Harvest forecast: sample plots, temperatures, calibration and snapshot doctypes

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 2: `curves.py`, the pure stage maths

**Files:**
- Create: `upande_agriculture/forecast/__init__.py` (empty), `upande_agriculture/forecast/curves.py`
- Test: `upande_agriculture/tests/test_forecast_curves.py`

**Interfaces:**
- Produces:
  - `BASE_TEMP = 5.3`
  - `DEFAULT_STAGES: list[dict]`
  - `stage_params(rows) -> dict[str, tuple[float, float, float]]` (days, spread, survival as a fraction)
  - `day_mass(offset: int, mean: float, sd: float) -> float`
  - `temperature_factor(ref_temp, recent_temp) -> float`
  - `bucket(h: int) -> str`, `default_band(h) -> (lo, hi)`, `band(h, bands: dict) -> (lo, hi)`
  - `quantile(values: list[float], q: float) -> float`

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_curves.py`:
```python
"""Pure maths: python -m unittest upande_agriculture.tests.test_forecast_curves"""
import unittest

from upande_agriculture.forecast import curves


class TestCurves(unittest.TestCase):
	def test_day_mass_sums_to_one(self):
		self.assertAlmostEqual(sum(curves.day_mass(o, 28, 5.6) for o in range(-10, 70)), 1.0, places=6)

	def test_day_mass_peaks_at_mean(self):
		masses = [curves.day_mass(o, 10, 2) for o in range(21)]
		self.assertEqual(masses.index(max(masses)), 10)

	def test_zero_spread_is_one_day(self):
		self.assertEqual(curves.day_mass(5, 5, 0), 1.0)
		self.assertEqual(curves.day_mass(4, 5, 0), 0.0)

	def test_defaults_when_no_rows(self):
		p = curves.stage_params(None)
		self.assertEqual(list(p), ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"])
		self.assertEqual(p["Rice"], (28.0, 5.6, 0.85))

	def test_blanks_take_defaults_unknown_without_days_dropped(self):
		p = curves.stage_params([
			{"stage_name": "Rice", "days_to_harvest": 30, "spread_days": None, "survival": None},
			{"stage_name": "Marble", "days_to_harvest": None},
			{"stage_name": "Bud", "days_to_harvest": 12, "spread_days": 1, "survival": 0.5},
		])
		self.assertEqual(p["Rice"], (30.0, 6.0, 0.85))
		self.assertNotIn("Marble", p)
		self.assertEqual(p["Bud"], (12.0, 1.5, 0.5))  # spread floor 1.5

	def test_temperature_factor(self):
		self.assertAlmostEqual(curves.temperature_factor(18, 20), 12.7 / 14.7)
		self.assertEqual(curves.temperature_factor(None, 20), 1.0)
		self.assertEqual(curves.temperature_factor(18, 4), 1.0)

	def test_bands(self):
		self.assertEqual(curves.default_band(0), (0.85, 1.15))
		lo, hi = curves.default_band(14)
		self.assertAlmostEqual(lo, 0.71)
		self.assertAlmostEqual(hi, 1.29)
		self.assertEqual(curves.band(1, {"0-2": [0.9, 1.1, 0.05]}), (0.9, 1.1))
		self.assertEqual(curves.band(5, {"0-2": [0.9, 1.1, 0.05]}), curves.default_band(5))
		self.assertEqual(curves.bucket(30), "14-20")

	def test_quantile(self):
		self.assertEqual(curves.quantile([4, 1, 3, 2], 0.5), 2.5)
		self.assertEqual(curves.quantile([7], 0.9), 7)
```

- [ ] **Step 2: Run, expect ModuleNotFoundError**

Run: `cd /home/teddy5456/frappe-bench/apps/upande_agriculture && ../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_curves -v`
Expected: ERROR `No module named 'upande_agriculture.forecast'`.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/curves.py`:
```python
"""Stage timing maths for the harvest forecast. Pure Python: no Frappe.

A bud counted at a stage is cut, on average, `days` later, give or take
`spread` days (one standard deviation of a normal curve), if it survives.
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
DEFAULT_SURVIVAL = 0.9

# Error bands are kept per horizon bucket (days ahead of the forecast date).
BUCKETS = [(0, 2, "0-2"), (3, 6, "3-6"), (7, 13, "7-13"), (14, 20, "14-20")]


def stage_params(rows):
	"""{stage: (days, spread, survival)} from protocol rows, survival as a
	fraction. Blank cells take the default for a stage of that name; a stage
	with no days anywhere cannot be placed in time and is dropped."""
	defaults = {s["stage_name"].lower(): s for s in DEFAULT_STAGES}
	out = {}
	for r in rows or DEFAULT_STAGES:
		name = r["stage_name"]
		d = defaults.get(name.lower(), {})
		days = r.get("days_to_harvest")
		if days in (None, ""):
			days = d.get("days_to_harvest")
		if days in (None, ""):
			continue
		days = float(days)
		spread = max(MIN_SPREAD, float(r.get("spread_days") or SPREAD_FRACTION * days))
		survival = r.get("survival")
		if survival in (None, ""):
			survival = d.get("survival", DEFAULT_SURVIVAL)
		out[name] = (days, spread, float(survival))
	return out


def day_mass(offset, mean, sd):
	"""Share of a stage's buds cut on whole day `offset` after the count."""
	if sd <= 0:
		return 1.0 if round(mean) == offset else 0.0
	cdf = lambda x: 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))
	return cdf(offset + 0.5) - cdf(offset - 0.5)


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
```

- [ ] **Step 4: Run, expect PASS**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_curves -v`
Expected: 8 tests OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/__init__.py upande_agriculture/forecast/curves.py upande_agriculture/tests/test_forecast_curves.py
git commit -m "Harvest forecast: stage timing curves, temperature factor, error bands

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 3: `engine.py`, buds, regrowth and the section forecast

**Files:**
- Create: `upande_agriculture/forecast/engine.py`
- Test: `upande_agriculture/tests/test_forecast_engine.py`

**Interfaces:**
- Consumes: `curves.day_mass`
- Produces:
  - `bud_curve(rates: dict, stages: dict, count_date: date, k: float, start: date, days: int) -> list[float]` (stems per plant per day)
  - `regrowth_curve(cuts: dict[date, float], regrowth_days: float, start, days, visible_after=None, k=1.0, first_stage_days=0.0) -> list[float]` (stems per day, yield 1)
  - `section_forecast(start, days, plants, round_, stages, cuts, params, temp_factor=1.0) -> {"buds": [...], "regrowth": [...], "stems": [...]}`
    - `round_` is `None` or `{"date": date, "rates": {stage: buds per plant}}`
    - `params` keys: `time_scale, bud_survival, regrowth_days, regrowth_yield`

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_engine.py`:
```python
"""Pure maths: python -m unittest upande_agriculture.tests.test_forecast_engine"""
import datetime
import unittest

from upande_agriculture.forecast import engine

D = datetime.date(2026, 10, 5)
P = {"time_scale": 1.0, "bud_survival": 1.0, "regrowth_days": 56, "regrowth_yield": 1.0}


class TestEngine(unittest.TestCase):
	def test_bud_curve_total_and_peak(self):
		out = engine.bud_curve({"Rice": 2.0}, {"Rice": (10.0, 1.0, 0.5)}, D, 1.0, D, 21)
		self.assertAlmostEqual(sum(out), 1.0, places=4)  # 2 buds/plant x 50% survival
		self.assertEqual(out.index(max(out)), 10)

	def test_time_scale_moves_the_peak(self):
		out = engine.bud_curve({"Rice": 1.0}, {"Rice": (10.0, 1.0, 1.0)}, D, 0.5, D, 21)
		self.assertEqual(out.index(max(out)), 5)

	def test_unknown_stage_ignored(self):
		self.assertEqual(sum(engine.bud_curve({"Marble": 9.0}, {"Rice": (10.0, 1.0, 1.0)}, D, 1, D, 21)), 0)

	def test_counted_after_start_still_lands_later(self):
		# forecast from D, count made 2 days earlier: peak 8 days into the window
		out = engine.bud_curve({"Rice": 1.0}, {"Rice": (10.0, 1.0, 1.0)}, D - datetime.timedelta(days=2), 1, D, 21)
		self.assertEqual(out.index(max(out)), 8)

	def test_regrowth_lands_regrowth_days_after_the_cut(self):
		out = engine.regrowth_curve({D - datetime.timedelta(days=50): 100.0}, 56, D, 21)
		self.assertEqual(out.index(max(out)), 6)
		self.assertGreater(sum(out), 75)

	def test_regrowth_already_visible_is_skipped(self):
		old = D - datetime.timedelta(days=50)  # its bud reached rice at D-22, before the count on D
		new = D - datetime.timedelta(days=20)  # reaches rice at D+8: not yet counted
		self.assertEqual(sum(engine.regrowth_curve({old: 100.0}, 56, D, 21, D, 1.0, 28.0)), 0)
		self.assertGreater(sum(engine.regrowth_curve({new: 100.0}, 56, D, 40, D, 1.0, 28.0)), 0)

	def test_section_forecast_scales_by_plants_and_adds_regrowth(self):
		stages = {"Rice": (10.0, 1.0, 1.0)}
		f = engine.section_forecast(D, 21, 1000, {"date": D, "rates": {"Rice": 1.0}}, stages,
			{D - datetime.timedelta(days=50): 100.0}, P)
		self.assertAlmostEqual(sum(f["buds"]), 1000, delta=1)
		self.assertEqual([round(a + b, 6) for a, b in zip(f["buds"], f["regrowth"])], [round(x, 6) for x in f["stems"]])

	def test_no_plants_means_regrowth_only(self):
		f = engine.section_forecast(D, 21, 0, None, {"Rice": (10.0, 1.0, 1.0)},
			{D - datetime.timedelta(days=50): 100.0}, P)
		self.assertEqual(sum(f["buds"]), 0)
		self.assertGreater(sum(f["regrowth"]), 75)
```

- [ ] **Step 2: Run, expect failure**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_engine -v`
Expected: ERROR, cannot import `engine`.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/engine.py`:
```python
"""The section forecast, pure: no Frappe.

Two sources of stems for a day:
- buds counted on the sample plots, per plant, each stage spread over days by
  its curve and scaled to the section's plants;
- regrowth: every stem cut grows back one cycle later. A cut whose new bud had
  already reached the earliest counted stage by the last count is part of that
  count, so it is not added twice.
"""
from datetime import timedelta

from upande_agriculture.forecast.curves import day_mass

REGROWTH_SPREAD = 0.12  # sd of the regrowth timing as a share of its days


def bud_curve(rates, stages, count_date, k, start, days):
	"""Expected stems per plant per day, from `start` for `days` days."""
	out = [0.0] * days
	first = (start - count_date).days
	for name, rate in rates.items():
		if not rate or name not in stages:
			continue
		mean, sd, survival = stages[name]
		for i in range(days):
			out[i] += rate * survival * day_mass(first + i, k * mean, k * sd)
	return out


def regrowth_curve(cuts, regrowth_days, start, days, visible_after=None, k=1.0, first_stage_days=0.0):
	"""Expected new stems per day (one per stem cut) from past cuts {date: stems}."""
	sd = max(1.0, REGROWTH_SPREAD * regrowth_days)
	out = [0.0] * days
	for cut_on, stems in cuts.items():
		if visible_after and cut_on + timedelta(days=regrowth_days - k * first_stage_days) <= visible_after:
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_engine -v`
Expected: 8 tests OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/engine.py upande_agriculture/tests/test_forecast_engine.py
git commit -m "Harvest forecast: bud and regrowth curves per section

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 4: upande_harvest stamps the section on harvest entries

**Files:**
- Modify: `/home/teddy5456/frappe-bench/apps/upande_harvest/upande_harvest/fixtures/server_script.json` (script `Create Harvest Entry`)

**Interfaces:**
- Produces: every new Harvesting Stock Entry has `custom_section` = the harvested section. Task 5's `data.cuts` reads it.

- [ ] **Step 1: Edit the script (JSON-safe, minimal diff)**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_harvest && python3 - <<'EOF'
import json
p='upande_harvest/fixtures/server_script.json'; raw=open(p).read(); j=json.loads(raw)
s=[x for x in j if x['name']=='Create Harvest Entry'][0]
a='''            "custom_greenhouse": greenhouse,
            "custom_harvester": employee_id,'''
assert s['script'].count(a)==1
s['script']=s['script'].replace(a,'''            "custom_greenhouse": greenhouse,
            "custom_section": section_name,
            "custom_harvester": employee_id,''')
open(p,'w').write(json.dumps(j,indent=1)+("\n" if raw.endswith("\n") else ""))
EOF
git diff --stat upande_harvest/fixtures/server_script.json
```
Expected: `1 file changed, 1 insertion(+), 1 deletion(-)`.

- [ ] **Step 2: Migrate and verify with a real photo harvest**

```bash
cd /home/teddy5456/frappe-bench && bench --site bhcloud.local migrate >/dev/null && cd sites && ../env/bin/python -c "
import frappe, base64, io
from PIL import Image
frappe.init('bhcloud.local', sites_path='.'); frappe.connect(); frappe.set_user('Administrator')
from upande_harvest.upande_harvest import photo_count as pc
gh = frappe.db.get_value('Warehouse', {'warehouse_name': 'GH01'}); emp = frappe.db.get_value('Employee', {'first_name': 'Wanjiku'})
b = frappe.generate_hash(length=6); frappe.get_doc({'doctype': 'Bucket QR Code', 'id': 'BKT-SEC' + b}).insert()
pc.reserve_photo_harvest(client_uuid='sec-' + b, bucket_id='BKT-SEC' + b, greenhouse=gh, section='S1', harvester=emp, stem_length='60cm', farm='Bila')
im = io.BytesIO(); Image.new('RGB', (64, 64)).save(im, 'JPEG')
pc.upload_harvest_photo('sec-' + b, base64.b64encode(im.getvalue()).decode(), device_count=11, device_note='t'); frappe.db.commit()
name = frappe.db.get_value('Harvest Photo Count', {'client_uuid': 'sec-' + b}); pc.process_photo_count(name)
se = frappe.db.get_value('Harvest Photo Count', name, 'stock_entry'); print('SECTION', frappe.db.get_value('Stock Entry', se, 'custom_section'))
frappe.db.rollback()
" 2>&1 | grep SECTION
```
Expected: `SECTION S1`.

- [ ] **Step 3: Commit (upande_harvest, only this file)**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_harvest && git add upande_harvest/fixtures/server_script.json && git diff --cached --stat
git commit -m "Harvest entries record their section (per-bay picks for the forecast)

Co-Authored-By: claude-flow <ruv@ruv.net>" && git push -q upstream bilashaka
```
`git diff --cached --stat` must list only `server_script.json`.

---

### Task 5: `data.py`, database reads

**Files:**
- Create: `upande_agriculture/forecast/data.py`
- Test: `upande_agriculture/tests/test_forecast_data.py`

**Interfaces:**
- Consumes: `curves.stage_params`, the Task 1 doctypes, `Stock Entry.custom_section` (Task 4).
- Produces:
  - `ROUND_DAYS = 3`, `MAX_COUNT_AGE = 35`, `DEFAULT_REGROWTH_DAYS = 56`, `DEFAULT_REGROWTH_YIELD = 0.8`
  - `sections(farm=None, greenhouse=None, variety=None) -> list[frappe._dict]`; keys `greenhouse, warehouse_name, farm, section, from_bed, to_bed, variety, employee, employee_name, plants`
  - `stages(variety) -> dict` (from `curves.stage_params`)
  - `latest_round(greenhouse, section, as_of) -> None | {"date": date, "rates": {stage: buds per plant}, "plants_counted": int, "plots": int}`
  - `cuts(greenhouse, section, since, until) -> {date: float}`
  - `recent_temp(greenhouse, as_of, days=7) -> float | None`
  - `mean_temp(greenhouses: list, since, until) -> float | None`
  - `default_regrowth_days(variety) -> int`
  - `params(variety) -> dict`; keys `time_scale, bud_survival, regrowth_days, regrowth_yield, ref_temp, error_bands (dict), model_version (str)`
  - `length_mix(variety, as_of) -> (dict[str, float], str)`

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_data.py`:
```python
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


class TestForecastData(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse()
		self.today = getdate(nowdate())

	def test_sections_lists_variety_and_plants(self):
		row = [r for r in data.sections(greenhouse=self.gh) if r.section == "S1"][0]
		self.assertEqual((row.variety, row.plants), ("FC-ROSE", 1000))

	def test_latest_round_takes_latest_count_per_plot_within_three_days(self):
		p1, p2 = make_plot(self.gh, "S1", 1), make_plot(self.gh, "S1", 2)
		make_count(p1, add_days(self.today, -10), {"Rice": 99})   # an older round: ignored
		make_count(p1, add_days(self.today, -2), {"Rice": 10})    # superseded below
		make_count(p1, add_days(self.today, -1), {"Rice": 20, "Pea": 5})
		make_count(p2, add_days(self.today, -3), {"Rice": 10, "Marble": 4})  # same round, unknown stage kept
		r = data.latest_round(self.gh, "S1", self.today)
		self.assertEqual(r["date"], add_days(self.today, -1))
		self.assertEqual(r["plots"], 2)
		self.assertEqual(r["rates"], {"Rice": 1.5, "Pea": 0.25, "Marble": 0.2})

	def test_latest_round_none_when_too_old(self):
		make_count(make_plot(self.gh, "S1", 1), add_days(self.today, -40), {"Rice": 10})
		self.assertIsNone(data.latest_round(self.gh, "S1", self.today))

	def test_cuts_by_section_and_day(self):
		make_harvest(self.gh, "S1", "FC-ROSE", add_days(self.today, -2), 40)
		make_harvest(self.gh, "S1", "FC-ROSE", add_days(self.today, -2), 10)
		make_harvest(self.gh, "S2", "FC-ROSE", add_days(self.today, -2), 7)
		c = data.cuts(self.gh, "S1", add_days(self.today, -5), self.today)
		self.assertEqual(c, {add_days(self.today, -2): 50.0})

	def test_params_defaults_then_fitted(self):
		p = data.params("FC-ROSE")
		self.assertEqual((p["time_scale"], p["model_version"]), (1.0, "defaults"))
		frappe.get_doc({"doctype": "Harvest Forecast Calibration", "variety": "FC-ROSE", "time_scale": 0.9,
			"bud_survival": 0.8, "regrowth_days": 63, "regrowth_yield": 0.7, "fitted_on": frappe.utils.now(),
			"error_bands": json.dumps({"0-2": [0.9, 1.1, 0.04]})}).insert(ignore_permissions=True)
		p = data.params("FC-ROSE")
		self.assertEqual((p["time_scale"], p["regrowth_days"], p["error_bands"]["0-2"][0]), (0.9, 63, 0.9))
		self.assertTrue(p["model_version"].startswith("FC-ROSE@"))

	def test_temperatures(self):
		for i, t in enumerate((18, 20)):
			frappe.get_doc({"doctype": "Greenhouse Temperature", "greenhouse": self.gh,
				"date": add_days(self.today, -i), "mean_temp": t}).insert(ignore_permissions=True)
		self.assertEqual(data.recent_temp(self.gh, self.today), 19)
		self.assertIsNone(data.recent_temp("NO SUCH GH", self.today))
```

- [ ] **Step 2: Run, expect import failure**

Run: `cd /home/teddy5456/frappe-bench && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_data`
Expected: ImportError for `upande_agriculture.forecast.data`.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/data.py`:
```python
"""Database reads for the harvest forecast. Everything Frappe lives here, so
curves.py and engine.py stay pure and testable without a site."""
import json

import frappe
from frappe.utils import add_days, getdate

from upande_agriculture.forecast import curves

ROUND_DAYS = 3        # counts within 3 days of a section's latest count are one round
MAX_COUNT_AGE = 35    # older counts have all been cut by now
DEFAULT_REGROWTH_DAYS, DEFAULT_REGROWTH_YIELD = 56, 0.8
MIN_MEASURED_STEMS = 500  # harvested stems before the measured length mix is trusted


def sections(farm=None, greenhouse=None, variety=None):
	"""Every greenhouse section (upande_core Warehouse Section) with its
	variety, harvester and plants."""
	cond = ["w.warehouse_type = 'Greenhouse'", "w.disabled = 0", "ws.parenttype = 'Warehouse'"]
	args = {}
	for key, col in (("farm", "w.custom_farm"), ("greenhouse", "w.name"), ("variety", "ws.custom_variety")):
		if locals()[key]:
			cond.append(f"{col} = %({key})s")
			args[key] = locals()[key]
	return frappe.db.sql(
		f"""SELECT ws.parent AS greenhouse, w.warehouse_name, w.custom_farm AS farm, ws.section,
		       ws.from_bed, ws.to_bed, ws.custom_variety AS variety, ws.custom_employee AS employee,
		       ws.custom_employee_name AS employee_name, ws.custom_plants AS plants
		   FROM `tabWarehouse Section` ws JOIN `tabWarehouse` w ON w.name = ws.parent
		   WHERE {" AND ".join(cond)} ORDER BY w.warehouse_name, ws.section""",
		args, as_dict=True)


def stages(variety):
	protocol = variety and frappe.db.get_value("Crop Protocol", {"variety_item": variety})
	rows = protocol and frappe.get_all("Crop Protocol Growth Stage",
		filters={"parent": protocol, "parenttype": "Crop Protocol"},
		fields=["stage_name", "days_to_harvest", "spread_days", "survival"], order_by="idx")
	rows = [dict(r, survival=(r.survival / 100 if r.survival else None)) for r in (rows or [])]
	return curves.stage_params(rows)


def latest_round(greenhouse, section, as_of):
	"""The section's latest counting round up to `as_of`: each plot's latest
	count within ROUND_DAYS of the newest one, as buds per plant per stage."""
	as_of = getdate(as_of)
	rows = frappe.get_all("Bed Sample",
		filters={"greenhouse": greenhouse, "section": section,
			"sampling_date": ["between", [add_days(as_of, -MAX_COUNT_AGE), as_of]]},
		fields=["name", "sample_plot", "sampling_date", "plants_counted"],
		order_by="sampling_date desc, creation desc")
	if not rows:
		return None
	last = getdate(rows[0].sampling_date)
	per_plot = {}
	for r in rows:
		if (last - getdate(r.sampling_date)).days >= ROUND_DAYS:
			break
		per_plot.setdefault(r.sample_plot or r.name, r)
	picked = list(per_plot.values())
	plants = sum(r.plants_counted or 0 for r in picked)
	if not plants:
		return None
	totals = {}
	for s in frappe.get_all("Bed Sample Stage", fields=["stage_name", "count"],
			filters={"parent": ["in", [r.name for r in picked]], "parenttype": "Bed Sample"}):
		totals[s.stage_name] = totals.get(s.stage_name, 0) + (s.count or 0)
	return {"date": last, "rates": {k: v / plants for k, v in totals.items()},
		"plants_counted": plants, "plots": len(picked)}


def cuts(greenhouse, section, since, until):
	"""{day: stems} cut in a section: its submitted Harvesting entries."""
	rows = frappe.db.sql(
		"""SELECT se.posting_date AS d, SUM(sed.qty) AS q
		   FROM `tabStock Entry` se JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
		     AND se.custom_greenhouse = %s AND se.custom_section = %s
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY se.posting_date""",
		(greenhouse, section, getdate(since), getdate(until)), as_dict=True)
	return {getdate(r.d): float(r.q) for r in rows}


def recent_temp(greenhouse, as_of, days=7):
	return mean_temp([greenhouse], add_days(as_of, -(days - 1)), as_of)


def mean_temp(greenhouses, since, until):
	if not greenhouses:
		return None
	v = frappe.db.sql(
		"""SELECT AVG(mean_temp) FROM `tabGreenhouse Temperature`
		   WHERE greenhouse IN %s AND date BETWEEN %s AND %s""",
		(tuple(greenhouses), getdate(since), getdate(until)))[0][0]
	return float(v) if v is not None else None


def default_regrowth_days(variety):
	weeks = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "weeks_between_cuts")
	return int(weeks * 7) if weeks else DEFAULT_REGROWTH_DAYS


def params(variety):
	p = {"time_scale": 1.0, "bud_survival": 1.0, "regrowth_days": default_regrowth_days(variety),
		"regrowth_yield": DEFAULT_REGROWTH_YIELD, "ref_temp": None, "error_bands": {}, "model_version": "defaults"}
	cal = frappe.db.get_value("Harvest Forecast Calibration", variety,
		["time_scale", "bud_survival", "regrowth_days", "regrowth_yield", "ref_temp", "error_bands", "fitted_on"],
		as_dict=True)
	if cal and cal.fitted_on:
		p.update({"time_scale": float(cal.time_scale or 1), "bud_survival": float(cal.bud_survival or 0),
			"regrowth_days": int(cal.regrowth_days or p["regrowth_days"]),
			"regrowth_yield": float(cal.regrowth_yield or 0), "ref_temp": cal.ref_temp,
			"error_bands": json.loads(cal.error_bands or "{}"), "model_version": f"{variety}@{cal.fitted_on}"})
	return p


def length_mix(variety, as_of):
	"""({length: share}, basis): the last 4 weeks' harvest if there is enough
	of it, else the Crop Protocol's grade mix."""
	rows = frappe.db.sql(
		"""SELECT iva.attribute_value AS length, SUM(sed.qty) AS qty
		   FROM `tabStock Entry` se
		   JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		   JOIN `tabItem` i ON i.name = sed.item_code
		   JOIN `tabItem Variant Attribute` iva ON iva.parent = i.name
		        AND iva.attribute IN ('Length', 'Stem Length')
		   WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting' AND i.variant_of = %s
		     AND se.posting_date BETWEEN %s AND %s
		   GROUP BY iva.attribute_value""",
		(variety, add_days(as_of, -28), as_of), as_dict=True)
	total = sum(r.qty for r in rows)
	if total >= MIN_MEASURED_STEMS:
		return {r.length: float(r.qty / total) for r in rows}, f"Harvested, last 4 weeks ({int(total)} stems)"
	protocol = frappe.db.get_value("Crop Protocol", {"variety_item": variety})
	mix = protocol and frappe.get_all("Crop Protocol Grade Mix",
		filters={"parent": protocol, "parenttype": "Crop Protocol"}, fields=["length_cm", "pct"])
	if mix:
		return {f"{m.length_cm}cm": (m.pct or 0) / 100 for m in mix}, "Crop Protocol grade mix"
	return {}, "No length mix yet"
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_data`
Expected: 6 tests OK.

- [ ] **Step 5: Commit**

```bash
cd apps/upande_agriculture && git add upande_agriculture/forecast/data.py upande_agriculture/tests/test_forecast_data.py
git commit -m "Harvest forecast: plot rounds, cuts, temperatures and parameters from the database

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 6: `service.py`, a section forecast with bands and flags

**Files:**
- Create: `upande_agriculture/forecast/service.py`
- Test: `upande_agriculture/tests/test_forecast_service.py`

**Interfaces:**
- Consumes: `data.*`, `engine.section_forecast`, `curves.band`, `curves.temperature_factor`
- Produces:
  - `HORIZON = 21`, `STALE_DAYS = 7`
  - `forecast_section(row, start=None, days=HORIZON, as_of=None) -> dict`, with keys:
    - `greenhouse, warehouse_name, section, variety, employee, employee_name, plants, last_count (date|None), model_version`
    - `flags: {"no_plants", "no_count", "stale_count"}` (bools)
    - `daily: [{"date": date, "stems", "low", "high", "buds", "regrowth"}]` (floats)
  - `forecast_all(farm=None, greenhouse=None, variety=None, start=None, days=HORIZON) -> list[dict]`, sections without a variety skipped.

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_service.py`:
```python
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data, service
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


def _row(gh, section):
	return [r for r in data.sections(greenhouse=gh) if r.section == section][0]


class TestForecastService(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.today = getdate(nowdate())
		self.gh = make_greenhouse("FC SVC", (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 18, "FC-ROSE", None)))

	def test_counted_section_forecasts_buds_with_bands(self):
		make_count(make_plot(self.gh, "S1"), self.today, {"Showing colour": 10})  # 1 bud/plant, ~8 days out
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertEqual(len(f["daily"]), 21)
		self.assertFalse(any(f["flags"].values()))
		total = sum(d["stems"] for d in f["daily"])
		self.assertAlmostEqual(total, 1000 * 0.97, delta=5)
		d8 = f["daily"][8]
		self.assertLess(d8["low"], d8["stems"])
		self.assertGreater(d8["high"], d8["stems"])

	def test_no_plants_regrowth_only_flagged(self):
		make_harvest(self.gh, "S2", "FC-ROSE", add_days(self.today, -50), 100)
		make_count(make_plot(self.gh, "S2"), self.today, {"Rice": 10})
		f = service.forecast_section(_row(self.gh, "S2"))
		self.assertTrue(f["flags"]["no_plants"])
		self.assertEqual(sum(d["buds"] for d in f["daily"]), 0)
		self.assertGreater(sum(d["regrowth"] for d in f["daily"]), 50)

	def test_never_counted_and_stale_flags(self):
		self.assertTrue(service.forecast_section(_row(self.gh, "S1"))["flags"]["no_count"])
		make_count(make_plot(self.gh, "S1"), add_days(self.today, -9), {"Rice": 10})
		f = service.forecast_section(_row(self.gh, "S1"))
		self.assertTrue(f["flags"]["stale_count"])
		self.assertEqual(f["last_count"], add_days(self.today, -9))

	def test_forecast_all_covers_sections(self):
		got = {(f["greenhouse"], f["section"]) for f in service.forecast_all(greenhouse=self.gh)}
		self.assertEqual(got, {(self.gh, "S1"), (self.gh, "S2")})
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_service`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/service.py`:
```python
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
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_service`
Expected: 4 tests OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/service.py upande_agriculture/tests/test_forecast_service.py
git commit -m "Harvest forecast: per-section daily forecast with error bands and data flags

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 7: `calibrate.py`, the weekly per-variety fit

**Files:**
- Create: `upande_agriculture/forecast/calibrate.py`
- Test: `upande_agriculture/tests/test_forecast_calibrate.py`

**Interfaces:**
- Consumes: `data.*`, `engine.bud_curve`, `engine.regrowth_curve`, `curves.*`, `curves.day_mass` (in the test's simulator)
- Produces: `calibrate(variety, today=None) -> Document` (Harvest Forecast Calibration, saved). On success it sets `fitted_on`, `time_scale`, `bud_survival`, `regrowth_days`, `regrowth_yield`, `ref_temp`, `rounds_used`, `error_bands` (JSON `{bucket: [q10, q90, mape]}`) and `status`. With too little data only `status` is set, and `fitted_on` stays empty, so the defaults remain in force.

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_calibrate.py`:
```python
"""A simulated rose crop with a known timing: buds appear every day in a
6-week wave, each takes K_TRUE x ~28 days (normal spread) from rice stage to
the knife, plot counts report each bud's stage from its remaining days, and
picks are the buds reaching harvest. Calibration must recover the timing and
forecast the picks well. (No deaths and no regrowth in the simulation, so
only timing and forecast error are asserted.)"""
import json
import math

from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import calibrate
from upande_agriculture.forecast.curves import day_mass
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)

K_TRUE = 0.9
PLANTS, PLOT = 1000, 10
# Remaining days (at timing 1.0) at which a bud moves into each stage: the
# midpoints between the default stages' days to harvest (3, 8, 15, 22, 28).
EDGES = [("Opening", 5.5), ("Showing colour", 11.5), ("Chickpea", 18.5), ("Pea", 25.0), ("Rice", 1e9)]
DURATIONS = {d: day_mass(d, K_TRUE * 28, K_TRUE * 5.6) for d in range(5, 60)}


def births(day):  # new rice-stage buds per plant per day, a 6-week wave
	return 0.1 * (1 + 0.6 * math.sin(2 * math.pi * day / 42))


def stage(remaining):
	for name, edge in EDGES:
		if remaining / K_TRUE <= edge:
			return name


def counts_on(c):
	"""Expected buds per plant by stage on day c (days are ints)."""
	out = {}
	for b in range(c - 60, c + 1):
		for d, m in DURATIONS.items():
			if b + d > c:
				name = stage(b + d - c)
				out[name] = out.get(name, 0) + births(b) * m
	return out


def picks_on(t):
	return PLANTS * sum(births(t - d) * m for d, m in DURATIONS.items())


class TestForecastCalibrate(FrappeTestCase):
	def test_too_little_data_keeps_defaults(self):
		make_variety("FC-THIN")
		make_greenhouse("FC THIN", (("S1", 1, 9, "FC-THIN", 1000),))
		doc = calibrate.calibrate("FC-THIN")
		self.assertFalse(doc.fitted_on)
		self.assertIn("Not enough data", doc.status)

	def test_recovers_timing_from_a_simulated_crop(self):
		make_variety("FC-CAL")
		gh = make_greenhouse("FC CAL", (("S1", 1, 9, "FC-CAL", PLANTS),))
		plot = make_plot(gh, "S1")
		today = getdate(nowdate())
		for i in range(-90, 0):  # day index relative to today
			day = add_days(today, i)
			if i % 3 == 0:
				make_count(plot, day, {k: round(v * PLOT) for k, v in counts_on(i).items()})
			make_harvest(gh, "S1", "FC-CAL", day, max(1, round(picks_on(i))))
		doc = calibrate.calibrate("FC-CAL", today)
		self.assertTrue(doc.fitted_on, doc.status)
		self.assertAlmostEqual(doc.time_scale, K_TRUE, delta=0.051)
		bands = json.loads(doc.error_bands)
		self.assertLess(bands["3-6"][2], 0.2)  # mean abs % error, 3-day sums, 3-6 days ahead
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_calibrate`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/calibrate.py`:
```python
"""Weekly per-variety calibration from Bilashaka's own counts and picks.

For every past counting round (with at least a week of picks after it) the
forecast that could have been made that day is rebuilt from the stored
counts and cuts, then compared with what was actually picked, in 3-day sums.
A grid over the timing scale and regrowth days, with bud survival and regrowth
yield solved by least squares at each grid point, keeps the best fit. The
spread of actual / forecast by horizon becomes the error bands."""
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

	ratios, apes = {}, {}
	for c in cases:
		b, gr = _curves(c, stages, k, R, c["tf"])
		f = [s * x + g * y for x, y in zip(b, gr)]
		for i in range(0, len(f), 3):
			fa, aa = sum(f[i:i + 3]), sum(c["actual"][i:i + 3])
			label = curves.bucket(i + 1)
			if fa > 0:
				ratios.setdefault(label, []).append(aa / fa)
			if aa > 0:
				apes.setdefault(label, []).append(abs(aa - fa) / aa)
	bands = {label: [round(curves.quantile(r, 0.1), 3), round(curves.quantile(r, 0.9), 3),
		round(sum(apes.get(label, [0])) / max(1, len(apes.get(label, []))), 3)] for label, r in ratios.items()}

	doc.update({"time_scale": k, "bud_survival": round(s, 4), "regrowth_days": R, "regrowth_yield": round(g, 4),
		"ref_temp": ref, "rounds_used": len(cases), "error_bands": json.dumps(bands, indent=1),
		"fitted_on": frappe.utils.now(),
		"status": f"Fitted on {len(cases)} count rounds and {int(picked)} picked stems."})
	doc.save(ignore_permissions=True)
	return doc
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_calibrate`
Expected: 2 tests OK. The simulation creates ~90 stock entries, so the test takes about a minute. If `time_scale` lands on a grid edge (0.70 or 1.40), the stage EDGES and the default stage days have drifted apart: keep both on the same five stages.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/calibrate.py upande_agriculture/tests/test_forecast_calibrate.py
git commit -m "Harvest forecast: weekly self-calibration of timing, survival and regrowth per variety

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 8: Scheduled jobs and the accuracy report

**Files:**
- Create: `upande_agriculture/forecast/jobs.py`
- Create: `upande_agriculture/upande_agriculture/report/harvest_forecast_accuracy/{__init__.py,harvest_forecast_accuracy.json,.py,.js}`
- Modify: `upande_agriculture/hooks.py` (`scheduler_events`)
- Test: `upande_agriculture/tests/test_forecast_jobs.py`

**Interfaces:**
- Consumes: `service.forecast_all`, `calibrate.calibrate`, `data.sections`, `data.cuts`
- Produces: `jobs.daily_snapshot(today=None) -> int` (rows written), `jobs.weekly_calibrate() -> list[str]` (varieties fitted or attempted), and the report `Harvest Forecast Accuracy`.

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_jobs.py`:
```python
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import jobs
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)


class TestForecastJobs(FrappeTestCase):
	def test_snapshot_writes_21_days_per_section_and_replaces_same_day(self):
		make_variety()
		gh = make_greenhouse("FC JOB", (("S1", 1, 9, "FC-ROSE", 1000),))
		make_count(make_plot(gh, "S1"), nowdate(), {"Rice": 10})
		jobs.daily_snapshot()
		jobs.daily_snapshot()
		n = frappe.db.count("Harvest Forecast Snapshot", {"greenhouse": gh, "made_on": nowdate()})
		self.assertEqual(n, 21)

	def test_accuracy_report_compares_snapshot_with_picks(self):
		from upande_agriculture.upande_agriculture.report.harvest_forecast_accuracy.harvest_forecast_accuracy import execute
		make_variety()
		gh = make_greenhouse("FC ACC", (("S1", 1, 9, "FC-ROSE", 1000),))
		yday = add_days(getdate(nowdate()), -1)
		frappe.get_doc({"doctype": "Harvest Forecast Snapshot", "made_on": add_days(yday, -1), "target_date": yday,
			"greenhouse": gh, "section": "S1", "variety": "FC-ROSE", "stems": 100, "low": 80, "high": 120}).insert()
		make_harvest(gh, "S1", "FC-ROSE", yday, 80)
		cols, rows = execute({"from_date": yday, "to_date": yday})
		row = [r for r in rows if r["greenhouse"] == gh][0]
		self.assertEqual((row["horizon"], row["forecast"], row["actual"]), ("0-2", 100, 80))
		self.assertAlmostEqual(row["mape"], 25.0)
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_jobs`
Expected: ImportError.

- [ ] **Step 3: Implement jobs and hooks**

`upande_agriculture/forecast/jobs.py`:
```python
"""Scheduled work: a daily snapshot of every section's forecast (audit trail,
scored by the Harvest Forecast Accuracy report) and a weekly calibration."""
import frappe
from frappe.utils import getdate, nowdate

from upande_agriculture.forecast import calibrate, data, service

FIELDS = ["name", "made_on", "target_date", "greenhouse", "section", "variety", "stems", "low", "high",
	"model_version", "creation", "modified", "owner", "modified_by", "docstatus"]


def daily_snapshot(today=None):
	today = getdate(today or nowdate())
	frappe.db.delete("Harvest Forecast Snapshot", {"made_on": today})
	now = frappe.utils.now()
	values = [
		(frappe.generate_hash(length=12), today, d["date"], f["greenhouse"], f["section"], f["variety"],
			round(d["stems"], 2), round(d["low"], 2), round(d["high"], 2), f["model_version"],
			now, now, "Administrator", "Administrator", 0)
		for f in service.forecast_all(start=today) for d in f["daily"]]
	if values:
		frappe.db.bulk_insert("Harvest Forecast Snapshot", FIELDS, values)
	frappe.db.commit()
	return len(values)


def weekly_calibrate():
	done = []
	for variety in sorted({r.variety for r in data.sections() if r.variety}):
		try:
			calibrate.calibrate(variety)
			done.append(variety)
		except Exception:
			frappe.log_error(title=f"Harvest forecast calibration failed for {variety}")
	frappe.db.commit()
	return done
```

In `upande_agriculture/hooks.py`, replace
```python
scheduler_events = {
    "daily": [
        "upande_agriculture.scheduled.rollup_actuals",
    ],
}
```
with
```python
scheduler_events = {
    "daily": [
        "upande_agriculture.scheduled.rollup_actuals",
        "upande_agriculture.forecast.jobs.daily_snapshot",
    ],
    "weekly": [
        "upande_agriculture.forecast.jobs.weekly_calibrate",
    ],
}
```

- [ ] **Step 4: Implement the accuracy report**

`harvest_forecast_accuracy.json`:
```json
{
 "add_total_row": 0, "creation": "2026-10-08 12:00:00", "disabled": 0, "docstatus": 0, "doctype": "Report",
 "is_standard": "Yes", "modified": "2026-10-08 12:00:00", "modified_by": "Administrator",
 "module": "Upande Agriculture", "name": "Harvest Forecast Accuracy", "owner": "Administrator",
 "prepared_report": 0, "ref_doctype": "Harvest Forecast Snapshot", "report_name": "Harvest Forecast Accuracy",
 "report_type": "Script Report",
 "roles": [{"role": "System Manager"}, {"role": "Agriculture Manager"}, {"role": "Agriculture User"}]
}
```

`harvest_forecast_accuracy.js`:
```javascript
frappe.query_reports["Harvest Forecast Accuracy"] = {
	filters: [
		{ fieldname: "from_date", label: __("From"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -28) },
		{ fieldname: "to_date", label: __("To"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -1) },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
	],
};
```

`harvest_forecast_accuracy.py`:
```python
"""Each day's snapshot forecast vs the stems actually picked that day, by
section and by how far ahead the forecast was made."""
from frappe.utils import add_days, getdate, nowdate

import frappe
from frappe import _

from upande_agriculture.forecast import curves, data


def execute(filters=None):
	f = filters or {}
	to_date = getdate(f.get("to_date") or add_days(nowdate(), -1))
	from_date = getdate(f.get("from_date") or add_days(to_date, -27))
	cond = {"target_date": ["between", [from_date, to_date]]}
	if f.get("variety"):
		cond["variety"] = f["variety"]
	snaps = frappe.get_all("Harvest Forecast Snapshot", filters=cond,
		fields=["made_on", "target_date", "greenhouse", "section", "variety", "stems"])
	picked = {}
	groups = {}
	for s in snaps:
		key = (s.greenhouse, s.section)
		if key not in picked:
			picked[key] = data.cuts(s.greenhouse, s.section, from_date, to_date)
		actual = picked[key].get(getdate(s.target_date), 0.0)
		h = curves.bucket((getdate(s.target_date) - getdate(s.made_on)).days)
		g = groups.setdefault((s.greenhouse, s.section, s.variety, h), {"f": 0.0, "a": 0.0, "apes": [], "days": 0})
		g["f"] += s.stems or 0
		g["a"] += actual
		g["days"] += 1
		if actual > 0:
			g["apes"].append(abs(actual - (s.stems or 0)) / actual)
	order = [b[2] for b in curves.BUCKETS]
	rows = []
	for (gh, section, variety, h), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1], order.index(kv[0][3]))):
		rows.append({"greenhouse": gh, "section": section, "variety": variety, "horizon": h,
			"forecast": round(g["f"]), "actual": round(g["a"]),
			"bias": round(100 * (g["f"] - g["a"]) / g["a"], 1) if g["a"] else None,
			"mape": round(100 * sum(g["apes"]) / len(g["apes"]), 1) if g["apes"] else None, "days": g["days"]})
	columns = [
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Link", "options": "Warehouse", "width": 150},
		{"fieldname": "section", "label": _("Section"), "fieldtype": "Data", "width": 80},
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "horizon", "label": _("Days Ahead"), "fieldtype": "Data", "width": 90},
		{"fieldname": "forecast", "label": _("Forecast"), "fieldtype": "Int", "width": 90},
		{"fieldname": "actual", "label": _("Picked"), "fieldtype": "Int", "width": 90},
		{"fieldname": "bias", "label": _("Bias %"), "fieldtype": "Float", "width": 80},
		{"fieldname": "mape", "label": _("Avg Daily Error %"), "fieldtype": "Float", "width": 120},
		{"fieldname": "days", "label": _("Days"), "fieldtype": "Int", "width": 60},
	]
	return columns, rows
```

- [ ] **Step 5: Migrate, run, expect PASS**

Run: `bench --site bhcloud.local migrate >/dev/null && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_jobs`
Expected: 2 tests OK.

- [ ] **Step 6: Commit**

```bash
git add upande_agriculture/forecast/jobs.py upande_agriculture/hooks.py upande_agriculture/upande_agriculture/report/harvest_forecast_accuracy upande_agriculture/tests/test_forecast_jobs.py
git commit -m "Harvest forecast: daily snapshots, weekly calibration, accuracy report

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 9: Phone endpoints (`forecast/api.py`)

**Files:**
- Create: `upande_agriculture/forecast/api.py`
- Delete: `upande_agriculture/bed_sampling.py`, `upande_agriculture/_check_bed_sampling.py`
- Test: `upande_agriculture/tests/test_forecast_api.py`

**Interfaces:**
- Consumes: `data.sections`, `data.stages`, `service.forecast_section`, `calibrate.calibrate`
- Produces (whitelisted, under `upande_agriculture.forecast.api.`):
  - `get_plot_plan(farm=None) -> {"count_interval_days": 3, "plots": [{"plot", "title", "greenhouse", "greenhouse_name", "section", "variety", "plants", "bed", "plot_no", "stages": [{"stage_name", "days_to_harvest"}], "last_counted": "YYYY-MM-DD"|None, "due": bool, "mine": bool}]}`
  - `submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None) -> {"name"}` (POST)
  - `get_bay_forecast(greenhouse, section, days=7) -> {"greenhouse", "section", "variety", "today", "tomorrow", "next7", "low7", "high7", "daily": [{"date", "stems", "low", "high"}], "flags", "last_count", "plants"}` (ints)
  - `recalibrate(variety) -> {"status", "fitted_on"}`
  - `ensure_plots(row)`

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_api.py`:
```python
import base64
import io

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate
from PIL import Image

from upande_agriculture.forecast import api
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestForecastApi(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse("FC API", (("S1", 1, 9, "FC-ROSE", 1000), ("S2", 10, 10, "FC-ROSE", 500)))

	def _plots(self):
		return [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == self.gh]

	def test_plan_creates_two_plots_per_section_one_for_a_single_bed(self):
		plots = self._plots()
		self.assertEqual(sorted((p["section"], p["bed"]) for p in plots), [("S1", 3), ("S1", 6), ("S2", 10)])
		self.assertTrue(all(p["due"] for p in plots))
		self.assertEqual([s["stage_name"] for s in plots[0]["stages"]], ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"])
		self.assertEqual(len(self._plots()), 3)  # second call creates nothing new

	def test_submit_is_idempotent_and_marks_plot_done(self):
		plot = self._plots()[0]["plot"]
		im = io.BytesIO(); Image.new("RGB", (32, 32), (200, 0, 0)).save(im, "JPEG")
		args = dict(client_uuid="fc-api-1", sample_plot=plot, counts='[{"stage_name": "Rice", "count": 7}]',
			plants_counted=10, captured_at=f"{nowdate()} 08:00:00", photos=[base64.b64encode(im.getvalue()).decode()])
		a, b = api.submit_plot_count(**args), api.submit_plot_count(**args)
		self.assertEqual(a, b)
		doc = frappe.get_doc("Bed Sample", a["name"])
		self.assertEqual((doc.section, doc.total_count, doc.stages[0].days_to_harvest), ("S1", 7, 28))
		self.assertTrue(doc.photo_1)
		p = [x for x in self._plots() if x["plot"] == plot][0]
		self.assertFalse(p["due"])
		self.assertEqual(p["last_counted"], nowdate())

	def test_bay_forecast_shape(self):
		f = api.get_bay_forecast(self.gh, "S1")
		self.assertEqual(len(f["daily"]), 7)
		self.assertTrue(f["flags"]["no_count"])
		for k in ("today", "tomorrow", "next7", "low7", "high7"):
			self.assertIsInstance(f[k], int)

	def test_unknown_section_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.get_bay_forecast(self.gh, "NOPE")
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_api`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/api.py`:
```python
"""Phone endpoints for plot counting and the bay forecast."""
import base64
import json

import frappe
from frappe.utils import getdate, nowdate

from upande_agriculture.forecast import calibrate as calib
from upande_agriculture.forecast import data, service

COUNT_INTERVAL_DAYS = 3  # ponytail: one interval for every section; make it a setting when sections differ
PLOT_PLANTS = 10


def ensure_plots(row):
	"""Two plots per section, a third and two thirds of the way along its beds
	(one plot when the section is a single bed). Created the first time the
	plan is asked for, so new sections need no setup."""
	if frappe.db.exists("Sample Plot", {"greenhouse": row.greenhouse, "section": row.section}):
		return
	lo = int(row.from_bed or 1)
	hi = int(row.to_bed or lo)
	beds = [lo + (hi - lo) // 3, lo + 2 * (hi - lo) // 3] if hi > lo else [lo]
	for n, bed in enumerate(beds, start=1):
		frappe.get_doc({"doctype": "Sample Plot", "greenhouse": row.greenhouse, "section": row.section,
			"plot_no": n, "bed": bed, "plants": PLOT_PLANTS}).insert(ignore_permissions=True)


@frappe.whitelist()
def get_plot_plan(farm=None):
	"""Every active plot on the farm's sections, the user's own sections first,
	then plots due a count."""
	today = getdate(nowdate())
	me = frappe.db.get_value("Employee", {"user_id": frappe.session.user})
	plots = []
	for row in data.sections(farm=farm or None):
		if not row.variety:
			continue
		ensure_plots(row)
		stages = [{"stage_name": n, "days_to_harvest": round(v[0])} for n, v in data.stages(row.variety).items()]
		for p in frappe.get_all("Sample Plot", filters={"greenhouse": row.greenhouse, "section": row.section, "active": 1},
				fields=["name", "title", "plot_no", "bed", "plants"], order_by="plot_no"):
			last = frappe.get_all("Bed Sample", filters={"sample_plot": p.name}, pluck="sampling_date",
				order_by="sampling_date desc", limit=1)
			last = getdate(last[0]) if last else None
			plots.append({"plot": p.name, "title": p.title, "greenhouse": row.greenhouse,
				"greenhouse_name": row.warehouse_name, "section": row.section, "variety": row.variety,
				"plants": p.plants, "bed": p.bed, "plot_no": p.plot_no, "stages": stages,
				"last_counted": str(last) if last else None,
				"due": not last or (today - last).days >= COUNT_INTERVAL_DAYS,
				"mine": bool(me and row.employee == me)})
	plots.sort(key=lambda p: (not p["mine"], not p["due"], p["greenhouse_name"] or "", p["section"], p["plot_no"]))
	frappe.db.commit()  # plots made by ensure_plots
	return {"count_interval_days": COUNT_INTERVAL_DAYS, "plots": plots}


@frappe.whitelist(methods=["POST"])
def submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None):
	"""One plot count. Replaying the same client_uuid (offline queue after a
	lost response) returns the first record instead of a duplicate."""
	existing = frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid})
	if existing:
		return {"name": existing}
	counts = json.loads(counts) if isinstance(counts, str) else counts
	photos = json.loads(photos) if isinstance(photos, str) else (photos or [])
	plot = frappe.get_doc("Sample Plot", sample_plot)
	days = {n: round(v[0]) for n, v in data.stages(plot.variety).items()}
	doc = frappe.get_doc({
		"doctype": "Bed Sample", "client_uuid": client_uuid, "sample_plot": plot.name,
		"plants_counted": int(plants_counted or plot.plants or PLOT_PLANTS), "notes": notes,
		"sampling_date": getdate(captured_at) if captured_at else nowdate(), "captured_at": captured_at,
		"stages": [{"stage_name": r["stage_name"], "count": int(r.get("count") or 0),
			"days_to_harvest": days.get(r["stage_name"])} for r in counts],
	}).insert()
	for i, b64 in enumerate(photos[:2], start=1):
		f = frappe.get_doc({"doctype": "File", "file_name": f"{doc.name}-{i}.jpg",
			"content": base64.b64decode(b64), "attached_to_doctype": "Bed Sample",
			"attached_to_name": doc.name, "attached_to_field": f"photo_{i}", "is_private": 1,
		}).insert(ignore_permissions=True)
		doc.db_set(f"photo_{i}", f.file_url)  # inserting a File does not fill the Attach field
	return {"name": doc.name}


@frappe.whitelist()
def get_bay_forecast(greenhouse, section, days=7):
	row = next((r for r in data.sections(greenhouse=greenhouse) if r.section == section), None)
	if not row:
		frappe.throw(f"Section {section} is not in {greenhouse}.")
	f = service.forecast_section(row, days=max(int(days), 2))
	d = f["daily"]
	week = d[:7]
	return {"greenhouse": greenhouse, "section": section, "variety": row.variety, "plants": f["plants"],
		"today": round(d[0]["stems"]), "tomorrow": round(d[1]["stems"]),
		"next7": round(sum(x["stems"] for x in week)), "low7": round(sum(x["low"] for x in week)),
		"high7": round(sum(x["high"] for x in week)),
		"daily": [{"date": str(x["date"]), "stems": round(x["stems"]), "low": round(x["low"]), "high": round(x["high"])} for x in d],
		"flags": f["flags"], "last_count": str(f["last_count"]) if f["last_count"] else None}


@frappe.whitelist()
def recalibrate(variety):
	frappe.only_for(("System Manager", "Agriculture Manager"))
	doc = calib.calibrate(variety)
	return {"status": doc.status, "fitted_on": doc.fitted_on}
```

Delete the replaced files:
```bash
git rm -q upande_agriculture/bed_sampling.py upande_agriculture/_check_bed_sampling.py
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_api`
Expected: 4 tests OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/api.py upande_agriculture/tests/test_forecast_api.py
git commit -m "Harvest forecast: plot plan, plot count and bay forecast endpoints

Replaces bed_sampling.py (random weekly beds) with permanent sample plots.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 10: Section Harvest Forecast and Weekly Harvest Forecast reports

**Files:**
- Create: `upande_agriculture/upande_agriculture/report/section_harvest_forecast/{__init__.py,.json,.py,.js}`
- Create: `upande_agriculture/upande_agriculture/report/weekly_harvest_forecast/{__init__.py,.json,.py,.js}`
- Delete: `upande_agriculture/upande_agriculture/report/bed_sample_forecast/`, `upande_agriculture/bed_forecast.py`
- Test: `upande_agriculture/tests/test_forecast_reports.py`

**Interfaces:**
- Consumes: `service.forecast_all`, `data.cuts`, `data.length_mix`, `weekcal.get_week_rule/week_key/week_start`
- Produces: reports `Section Harvest Forecast` (filters `farm, greenhouse, variety, days`) and `Weekly Harvest Forecast` (filters `farm, variety, weeks, group_by`).

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_reports.py`:
```python
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)
from upande_agriculture.upande_agriculture.report.section_harvest_forecast.section_harvest_forecast import (
	execute as section_report)
from upande_agriculture.upande_agriculture.report.weekly_harvest_forecast.weekly_harvest_forecast import (
	execute as weekly_report)


class TestForecastReports(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse("FC REP", (("S1", 1, 9, "FC-ROSE", 1000),))
		make_count(make_plot(self.gh, "S1"), nowdate(), {"Showing colour": 10})

	def test_section_report_has_a_column_per_day(self):
		cols, rows = section_report({"greenhouse": self.gh, "days": 14})
		self.assertEqual(sum(1 for c in cols if c["fieldname"].startswith("d")), 14)
		row = rows[0]
		self.assertAlmostEqual(row["total"], sum(row[f"d{i}"] for i in range(14)), delta=14)
		self.assertGreater(row["total"], 900)

	def test_weekly_report_adds_actual_so_far_this_week(self):
		today = getdate(nowdate())
		monday = add_days(today, -today.weekday())
		if monday < today:
			make_harvest(self.gh, "S1", "FC-ROSE", monday, 50)
		cols, rows = weekly_report({"weeks": 3})
		ours = [r for r in rows if r["variety"] == "FC-ROSE"]
		self.assertEqual(len(ours), 3)
		self.assertEqual(ours[0]["actual"], 50 if monday < today else 0)
		self.assertAlmostEqual(ours[0]["total"], ours[0]["actual"] + ours[0]["forecast"], delta=1)
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_reports`
Expected: ImportError.

- [ ] **Step 3: Implement the Section Harvest Forecast report**

`section_harvest_forecast.json`:
- Same shape as the accuracy report's JSON.
- `"name": "Section Harvest Forecast"`, `"report_name": "Section Harvest Forecast"`, `"ref_doctype": "Sample Plot"`.
- roles: System Manager, Agriculture Manager, Agriculture User, Stock User, Sales User.

`section_harvest_forecast.js`:
```javascript
frappe.query_reports["Section Harvest Forecast"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "greenhouse", label: __("Greenhouse"), fieldtype: "Link", options: "Warehouse" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item" },
		{ fieldname: "days", label: __("Days"), fieldtype: "Int", default: 14 },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "basis" && data && data.warn) {
			value = `<span style="color: var(--orange-600)">${value}</span>`;
		}
		return value;
	},
};
```

`section_harvest_forecast.py`:
```python
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
```

- [ ] **Step 4: Implement the Weekly Harvest Forecast report**

`weekly_harvest_forecast.json`:
- `"name": "Weekly Harvest Forecast"`, `"report_name": "Weekly Harvest Forecast"`, `"ref_doctype": "Sample Plot"`.
- roles: System Manager, Agriculture Manager, Agriculture User, Stock User, Sales User, Sales Manager.

`weekly_harvest_forecast.js`:
```javascript
frappe.query_reports["Weekly Harvest Forecast"] = {
	filters: [
		{ fieldname: "farm", label: __("Farm"), fieldtype: "Link", options: "Farm" },
		{ fieldname: "variety", label: __("Variety"), fieldtype: "Link", options: "Item", get_query: () => ({ filters: { has_variants: 1 } }) },
		{ fieldname: "weeks", label: __("Weeks"), fieldtype: "Int", default: 3 },
		{ fieldname: "group_by", label: __("Show"), fieldtype: "Select", options: "Variety\nGreenhouse", default: "Variety" },
	],
};
```

`weekly_harvest_forecast.py`:
```python
"""Stems per variety per week for sales: this week's actual picks so far plus
the forecast for the rest of the week, then whole forecast weeks, split by
length. Bands add in quadrature (sections vary independently)."""
import math

from frappe import _
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import data, service
from upande_agriculture.weekcal import get_week_rule, week_key, week_start


def execute(filters=None):
	f = filters or {}
	weeks = max(1, int(f.get("weeks") or 3))
	by_gh = f.get("group_by") == "Greenhouse"
	today = getdate(nowdate())
	rule = get_week_rule()
	first = week_start(*week_key(today, rule), rule)
	end = add_days(first, weeks * 7 - 1)
	forecasts = service.forecast_all(farm=f.get("farm"), variety=f.get("variety"), start=today,
		days=(end - today).days + 1)

	groups = {}

	def group(fc, wk):
		key = (fc["variety"], fc["warehouse_name"] if by_gh else None, wk)
		return groups.setdefault(key, {"forecast": 0.0, "hw2": 0.0, "actual": 0.0, "sections": set(), "warn": set()})

	for fc in forecasts:
		for d in fc["daily"]:
			g = group(fc, week_key(d["date"], rule))
			g["forecast"] += d["stems"]
			g["hw2"] += ((d["high"] - d["low"]) / 2) ** 2
			g["sections"].add((fc["greenhouse"], fc["section"]))
			g["warn"].update(k for k, v in fc["flags"].items() if v)
		if today > first:
			so_far = data.cuts(fc["greenhouse"], fc["section"], first, add_days(today, -1))
			group(fc, week_key(first, rule))["actual"] += sum(so_far.values())

	mixes = {v: data.length_mix(v, today) for v in {k[0] for k in groups}}
	lengths = sorted({ln for m, _b in mixes.values() for ln in m}, key=lambda s: (len(s), s))
	rows = []
	for (variety, gh, wk), g in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
		total = g["actual"] + g["forecast"]
		mix, basis = mixes[variety]
		row = {"variety": variety, "greenhouse": gh or "", "week": f"{wk[0]}-W{wk[1]:02d}",
			"starts": week_start(*wk, rule), "actual": round(g["actual"]), "forecast": round(g["forecast"]),
			"total": round(total), "margin": round(math.sqrt(g["hw2"])), "sections": len(g["sections"]),
			"basis": "; ".join(sorted(g["warn"]) + [f"lengths: {basis}"])}
		for ln in lengths:
			row["len_" + ln] = round(total * mix.get(ln, 0))
		rows.append(row)

	columns = [
		{"fieldname": "variety", "label": _("Variety"), "fieldtype": "Link", "options": "Item", "width": 130},
		{"fieldname": "greenhouse", "label": _("Greenhouse"), "fieldtype": "Data", "width": 120, "hidden": 0 if by_gh else 1},
		{"fieldname": "week", "label": _("Week"), "fieldtype": "Data", "width": 90},
		{"fieldname": "starts", "label": _("Starts"), "fieldtype": "Date", "width": 100},
		{"fieldname": "actual", "label": _("Picked So Far"), "fieldtype": "Int", "width": 105},
		{"fieldname": "forecast", "label": _("Forecast Rest"), "fieldtype": "Int", "width": 105},
		{"fieldname": "total", "label": _("Week Total"), "fieldtype": "Int", "width": 95},
		{"fieldname": "margin", "label": _("± Stems"), "fieldtype": "Int", "width": 80},
		*[{"fieldname": "len_" + ln, "label": ln, "fieldtype": "Int", "width": 75} for ln in lengths],
		{"fieldname": "sections", "label": _("Sections"), "fieldtype": "Int", "width": 75},
		{"fieldname": "basis", "label": _("Basis"), "fieldtype": "Data", "width": 300},
	]
	return columns, rows
```

Delete the replaced report and engine:
```bash
git rm -rq upande_agriculture/upande_agriculture/report/bed_sample_forecast upande_agriculture/bed_forecast.py
```

- [ ] **Step 5: Migrate, run, expect PASS**

Run: `bench --site bhcloud.local migrate >/dev/null && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_reports`
Expected: 2 tests OK. Migrate removes the orphaned `Bed Sample Forecast` report.

- [ ] **Step 6: Commit**

```bash
git add upande_agriculture/upande_agriculture/report/section_harvest_forecast upande_agriculture/upande_agriculture/report/weekly_harvest_forecast upande_agriculture/tests/test_forecast_reports.py
git commit -m "Harvest forecast: daily per-section and weekly per-variety reports

Weekly Harvest Forecast replaces Bed Sample Forecast and bed_forecast.py.

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 11: Photo dataset export (camera training hand-off)

**Files:**
- Create: `upande_agriculture/forecast/dataset.py`
- Test: `upande_agriculture/tests/test_forecast_dataset.py`

**Interfaces:**
- Consumes: Bed Sample `photo_1`, `photo_2` and stage rows
- Produces: `export_stage_dataset(from_date=None) -> {"file_url": str, "images": int}` (whitelisted, System Manager). The zip holds `images/<sample>-<n>.jpg` and `labels.csv`, with columns `image, sample, plot, greenhouse, section, variety, date, plants_counted` plus one column per stage name.

- [ ] **Step 1: Write the failing test**

`upande_agriculture/tests/test_forecast_dataset.py`:
```python
import base64
import csv
import io
import zipfile

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate
from PIL import Image

from upande_agriculture.forecast import api, dataset
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestForecastDataset(FrappeTestCase):
	def test_export_has_images_and_labels(self):
		make_variety()
		gh = make_greenhouse("FC DS", (("S1", 1, 9, "FC-ROSE", 1000),))
		plot = [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == gh][0]["plot"]
		im = io.BytesIO(); Image.new("RGB", (32, 32), (0, 200, 0)).save(im, "JPEG")
		api.submit_plot_count("fc-ds-1", plot, '[{"stage_name": "Pea", "count": 4}]', 10,
			captured_at=f"{nowdate()} 07:00:00", photos=[base64.b64encode(im.getvalue()).decode()])
		out = dataset.export_stage_dataset(nowdate())
		self.assertGreaterEqual(out["images"], 1)
		f = frappe.get_doc("File", {"file_url": out["file_url"]})
		z = zipfile.ZipFile(io.BytesIO(f.get_content()))
		rows = list(csv.DictReader(io.StringIO(z.read("labels.csv").decode())))
		mine = [r for r in rows if r["greenhouse"] == gh]
		self.assertEqual(mine[0]["Pea"], "4")
		self.assertIn(mine[0]["image"], z.namelist())
```

- [ ] **Step 2: Run, expect import failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_dataset`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/dataset.py`:
```python
"""Plot-count photos with their stage counts, zipped for annotation and
training a bud-stage detector. Image-level counts are weak labels: an
annotator draws the boxes, and the counts check that none were missed."""
import csv
import io
import zipfile

import frappe
from frappe.utils import nowdate


@frappe.whitelist()
def export_stage_dataset(from_date=None):
	frappe.only_for("System Manager")
	filters = {"photo_1": ["is", "set"]}
	if from_date:
		filters["sampling_date"] = [">=", from_date]
	samples = frappe.get_all("Bed Sample", filters=filters, order_by="sampling_date",
		fields=["name", "sample_plot", "greenhouse", "section", "variety", "sampling_date",
			"plants_counted", "photo_1", "photo_2"])
	counts = {}
	for r in frappe.get_all("Bed Sample Stage", fields=["parent", "stage_name", "count"],
			filters={"parenttype": "Bed Sample", "parent": ["in", [s.name for s in samples] or [""]]}):
		counts.setdefault(r.parent, {})[r.stage_name] = r.count or 0
	stage_names = sorted({k for c in counts.values() for k in c})

	buf = io.BytesIO()
	rows = []
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
		for s in samples:
			for i, url in enumerate((s.photo_1, s.photo_2), start=1):
				if not url:
					continue
				name = frappe.db.get_value("File", {"file_url": url})
				if not name:
					continue
				arc = f"images/{s.name}-{i}.jpg"
				z.writestr(arc, frappe.get_doc("File", name).get_content())
				rows.append({"image": arc, "sample": s.name, "plot": s.sample_plot or "",
					"greenhouse": s.greenhouse, "section": s.section or "", "variety": s.variety,
					"date": str(s.sampling_date), "plants_counted": s.plants_counted,
					**{k: counts.get(s.name, {}).get(k, 0) for k in stage_names}})
		out = io.StringIO()
		w = csv.DictWriter(out, fieldnames=["image", "sample", "plot", "greenhouse", "section", "variety",
			"date", "plants_counted", *stage_names])
		w.writeheader()
		w.writerows(rows)
		z.writestr("labels.csv", out.getvalue())
	f = frappe.get_doc({"doctype": "File", "file_name": f"stage-dataset-{nowdate()}.zip",
		"content": buf.getvalue(), "is_private": 1}).insert(ignore_permissions=True)
	return {"file_url": f.file_url, "images": len(rows)}
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_dataset`
Expected: 1 test OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/dataset.py upande_agriculture/tests/test_forecast_dataset.py
git commit -m "Harvest forecast: export plot photos with stage counts for detector training

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 12: App types, API calls and the sync queue

**Files (in `/home/teddy5456/bilashaka-harvest`):**
- Modify: `src/types/index.ts`: replace `SamplingCycle`, `SamplingPlan` and `BedSamplePayload`
- Modify: `src/services/api.ts`: replace `getSamplingPlan` and `submitBedSample`
- Modify: `src/services/sync.ts`: replace the `bedSample` action with `plotCount`

**Interfaces:**
- Produces (TypeScript):
```ts
export interface PlotRow {
  plot: string; title: string; greenhouse: string; greenhouse_name: string; section: string;
  variety: string; plants: number; bed: number | null; plot_no: number;
  stages: { stage_name: string; days_to_harvest: number }[];
  last_counted: string | null; due: boolean; mine: boolean;
}
export interface PlotPlan { count_interval_days: number; plots: PlotRow[] }
export interface PlotCountPayload {
  client_uuid: string; sample_plot: string; plants_counted: number;
  counts: string; // JSON [{stage_name, count}]
  notes?: string; captured_at: string; photos?: string[];
}
export interface BayForecast {
  greenhouse: string; section: string; variety: string; plants: number;
  today: number; tomorrow: number; next7: number; low7: number; high7: number;
  daily: { date: string; stems: number; low: number; high: number }[];
  flags: { no_plants: boolean; no_count: boolean; stale_count: boolean };
  last_count: string | null;
}
```
  - `getPlotPlan(farm: string): Promise<PlotPlan>`
  - `submitPlotCount(p: PlotCountPayload): Promise<{ name: string }>`
  - `getBayForecast(greenhouse: string, section: string, days?: number): Promise<BayForecast>`
  - sync action `'plotCount'` → `sendPlotCount(job)`, exported from `PlotCountScreen.tsx` in Task 13.

- [ ] **Step 1: Types.** In `src/types/index.ts`, delete the `SamplingCycle`, `SamplingPlan` and `BedSamplePayload` interfaces and add the four interfaces above.

- [ ] **Step 2: API.** In `src/services/api.ts`:
  - Remove `getSamplingPlan` and `submitBedSample`, and their imports `SamplingPlan` and `BedSamplePayload`.
  - Import `PlotPlan, PlotCountPayload, BayForecast`.
  - Add:
```ts
// Harvest forecast (upande_agriculture.forecast.api): permanent sample plots,
// counted by the section's own harvester, and the bay forecast built on them.
export async function getPlotPlan(farm: string): Promise<PlotPlan> {
  const res = await apiPost<{ message?: PlotPlan }>('upande_agriculture.forecast.api.get_plot_plan', { farm });
  return (res as any).message ?? res;
}

export async function submitPlotCount(payload: PlotCountPayload): Promise<{ name: string }> {
  const res = await apiPost<{ message?: { name: string } }>('upande_agriculture.forecast.api.submit_plot_count', payload);
  return (res as any).message ?? res;
}

export async function getBayForecast(greenhouse: string, section: string, days = 7): Promise<BayForecast> {
  const res = await apiPost<{ message?: BayForecast }>('upande_agriculture.forecast.api.get_bay_forecast', { greenhouse, section, days });
  return (res as any).message ?? res;
}
```

- [ ] **Step 3: Sync.** In `src/services/sync.ts`:
  - Replace `import { sendBedSample } from '../screens/agriculture/BedSamplingScreen';` with `import { sendPlotCount } from '../screens/agriculture/PlotCountScreen';`.
  - Replace the branch
```ts
      } else if (entry.action === 'bedSample') {
        await sendBedSample(payload);
```
with
```ts
      } else if (entry.action === 'plotCount') {
        await sendPlotCount(payload);
```

- [ ] **Step 4: No commit yet.** `sync.ts` imports `PlotCountScreen`, which Task 13 creates, so Tasks 12 and 13 type-check together at the end of Task 13.

---

### Task 13: App Plot Count screen and navigation

**Files:**
- Create: `src/screens/agriculture/PlotCountScreen.tsx`
- Delete: `src/screens/agriculture/BedSamplingScreen.tsx`
- Modify: `App.tsx` (stack screen `BedSampling` → `PlotCount`), `src/screens/AgricultureScreen.tsx` (menu entry)

**Interfaces:**
- Consumes: `getPlotPlan`, `submitPlotCount`, `PlotPlan`, `PlotRow`, `PlotCountPayload` (Task 12); `BayForecastCard` (Task 14). Task 14 creates it, so build Task 14's component file before type-checking this task.
- Produces: `export default function PlotCountScreen()` and `export async function sendPlotCount(job: Omit<PlotCountPayload, 'photos'> & { photo_uris?: string[] })`.

- [ ] **Step 1: Write the screen**

`src/screens/agriculture/PlotCountScreen.tsx`:
```tsx
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, TextInput, Image, Modal } from 'react-native';
import { CameraView, useCameraPermissions } from 'expo-camera';
import * as FileSystem from 'expo-file-system/legacy';
import { Ionicons } from '@expo/vector-icons';
import { useApp } from '../../context/AppContext';
import ScanConfirmation from '../../components/ScanConfirmation';
import BayForecastCard from '../../components/BayForecastCard';
import { addToSyncQueue } from '../../database/sync-queue';
import { getFarm, getSetting, setSetting } from '../../database/settings';
import { getPlotPlan, submitPlotCount } from '../../services/api';
import { PlotPlan, PlotRow, PlotCountPayload } from '../../types';
import { onScanSuccess, onScanError } from '../../utils/feedback';
import { colors, fontFamily, fontSize, spacing, borderRadius, scale } from '../../theme';

// Plot counts for the harvest forecast. Each section has 1-2 permanent plots
// of 10 plants (tag on the first plant). The section's own harvester counts
// them every few days on their rounds: tap + for every flower bud by stage,
// one photo, save. Counting the SAME plants each time shows how fast buds move
// to harvest; the ERP turns the counts into each bay's daily forecast. Saving
// goes through the sync queue, so it works with no signal.

const PHOTO_DIR = `${FileSystem.documentDirectory}plot-counts/`;
const TARGET_LONG_SIDE = 1600;
const MAX_PHOTOS = 2;

function nowStamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export default function PlotCountScreen() {
  const { isConnected, triggerSync, refreshStats } = useApp();
  const [plan, setPlan] = useState<PlotPlan | null>(null);
  const [plotName, setPlotName] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [plants, setPlants] = useState('10');
  const [photos, setPhotos] = useState<string[]>([]);
  const [notes, setNotes] = useState('');
  const [doneToday, setDoneToday] = useState<string[]>([]);
  const [cameraOpen, setCameraOpen] = useState(false);
  const [pictureSize, setPictureSize] = useState<string | undefined>(undefined);
  const [confirm, setConfirm] = useState<{ visible: boolean; type: 'success' | 'error' | 'queued'; message: string }>(
    { visible: false, type: 'success', message: '' });
  const [permission, requestPermission] = useCameraPermissions();
  const cameraRef = useRef<CameraView>(null);

  useEffect(() => {
    (async () => {
      const cached = await getSetting('plot_plan');
      if (cached) setPlan(JSON.parse(cached));
      try {
        const fresh = await getPlotPlan((await getFarm()) ?? '');
        setPlan(fresh);
        await setSetting('plot_plan', JSON.stringify(fresh));
      } catch {}
    })();
  }, []);

  const plot = plan?.plots.find((p) => p.plot === plotName);
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const done = (p: PlotRow) => doneToday.includes(p.plot);

  const openPlot = (p: PlotRow) => {
    setPlotName(p.plot); setCounts({}); setPhotos([]); setNotes(''); setPlants(String(p.plants || 10));
  };
  const bump = (stage: string, by: number) =>
    setCounts((c) => ({ ...c, [stage]: Math.max(0, (c[stage] ?? 0) + by) }));

  const pickPictureSize = useCallback(async () => {
    try {
      const sizes = (await cameraRef.current?.getAvailablePictureSizesAsync()) ?? [];
      const parsed = sizes.map((sz) => ({ sz, long: Math.max(...sz.split('x').map(Number)) }))
        .filter((p) => p.long > 0).sort((a, b) => a.long - b.long);
      const pick = parsed.find((p) => p.long >= TARGET_LONG_SIDE) ?? parsed[parsed.length - 1];
      if (pick) setPictureSize(pick.sz);
    } catch {}
  }, []);

  const openCamera = useCallback(async () => {
    if (!permission?.granted && !(await requestPermission()).granted) {
      setConfirm({ visible: true, type: 'error', message: 'Camera permission is needed for plot photos.' });
      return;
    }
    setCameraOpen(true);
  }, [permission, requestPermission]);

  const takePhoto = useCallback(async () => {
    const pic = await cameraRef.current?.takePictureAsync({ quality: 0.6 });
    if (!pic) return;
    await FileSystem.makeDirectoryAsync(PHOTO_DIR, { intermediates: true }).catch(() => {});
    const dest = `${PHOTO_DIR}${Date.now()}.jpg`;
    await FileSystem.moveAsync({ from: pic.uri, to: dest });
    setPhotos((p) => [...p, dest].slice(0, MAX_PHOTOS));
    setCameraOpen(false);
  }, []);

  const removePhoto = (uri: string) => {
    FileSystem.deleteAsync(uri, { idempotent: true });
    setPhotos((p) => p.filter((x) => x !== uri));
  };

  const save = useCallback(async () => {
    if (!plot) return;
    const plantCount = parseInt(plants, 10);
    const problem = !(plantCount > 0) ? 'Enter how many plants you counted.'
      : photos.length === 0 ? 'Take one photo of the counted plants first. The photos teach the camera to count buds.'
      : '';
    if (problem) {
      onScanError();
      setConfirm({ visible: true, type: 'error', message: problem });
      return;
    }
    const job = {
      client_uuid: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`,
      sample_plot: plot.plot,
      plants_counted: plantCount,
      counts: JSON.stringify(plot.stages.map((s) => ({ stage_name: s.stage_name, count: counts[s.stage_name] ?? 0 }))),
      notes: notes.trim() || undefined,
      captured_at: nowStamp(),
      photo_uris: photos,
    };
    try {
      await addToSyncQueue('plotCount', job);
      setDoneToday((d) => [...d, plot.plot]);
      setPlotName(null);
      onScanSuccess();
      setConfirm({
        visible: true,
        type: isConnected ? 'success' : 'queued',
        message: isConnected ? `${plot.title}: ${total} buds saved.` : `No signal. ${plot.title} is saved on the phone and sends when it reconnects.`,
      });
      if (isConnected) triggerSync().catch(() => {});
      refreshStats().catch(() => {});
    } catch (e: any) {
      onScanError();
      setConfirm({ visible: true, type: 'error', message: e?.message ?? 'Could not save' });
    }
  }, [plot, plants, photos, counts, notes, total, isConnected, triggerSync, refreshStats]);

  const camera = (
    <Modal visible={cameraOpen} animationType="slide" onRequestClose={() => setCameraOpen(false)}>
      <View style={styles.cameraWrap}>
        <CameraView ref={cameraRef} style={StyleSheet.absoluteFill} facing="back" pictureSize={pictureSize} onCameraReady={pickPictureSize} />
        <Text style={styles.cameraTitle}>The plants you counted, side on</Text>
        <View style={styles.cameraBar}>
          <TouchableOpacity onPress={() => setCameraOpen(false)} style={styles.cameraBtn}>
            <Ionicons name="close" size={28} color="#fff" />
          </TouchableOpacity>
          <TouchableOpacity onPress={takePhoto} style={styles.shutter} />
          <View style={styles.cameraBtn} />
        </View>
      </View>
    </Modal>
  );
  const confirmCard = (
    <ScanConfirmation visible={confirm.visible} type={confirm.type} message={confirm.message}
      onDismiss={() => setConfirm((c) => ({ ...c, visible: false }))} />
  );

  if (plot) {
    return (
      <ScrollView style={styles.container} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <TouchableOpacity onPress={() => setPlotName(null)} style={styles.back}>
          <Ionicons name="chevron-back" size={20} color={colors.text} />
          <Text style={styles.backText}>All plots</Text>
        </TouchableOpacity>
        <View style={styles.card}>
          <Text style={styles.cardMain}>{plot.title}</Text>
          <Text style={styles.sub}>{plot.variety}{plot.bed ? ` · bed ${plot.bed}` : ''} · start at the tagged plant</Text>
        </View>
        <BayForecastCard greenhouse={plot.greenhouse} section={plot.section} />

        <Text style={styles.label}>Plants counted</Text>
        <View style={styles.row}>
          <TouchableOpacity style={styles.stepBtn} onPress={() => setPlants((n) => String(Math.max(1, (parseInt(n, 10) || 1) - 1)))}>
            <Ionicons name="remove" size={22} color={colors.text} />
          </TouchableOpacity>
          <TextInput style={styles.plantsInput} value={plants} onChangeText={setPlants} keyboardType="number-pad" />
          <TouchableOpacity style={styles.stepBtn} onPress={() => setPlants((n) => String((parseInt(n, 10) || 0) + 1))}>
            <Ionicons name="add" size={22} color={colors.text} />
          </TouchableOpacity>
        </View>

        <Text style={styles.label}>Flower buds by stage (one tap per bud)</Text>
        {plot.stages.map((s) => (
          <View key={s.stage_name} style={styles.stage}>
            <View style={{ flex: 1 }}>
              <Text style={styles.stageName}>{s.stage_name}</Text>
              <Text style={styles.sub}>~{s.days_to_harvest} days to cut</Text>
            </View>
            <TouchableOpacity style={styles.countBtn} onPress={() => bump(s.stage_name, -1)}>
              <Ionicons name="remove" size={26} color={colors.text} />
            </TouchableOpacity>
            <TextInput style={styles.countValue} value={String(counts[s.stage_name] ?? 0)} keyboardType="number-pad" selectTextOnFocus
              onChangeText={(t) => setCounts((c) => ({ ...c, [s.stage_name]: Math.max(0, parseInt(t, 10) || 0) }))} />
            <TouchableOpacity style={[styles.countBtn, styles.countPlus]} onPress={() => bump(s.stage_name, 1)}>
              <Ionicons name="add" size={30} color={colors.textOnPrimary} />
            </TouchableOpacity>
          </View>
        ))}
        <Text style={styles.total}>Total buds: {total}</Text>

        <Text style={styles.label}>Photos ({photos.length}/{MAX_PHOTOS}, at least one)</Text>
        <View style={styles.row}>
          {photos.map((p) => (
            <TouchableOpacity key={p} onLongPress={() => removePhoto(p)} style={styles.thumb}>
              <Image source={{ uri: p }} style={styles.thumbImg} />
            </TouchableOpacity>
          ))}
          {photos.length < MAX_PHOTOS && (
            <TouchableOpacity style={[styles.thumb, styles.thumbAdd]} onPress={openCamera}>
              <Ionicons name="camera-outline" size={28} color={colors.textMuted} />
            </TouchableOpacity>
          )}
        </View>
        {photos.length > 0 && <Text style={styles.sub}>Long-press a photo to remove it.</Text>}

        <Text style={styles.label}>Notes (optional)</Text>
        <TextInput style={styles.notes} value={notes} onChangeText={setNotes} multiline
          placeholder="Disease, damage, blind shoots…" placeholderTextColor={colors.textMuted} />

        <TouchableOpacity style={styles.saveBtn} onPress={save}>
          <Text style={styles.saveText}>Save count</Text>
        </TouchableOpacity>
        {camera}
        {confirmCard}
      </ScrollView>
    );
  }

  const groups: Record<string, PlotRow[]> = {};
  for (const p of plan?.plots ?? []) (groups[p.greenhouse_name] ??= []).push(p);
  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      {!plan && <Text style={styles.sub}>Loading plots… (needs signal the first time)</Text>}
      {plan && plan.plots.length === 0 && (
        <Text style={styles.sub}>No plots yet. Sections need a variety in the ERP (Greenhouse → Sections).</Text>
      )}
      {plan && <Text style={styles.sub}>Count each plot every {plan.count_interval_days} days. Your sections are listed first.</Text>}
      {Object.entries(groups).map(([gh, rows]) => (
        <View key={gh}>
          <Text style={styles.label}>{gh}</Text>
          {rows.map((p) => (
            <TouchableOpacity key={p.plot} style={styles.plotRow} onPress={() => openPlot(p)}>
              <View style={{ flex: 1 }}>
                <Text style={styles.stageName}>{p.section} · plot {p.plot_no}{p.mine ? '  ★' : ''}</Text>
                <Text style={styles.sub}>{p.variety}{p.bed ? ` · bed ${p.bed}` : ''} · {p.last_counted ? `last ${p.last_counted}` : 'never counted'}</Text>
              </View>
              {done(p) ? <Ionicons name="checkmark-circle" size={22} color={colors.success} />
                : p.due ? <Text style={styles.due}>Due</Text> : null}
            </TouchableOpacity>
          ))}
        </View>
      ))}
      {confirmCard}
    </ScrollView>
  );
}

// Run by the sync queue: send the count with its photos, then delete them.
export async function sendPlotCount(job: Omit<PlotCountPayload, 'photos'> & { photo_uris?: string[] }) {
  const { photo_uris = [], ...rest } = job;
  const photos = await Promise.all(
    photo_uris.map((uri) => FileSystem.readAsStringAsync(uri, { encoding: FileSystem.EncodingType.Base64 }).catch(() => null)),
  );
  const res = await submitPlotCount({ ...rest, photos: photos.filter((p): p is string => !!p) });
  await Promise.all(photo_uris.map((uri) => FileSystem.deleteAsync(uri, { idempotent: true })));
  return res;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: spacing.lg, gap: spacing.sm, paddingBottom: spacing.xl * 2 },
  label: { fontFamily: fontFamily.semiBold, fontSize: fontSize.sm, color: colors.textSecondary, marginTop: spacing.sm },
  sub: { fontFamily: fontFamily.regular, fontSize: fontSize.sm, color: colors.textMuted },
  card: { backgroundColor: colors.surface, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md },
  cardMain: { fontFamily: fontFamily.semiBold, fontSize: fontSize.lg, color: colors.text },
  back: { flexDirection: 'row', alignItems: 'center', gap: spacing.xs, minHeight: 36 },
  backText: { fontFamily: fontFamily.medium, fontSize: fontSize.md, color: colors.text },
  plotRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, backgroundColor: colors.surface, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, marginTop: spacing.xs, minHeight: 56 },
  due: { fontFamily: fontFamily.semiBold, fontSize: fontSize.sm, color: colors.warning },
  row: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, flexWrap: 'wrap' },
  stepBtn: { width: 44, height: 44, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, alignItems: 'center', justifyContent: 'center' },
  plantsInput: { width: 80, height: 44, textAlign: 'center', borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, fontFamily: fontFamily.semiBold, fontSize: fontSize.lg, color: colors.text },
  stage: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, backgroundColor: colors.surface, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.sm },
  stageName: { fontFamily: fontFamily.semiBold, fontSize: fontSize.md, color: colors.text },
  countBtn: { width: scale(56), height: scale(56), borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surfaceAlt },
  countPlus: { backgroundColor: colors.primary, borderColor: colors.primary },
  countValue: { width: scale(56), textAlign: 'center', fontFamily: fontFamily.bold, fontSize: fontSize.xl, color: colors.text },
  total: { fontFamily: fontFamily.semiBold, fontSize: fontSize.md, color: colors.text, textAlign: 'right' },
  thumb: { width: scale(96), height: scale(96), borderRadius: borderRadius.md, overflow: 'hidden', borderWidth: 1, borderColor: colors.border },
  thumbImg: { width: '100%', height: '100%' },
  thumbAdd: { borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', backgroundColor: colors.surface },
  notes: { minHeight: 64, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, backgroundColor: colors.surface, padding: spacing.sm, fontFamily: fontFamily.regular, fontSize: fontSize.md, color: colors.text, textAlignVertical: 'top' },
  saveBtn: { marginTop: spacing.md, minHeight: 52, borderRadius: borderRadius.md, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  saveText: { fontFamily: fontFamily.semiBold, fontSize: fontSize.md, color: colors.textOnPrimary },
  cameraWrap: { flex: 1, backgroundColor: '#000' },
  cameraTitle: { position: 'absolute', top: 48, left: 0, right: 0, textAlign: 'center', color: '#fff', fontFamily: fontFamily.semiBold, fontSize: fontSize.md, textShadowColor: '#000', textShadowRadius: 4 },
  cameraBar: { position: 'absolute', bottom: 40, left: 0, right: 0, flexDirection: 'row', justifyContent: 'space-around', alignItems: 'center' },
  cameraBtn: { width: 48, height: 48, alignItems: 'center', justifyContent: 'center' },
  shutter: { width: 72, height: 72, borderRadius: 36, backgroundColor: '#fff', borderWidth: 4, borderColor: '#ccc' },
});
```

- [ ] **Step 2: Navigation**
  - In `App.tsx`, replace `import BedSamplingScreen from './src/screens/agriculture/BedSamplingScreen';` with `import PlotCountScreen from './src/screens/agriculture/PlotCountScreen';`.
  - In the `AgricultureStack.Screen` with `name="BedSampling"`, change `name` to `"PlotCount"`, `component` to `{PlotCountScreen}`, and the options title to `'Plot Count'`.
  - In `src/screens/AgricultureScreen.tsx`, change the entry with `key: 'BedSampling'` to `key: 'PlotCount'`, `label: 'Plot Count'`, `description: 'Count your section's sample plots'`, `navigateTo: { route: 'PlotCount' }`.
  - Delete the old screen: `rm src/screens/agriculture/BedSamplingScreen.tsx`.

- [ ] **Step 3: Type-check after Task 14's component exists**

Run: `cd /home/teddy5456/bilashaka-harvest && npx tsc --noEmit -p . 2>&1 | grep -v vector-icons | grep -E "PlotCount|BayForecast|services/(api|sync)|types/index|App.tsx|AgricultureScreen|PhotoHarvest"; echo done`
Expected: only `done`. Errors in other files (Skeleton, GradeScreen, HarvestScreen, `@expo/vector-icons`) were there before this work.

---

### Task 14: Bay Forecast card on the plot and harvest screens

**Files:**
- Create: `src/components/BayForecastCard.tsx`
- Modify: `src/screens/PhotoHarvestScreen.tsx`

**Interfaces:**
- Consumes: `getBayForecast`, `BayForecast` (Task 12)
- Produces: `export default function BayForecastCard({ greenhouse, section }: { greenhouse: string; section: string })`. It renders nothing while loading, offline or on error.

- [ ] **Step 1: Write the card**

`src/components/BayForecastCard.tsx`:
```tsx
import React, { useEffect, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { useApp } from '../context/AppContext';
import { getBayForecast } from '../services/api';
import { BayForecast } from '../types';
import { colors, fontFamily, fontSize, spacing, borderRadius } from '../theme';

// "Your bay": what this section should give today, tomorrow and this week,
// from its plot counts and its own regrowth (upande_agriculture forecast).
// Quietly absent offline or when the ERP has no forecast module.
export default function BayForecastCard({ greenhouse, section }: { greenhouse: string; section: string }) {
  const { isConnected } = useApp();
  const [f, setF] = useState<BayForecast | null>(null);

  useEffect(() => {
    let live = true;
    setF(null);
    if (!isConnected || !greenhouse || !section) return;
    getBayForecast(greenhouse, section).then((r) => live && setF(r)).catch(() => {});
    return () => { live = false; };
  }, [greenhouse, section, isConnected]);

  if (!f) return null;
  const note = f.flags.no_plants ? 'Plants not set for this section, so only regrowth is counted.'
    : f.flags.no_count ? 'No plot count yet: counting the plots makes this accurate.'
    : f.flags.stale_count ? `Last plot count ${f.last_count}. Count again for a sharper forecast.`
    : null;
  return (
    <View style={styles.card}>
      <Text style={styles.title}>Expected from {section}</Text>
      <View style={styles.row}>
        <Stat label="Today" value={f.today} />
        <Stat label="Tomorrow" value={f.tomorrow} />
        <Stat label="Next 7 days" value={f.next7} sub={`${f.low7}–${f.high7}`} />
      </View>
      {note && <Text style={styles.note}>{note}</Text>}
    </View>
  );
}

function Stat({ label, value, sub }: { label: string; value: number; sub?: string }) {
  return (
    <View style={styles.stat}>
      <Text style={styles.value}>~{value}</Text>
      <Text style={styles.label}>{label}</Text>
      {sub && <Text style={styles.label}>{sub}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { backgroundColor: colors.surface, borderRadius: borderRadius.md, borderWidth: 1, borderColor: colors.border, padding: spacing.md, gap: spacing.xs },
  title: { fontFamily: fontFamily.semiBold, fontSize: fontSize.sm, color: colors.textSecondary },
  row: { flexDirection: 'row', justifyContent: 'space-between' },
  stat: { alignItems: 'center', flex: 1 },
  value: { fontFamily: fontFamily.bold, fontSize: fontSize.xl, color: colors.text },
  label: { fontFamily: fontFamily.regular, fontSize: fontSize.xs, color: colors.textMuted },
  note: { fontFamily: fontFamily.regular, fontSize: fontSize.xs, color: colors.warning },
});
```

- [ ] **Step 2: Show it on the harvest screen**

In `src/screens/PhotoHarvestScreen.tsx`:
- Add `import BayForecastCard from '../components/BayForecastCard';` after the `Dropdown` import.
- Immediately after the section card block
```tsx
      {section && (
        <View style={styles.card}>
          <Text style={styles.cardMain}>{section.variety ?? 'No variety set on this section'}</Text>
          <Text style={styles.cardSub}>{section.employee_name}{section.employee ? ` · ${section.employee}` : ''}</Text>
        </View>
      )}
```
  add
```tsx
      {section && greenhouse && <BayForecastCard greenhouse={greenhouse.name} section={section.section_name} />}
```

- [ ] **Step 3: Type-check Tasks 12–14 together**

Run: `cd /home/teddy5456/bilashaka-harvest && npx tsc --noEmit -p . 2>&1 | grep -v vector-icons | grep -E "PlotCount|BayForecast|services/(api|sync)|types/index|App.tsx|AgricultureScreen|PhotoHarvest"; grep -rn "BedSampling\|SamplingPlan\|submitBedSample\|getSamplingPlan" src App.tsx; echo done`
Expected: only `done`.

---

### Task 15: Ship

**Files:** none new.

- [ ] **Step 1: Full test run on bhcloud**

```bash
cd /home/teddy5456/frappe-bench && bench --site bhcloud.local migrate >/dev/null
for m in test_forecast_doctypes test_forecast_data test_forecast_service test_forecast_calibrate test_forecast_jobs test_forecast_api test_forecast_reports test_forecast_dataset; do
  bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.$m 2>&1 | tail -3; done
cd apps/upande_agriculture && ../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_curves upande_agriculture.tests.test_forecast_engine
```
Expected: every module OK.

- [ ] **Step 2: Check the cloud prerequisites with FAC (read-only)**

With `mcp__bilashaka__run_python_code`:
```python
print(frappe.get_installed_apps())
print(frappe.get_meta("Stock Entry").has_field("custom_section"))
```
Expected: `custom_section` True. upande_agriculture appears only after Teddy installs it.

- [ ] **Step 3: Push the branches**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture && git status --short | grep -v "^??" ; git log --oneline origin/bilashaka..HEAD; git push -q origin bilashaka
```
`git status` must still show only the untouched `production_plan_*` changes (and `coffee_plan.py` untracked).

- [ ] **Step 4: OTA the app**

```bash
cd /home/teddy5456/bilashaka-harvest && NODE_OPTIONS="--dns-result-order=ipv4first --network-family-autoselection-attempt-timeout=10000" npx eas update --channel preview --message "Plot counts and the bay forecast" --non-interactive 2>&1 | tail -6
```
Expected: `Published!` with Runtime version 1.1.0.

- [ ] **Step 5: Record in memory**

Append to `/home/teddy5456/.claude/projects/-home-teddy5456-frappe-bench/memory/project_bilashaka.md`:
- the forecast package layout;
- the defaults;
- the calibration rule (3 rounds, 200 stems);
- the cloud prerequisites still open: install upande_agriculture, fill `custom_plants` per section, temperature logs optional.
