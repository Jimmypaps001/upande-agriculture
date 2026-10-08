# Bilashaka Harvest Forecast, Part 2: Camera, Bay Imagery and Learned Models

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Part 1 (`2026-10-08-bilashaka-harvest-forecast.md`) must be complete first.** This plan builds on its `forecast/` package, doctypes and app screens.

**Goal:** Close the spec's remaining layers:
- a bud-stage detector trained from Bilashaka's plot photos, with camera-guided counting on the phone;
- full-bay imagery (phone walk scans and drone photos) that corrects how representative each section's plots are;
- a learned correction model and a GRU sequence model on top of the physical forecast. Each switches itself on only when it beats the physical forecast on held-out weeks.

**Architecture:** Frappe Cloud runs no torch, so:
- detection runs **on the phone** (TFLite, the same path as the bucket bud counter);
- labelling and training run on **Kaggle** (the same pipeline as the bud counter, now with 5 stage classes);
- drone photos uploaded in Desk are processed by an **off-cloud script** that calls the ERP API;
- the learned forecast models run in **numpy** inside the ERP: ridge regression on residuals, and a GRU forward pass with weights trained offline in torch.

**Tech Stack:** Frappe v16, numpy 2.x (present on the cloud), Expo/React Native with react-native-fast-tflite, Ultralytics 8.4.80 + OWLv2 (transformers) on Kaggle T4, torch 2.x in `/home/teddy5456/upande-vision/venv`.

**Spec:** `docs/superpowers/specs/2026-10-08-bilashaka-harvest-forecast.md` (Part 2 section)

## Global Constraints
- Every Part 1 global constraint applies (branches, never-commit files, test commands, OTA command).
- No torch or ultralytics inside the Frappe app. The ERP side uses numpy only.
- **Stage class order everywhere:** `["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]` (class ids 0–4). The detector, the phone decoder, the gate and the scan processor must all use this list.
- **Learned layers are gated:**
  - The residual model is used only if, on the last 30% of rounds (walk-forward), it cuts the 3-day-sum MAPE by ≥5% relative to the physical forecast.
  - The sequence model is used only for the horizon buckets where its validation MAPE beats the physical forecast's.
  - Both are also behind switches in Agriculture Settings.
- **Camera guidance is enabled only if the gate passes:** stage-share MAE ≤ 0.15 and Spearman ρ ≥ 0.6 between detections and counted totals on held-out photos.
- **Scan factor** = (mean detections per frame of processed bay scans, last 14 days) ÷ (mean camera detections per plot photo, same window), clipped to [0.5, 1.5]. It applies to the counted-bud part only. Without both inputs it's 1.0.
- Kaggle: CLI `/home/teddy5456/upande-vision/train/exportenv2/bin/kaggle` with `export KAGGLE_API_TOKEN=$(cat ~/.kaggle/access_token)`. The dataset mount path is found by globbing.
- **ERP API scripts** read `ERP_URL`, `ERP_KEY`, `ERP_SECRET` from the environment (an API key pair of a System Manager user). Never commit keys.

## Review Focus
1. **No stage model attached:**
   - the Plot Count screen behaves exactly as in Part 1 (no camera suggestions, no error);
   - the walk scan button explains it needs the camera model.
   Pinned in Task 19 (the `get_camera_config` test returns empty) and Task 20 (app guard).
2. **A camera suggestion the worker changes:** the saved counts are the worker's, and `camera_counts` keeps what the camera said. Pinned in Task 19's API test.
3. **Residual or sequence model never good enough:** `residual_enabled` stays 0, and the sequence model applies to no bucket. The forecast is then identical to Part 1's. Pinned in Task 22 (noise-only case) and Task 23 (gating test).
4. **A bay scan without plot photos with camera counts:** the scan factor is 1.0 and the forecast is unchanged. Pinned in Task 21.
5. **A drone scan with unreadable or missing images:** the scan goes to `Failed` with the reason, and the others continue. Pinned in Task 20 (`post_scan_result` with an error).

---

## File Structure

| File | Responsibility |
|---|---|
| `upande_agriculture/upande_agriculture/doctype/agriculture_settings/agriculture_settings.json` | (modify) Camera and learned-model switches |
| `upande_agriculture/upande_agriculture/doctype/bay_imagery_scan/*` | One full-bay scan: phone walk or drone photos, with its stage density |
| `upande_agriculture/upande_agriculture/doctype/bed_sample/bed_sample.json` | (modify) `camera_counts` |
| `upande_agriculture/upande_agriculture/doctype/harvest_forecast_calibration/*.json` | (modify) `residual_model`, `residual_gain`, `residual_enabled` |
| `upande_agriculture/forecast/camera.py` | Camera config, scan endpoints, scan factor |
| `upande_agriculture/forecast/residual.py` | Ridge correction on log residuals: features, fit, walk-forward gate, apply |
| `upande_agriculture/forecast/sequence.py` | numpy GRU forward pass, history export, apply |
| `upande_agriculture/forecast/service.py` | (modify) Applies the scan factor, residual and sequence |
| `upande_agriculture/forecast/calibrate.py` | (modify) Fits the residual model after the physical fit |
| `upande_agriculture/forecast/api.py` | (modify) `submit_plot_count(camera_counts=...)` |
| `/home/teddy5456/upande-vision/train/stage_label.py` | OWLv2 bud boxes + count-consistent stage assignment → YOLO dataset |
| `/home/teddy5456/upande-vision/train/check_stage_model.py` | Gate: stage-share MAE and total-count ρ on held-out photos |
| `/home/teddy5456/upande-vision/train/erp_client.py` | Minimal ERP API client (key/secret) for the scripts below |
| `/home/teddy5456/upande-vision/train/fetch_stage_dataset.py` | Pull the photo dataset, refresh the Kaggle dataset |
| `/home/teddy5456/upande-vision/train/attach_stage_model.py` | Upload a gated model to Agriculture Settings |
| `/home/teddy5456/upande-vision/train/process_bay_scans.py` | Off-cloud processor for drone/Desk scans |
| `/home/teddy5456/upande-vision/train/seq_forecast.py` | Offline GRU trainer → JSON weights |
| `/home/teddy5456/upande-vision/kaggle/stage_job/{run.py,kernel-metadata.json}` | Kaggle training job for the stage detector |
| `/home/teddy5456/upande-vision/train/tests/test_stage_tools.py` | Pure tests for labelling and gate maths |
| App `src/services/budCounter.ts` | (modify) Shared model load, letterbox, NMS |
| App `src/services/stageCounter.ts` | Multi-class stage detection |
| App `src/screens/agriculture/PlotCountScreen.tsx` | (modify) Camera-guided counts, walk scan |
| App `src/services/api.ts`, `src/types/index.ts`, `src/services/sync.ts` | (modify) Camera config, bay scan |

---

### Task 16: Settings, fields and the Bay Imagery Scan doctype

**Files:**
- Modify: `agriculture_settings/agriculture_settings.json`, `bed_sample/bed_sample.json`, `harvest_forecast_calibration/harvest_forecast_calibration.json`
- Create: `bay_imagery_scan/{__init__.py,bay_imagery_scan.json,bay_imagery_scan.py}`
- Test: `upande_agriculture/tests/test_forecast_imagery_doctypes.py`

**Interfaces:**
- Produces:
  - Agriculture Settings: `stage_model` (Attach), `stage_threshold` (Float, default 0.25), `camera_guided_counts` (Check), `learned_correction` (Check, default 1), `sequence_model` (Attach).
  - `Bed Sample.camera_counts` (Code JSON `{"frames": n, "counts": {stage: n}, "total": n}`).
  - Calibration: `residual_model` (Code JSON), `residual_gain` (Float, the relative MAPE reduction 0–1), `residual_enabled` (Check).
  - Bay Imagery Scan (`SCAN-#####`):
    - fields `greenhouse, section, variety, source (Phone walk|Drone|Other), scan_date, status (Pending|Processed|Failed), frames, stage_density (JSON {stage: detections per frame}), total_per_frame, client_uuid (unique), notes, error`;
    - `validate()` fills `variety` from the section and `total_per_frame` = the sum of `stage_density`.

- [ ] **Step 1: Write the failing test**

`upande_agriculture/tests/test_forecast_imagery_doctypes.py`:
```python
import json

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestImageryDoctypes(FrappeTestCase):
	def test_scan_totals_density_and_takes_variety(self):
		make_variety()
		gh = make_greenhouse("FC IMG", (("S1", 1, 9, "FC-ROSE", 1000),))
		doc = frappe.get_doc({"doctype": "Bay Imagery Scan", "greenhouse": gh, "section": "S1", "source": "Drone",
			"stage_density": json.dumps({"Rice": 2.5, "Opening": 0.5}), "frames": 12}).insert(ignore_permissions=True)
		self.assertEqual((doc.variety, doc.total_per_frame, doc.status), ("FC-ROSE", 3.0, "Pending"))

	def test_unknown_section_refused(self):
		make_variety()
		gh = make_greenhouse("FC IMG", (("S1", 1, 9, "FC-ROSE", 1000),))
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc({"doctype": "Bay Imagery Scan", "greenhouse": gh, "section": "NOPE"}).insert(ignore_permissions=True)

	def test_settings_and_fields_exist(self):
		meta = frappe.get_meta("Agriculture Settings")
		for f in ("stage_model", "stage_threshold", "camera_guided_counts", "learned_correction", "sequence_model"):
			self.assertTrue(meta.has_field(f), f)
		self.assertTrue(frappe.get_meta("Bed Sample").has_field("camera_counts"))
		self.assertTrue(frappe.get_meta("Harvest Forecast Calibration").has_field("residual_enabled"))
```

- [ ] **Step 2: Run, expect failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_imagery_doctypes`
Expected: FAIL (`DocType Bay Imagery Scan not found`).

- [ ] **Step 3: Edit the JSONs (one script, keeps formatting)**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture/upande_agriculture/upande_agriculture/doctype && python3 - <<'EOF'
import json, os
TS = "2026-10-08 15:00:00"
def edit(p, after, fields):
    j = json.load(open(p)); fo = j["field_order"]
    i = fo.index(after) + 1 if after else len(fo)
    fo[i:i] = [f["fieldname"] for f in fields]
    k = ([n for n, f in enumerate(j["fields"]) if f["fieldname"] == after][0] + 1) if after else len(j["fields"])
    j["fields"][k:k] = fields; j["modified"] = TS
    json.dump(j, open(p, "w"), indent=1)

edit("agriculture_settings/agriculture_settings.json", None, [
 {"fieldname": "section_forecast", "fieldtype": "Section Break", "label": "Harvest Forecast and Camera"},
 {"fieldname": "stage_model", "fieldtype": "Attach", "label": "Bud Stage Model (.tflite)",
  "description": "Trained on Kaggle from plot photos (stage_job). Attach only a model whose gate passed."},
 {"fieldname": "stage_threshold", "fieldtype": "Float", "label": "Stage Model Threshold", "default": "0.25"},
 {"fieldname": "camera_guided_counts", "fieldtype": "Check", "label": "Camera Suggests Plot Counts",
  "description": "The phone pre-fills each plot count from its photos; the worker corrects it."},
 {"fieldname": "column_break_forecast", "fieldtype": "Column Break"},
 {"fieldname": "learned_correction", "fieldtype": "Check", "label": "Use Learned Correction", "default": "1",
  "description": "Apply each variety's residual model when its calibration found it beats the physical forecast."},
 {"fieldname": "sequence_model", "fieldtype": "Attach", "label": "Sequence Model (.json)",
  "description": "GRU weights from seq_forecast.py. Used only for horizons where it beat the physical forecast."},
])
edit("bed_sample/bed_sample.json", "total_count", [
 {"fieldname": "camera_counts", "fieldtype": "Code", "options": "JSON", "label": "Camera Suggestion", "read_only": 1,
  "description": "What the camera suggested before the worker corrected it (frames, counts per stage, total)."},
])
edit("harvest_forecast_calibration/harvest_forecast_calibration.json", "error_bands", [
 {"fieldname": "section_residual", "fieldtype": "Section Break", "label": "Learned Correction"},
 {"fieldname": "residual_enabled", "fieldtype": "Check", "label": "In Use", "read_only": 1},
 {"fieldname": "residual_gain", "fieldtype": "Float", "label": "Error Reduction on Held-out Weeks", "precision": "3", "read_only": 1},
 {"fieldname": "residual_model", "fieldtype": "Code", "options": "JSON", "label": "Model", "read_only": 1},
])

os.makedirs("bay_imagery_scan", exist_ok=True); open("bay_imagery_scan/__init__.py", "w").close()
json.dump({"actions": [], "autoname": "format:SCAN-{#####}", "creation": TS, "doctype": "DocType", "engine": "InnoDB",
 "module": "Upande Agriculture", "name": "Bay Imagery Scan", "owner": "Administrator", "modified": TS,
 "modified_by": "Administrator", "sort_field": "creation", "sort_order": "DESC", "track_changes": 1,
 "field_order": ["greenhouse", "section", "variety", "column_break_1", "source", "scan_date", "status",
  "section_result", "frames", "total_per_frame", "stage_density", "section_meta", "notes", "error", "client_uuid"],
 "fields": [
  {"fieldname": "greenhouse", "fieldtype": "Link", "options": "Warehouse", "label": "Greenhouse", "reqd": 1, "in_list_view": 1, "in_standard_filter": 1},
  {"fieldname": "section", "fieldtype": "Data", "label": "Section", "reqd": 1, "in_list_view": 1},
  {"fieldname": "variety", "fieldtype": "Link", "options": "Item", "label": "Variety", "read_only": 1},
  {"fieldname": "column_break_1", "fieldtype": "Column Break"},
  {"fieldname": "source", "fieldtype": "Select", "options": "Phone walk\nDrone\nOther", "default": "Phone walk", "label": "Source", "in_list_view": 1},
  {"fieldname": "scan_date", "fieldtype": "Date", "label": "Scan Date", "default": "Today", "reqd": 1},
  {"fieldname": "status", "fieldtype": "Select", "options": "Pending\nProcessed\nFailed", "default": "Pending", "label": "Status", "in_list_view": 1,
   "description": "Drone or Desk scans: attach the photos and leave Pending; process_bay_scans.py fills the result."},
  {"fieldname": "section_result", "fieldtype": "Section Break", "label": "Result"},
  {"fieldname": "frames", "fieldtype": "Int", "label": "Frames"},
  {"fieldname": "total_per_frame", "fieldtype": "Float", "label": "Buds per Frame", "read_only": 1},
  {"fieldname": "stage_density", "fieldtype": "Code", "options": "JSON", "label": "Buds per Frame by Stage"},
  {"fieldname": "section_meta", "fieldtype": "Section Break"},
  {"fieldname": "notes", "fieldtype": "Small Text", "label": "Notes"},
  {"fieldname": "error", "fieldtype": "Small Text", "label": "Error", "read_only": 1},
  {"fieldname": "client_uuid", "fieldtype": "Data", "label": "Client UUID", "unique": 1, "hidden": 1, "read_only": 1}],
 "permissions": [
  {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1},
  {"role": "Agriculture Manager", "read": 1, "write": 1, "create": 1, "delete": 1, "report": 1},
  {"role": "Agriculture User", "read": 1, "write": 1, "create": 1, "report": 1},
  {"role": "Stock User", "read": 1, "write": 1, "create": 1, "report": 1}]},
 open("bay_imagery_scan/bay_imagery_scan.json", "w"), indent=1)
EOF
```

`bay_imagery_scan/bay_imagery_scan.py`:
```python
import json

import frappe
from frappe.model.document import Document


class BayImageryScan(Document):
	def validate(self):
		variety = frappe.db.get_value("Warehouse Section",
			{"parent": self.greenhouse, "parenttype": "Warehouse", "section": self.section}, "custom_variety")
		if variety is None and not frappe.db.exists("Warehouse Section",
				{"parent": self.greenhouse, "parenttype": "Warehouse", "section": self.section}):
			frappe.throw(f"Section {self.section} is not in {self.greenhouse}.")
		self.variety = variety
		density = json.loads(self.stage_density or "{}")
		self.total_per_frame = round(sum(float(v or 0) for v in density.values()), 4)
```

- [ ] **Step 4: Migrate, run, expect PASS**

Run: `bench --site bhcloud.local migrate >/dev/null && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_imagery_doctypes`
Expected: 3 tests OK.

- [ ] **Step 5: Commit**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture
git add upande_agriculture/upande_agriculture/doctype/{agriculture_settings,bed_sample,harvest_forecast_calibration,bay_imagery_scan} upande_agriculture/tests/test_forecast_imagery_doctypes.py
git reset -q -- '*__pycache__*'
git commit -m "Harvest forecast: camera and learned-model settings, bay imagery scans

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 17: Stage pseudo-labelling (OWLv2 boxes + count-consistent stages)

**Files:**
- Create: `/home/teddy5456/upande-vision/train/stage_label.py`
- Test: `/home/teddy5456/upande-vision/train/tests/test_stage_tools.py` (pure parts)

**Interfaces:**
- Consumes: the dataset zip from Part 1 Task 11 (`images/*.jpg`, `labels.csv`), `owl_label.label(model, proc, img, device)` with its module-level `PROMPTS`
- Produces:
  - `STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]`
  - `colour_score(img, box) -> float` (0–1, the share of saturated non-green pixels in the box's centre)
  - `assign_stages(features: list[tuple[float, float]], proportions: dict[str, float]) -> list[int]`
  - CLI `python stage_label.py <dataset.zip> <out_dir> [--device cuda]`, which writes:
    - `<out>/images/{train,val}`, `<out>/labels/{train,val}`
    - `<out>/stages.yaml`
    - `<out>/holdout.json` (`[{"image": path, "counts": {stage: n}}]` for the val images)

- [ ] **Step 1: Write the failing tests**

`/home/teddy5456/upande-vision/train/tests/__init__.py` (empty) and `/home/teddy5456/upande-vision/train/tests/test_stage_tools.py`:
```python
"""Pure tests: cd /home/teddy5456/upande-vision/train && venv/bin/python -m unittest tests.test_stage_tools -v"""
import unittest

from PIL import Image

import stage_label


class TestStageLabel(unittest.TestCase):
	def test_colour_score(self):
		green = Image.new("RGB", (40, 40), (40, 160, 40))
		red = Image.new("RGB", (40, 40), (200, 30, 40))
		self.assertLess(stage_label.colour_score(green, (0, 0, 40, 40)), 0.05)
		self.assertGreater(stage_label.colour_score(red, (0, 0, 40, 40)), 0.95)

	def test_assign_stages_follows_counts_smallest_greenest_first(self):
		# 4 boxes: (colour, area). Counts say half Rice, half Opening.
		features = [(0.9, 400.0), (0.0, 50.0), (0.0, 60.0), (0.8, 380.0)]
		got = stage_label.assign_stages(features, {"Rice": 5, "Opening": 5})
		self.assertEqual(got, [4, 0, 0, 4])

	def test_assign_stages_without_counts_is_empty(self):
		self.assertEqual(stage_label.assign_stages([(0.1, 10.0)], {}), [])
```

- [ ] **Step 2: Run, expect failure**

Run: `cd /home/teddy5456/upande-vision/train && venv/bin/python -m unittest tests.test_stage_tools -v`
Expected: ERROR, `No module named 'stage_label'`.

- [ ] **Step 3: Implement**

`/home/teddy5456/upande-vision/train/stage_label.py`:
```python
"""Weak labels for the bud-stage detector, from plot photos and their counts.

OWLv2 finds the buds; each photo's counts (worker's stage tallies for the
same plants) say how many of each stage there should be. Stages follow bud
size and colour (rice 4 mm and green ... opening, petals showing), so boxes
ranked by (petal colour, area) are cut into stages by the counted shares.
Weak but count-consistent; check_stage_model.py gates the result.

    venv/bin/python stage_label.py stage_dataset.zip dataset [--device cuda]
"""
import csv
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageOps

STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]
BUD_PROMPTS = ["a rose bud", "a flower bud", "a green bud", "a rose"]
VAL_SHARE = 0.15


def colour_score(img, box):
	"""Share of saturated, non-green pixels in the central half of a box: petal
	colour showing. PIL HSV hue runs 0-255; green is roughly 40-130."""
	x0, y0, x1, y1 = box
	cx0, cy0 = x0 + (x1 - x0) / 4, y0 + (y1 - y0) / 4
	crop = img.crop((int(cx0), int(cy0), int(cx0 + (x1 - x0) / 2) or 1, int(cy0 + (y1 - y0) / 2) or 1))
	px = list(crop.convert("HSV").getdata())
	if not px:
		return 0.0
	coloured = sum(1 for h, s, v in px if s > 60 and v > 40 and not 40 <= h <= 130)
	return coloured / len(px)


def assign_stages(features, proportions):
	"""Class id per box. Boxes ranked by (colour, area) ascending are split by
	the counted shares in STAGES order: least colour and smallest = Rice."""
	shares = [float(proportions.get(s, 0) or 0) for s in STAGES]
	total = sum(shares)
	if not features or total <= 0:
		return []
	cum, run = [], 0.0
	for s in shares:
		run += s / total
		cum.append(run)
	order = sorted(range(len(features)), key=lambda i: (features[i][0], features[i][1]))
	out = [0] * len(features)
	for rank, i in enumerate(order):
		pos = (rank + 0.5) / len(order)
		out[i] = next(j for j, c in enumerate(cum) if pos <= c + 1e-9)
	return out


def _split(sample):
	return "val" if int(hashlib.md5(sample.encode()).hexdigest(), 16) % 100 < VAL_SHARE * 100 else "train"


def main(zip_path, out_dir, device):
	import torch
	from transformers import Owlv2ForObjectDetection, Owlv2Processor

	import owl_label

	owl_label.PROMPTS = BUD_PROMPTS
	proc = Owlv2Processor.from_pretrained("google/owlv2-base-patch16-ensemble")
	model = Owlv2ForObjectDetection.from_pretrained("google/owlv2-base-patch16-ensemble").to(device).eval()
	out = Path(out_dir)
	for split in ("train", "val"):
		(out / "images" / split).mkdir(parents=True, exist_ok=True)
		(out / "labels" / split).mkdir(parents=True, exist_ok=True)
	z = zipfile.ZipFile(zip_path)
	rows = list(csv.DictReader(io.StringIO(z.read("labels.csv").decode())))
	holdout, written = [], 0
	for r in rows:
		counts = {s: int(r.get(s) or 0) for s in STAGES}
		img = ImageOps.exif_transpose(Image.open(io.BytesIO(z.read(r["image"])))).convert("RGB")
		img.thumbnail((1024, 1024))
		split = _split(r["sample"])
		stem = Path(r["image"]).stem
		img.save(out / "images" / split / f"{stem}.jpg", quality=90)
		if split == "val":
			holdout.append({"image": str(out / "images" / split / f"{stem}.jpg"), "counts": counts})
		with torch.no_grad():
			boxes = owl_label.label(model, proc, img, device)
		classes = assign_stages([(colour_score(img, b), (b[2] - b[0]) * (b[3] - b[1])) for b in boxes], counts)
		W, H = img.size
		with open(out / "labels" / split / f"{stem}.txt", "w") as fh:
			for (x0, y0, x1, y1), c in zip(boxes, classes):
				fh.write(f"{c} {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}\n")
		written += 1
	(out / "stages.yaml").write_text(
		f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n"
		+ "".join(f"  {i}: {s}\n" for i, s in enumerate(STAGES)))
	(out / "holdout.json").write_text(json.dumps(holdout, indent=1))
	print(f"labelled {written} photos ({len(holdout)} held out)")


if __name__ == "__main__":
	dev = "cuda" if "--device" in sys.argv and "cuda" in sys.argv else "cpu"
	main(sys.argv[1], sys.argv[2], dev)
```

- [ ] **Step 4: Run, expect PASS**

Run: `cd /home/teddy5456/upande-vision/train && venv/bin/python -m unittest tests.test_stage_tools -v`
Expected: 3 tests OK.

- [ ] **Step 5: No commit.** `upande-vision` is not a git repo. Note the files in the Task 24 memory step.

---

### Task 18: Kaggle training job, gate and attach

**Files:**
- Create: `/home/teddy5456/upande-vision/train/check_stage_model.py`, `erp_client.py`, `fetch_stage_dataset.py`, `attach_stage_model.py`
- Create: `/home/teddy5456/upande-vision/kaggle/stage_job/run.py`, `kernel-metadata.json`
- Modify: `/home/teddy5456/upande-vision/train/tests/test_stage_tools.py` (gate maths)

**Interfaces:**
- Consumes: `stage_label.py` (Task 17), `export_stage_dataset` (Part 1 Task 11), and Agriculture Settings `stage_model` and `camera_guided_counts` (Task 16).
- Produces:
  - `check_stage_model.share_mae(pred: dict, truth: dict) -> float`
  - `check_stage_model.spearman(xs, ys) -> float`
  - CLI `check_stage_model.py <model.pt> <holdout.json> [conf]`, which prints `GATE PASS` or `GATE FAIL ...` and writes `gate.json`
  - `ErpClient(url, key, secret)` with `.call(method, **kw)`, `.get_file(url) -> bytes`, `.upload(path, doctype, docname, fieldname, is_private) -> file_url`, `.set_value(doctype, name, field, value)`
  - The Kaggle job's outputs: `stage_detector.tflite`, `best.pt`, `gate.json`, `results.json`

- [ ] **Step 1: Add the failing gate tests**

Append to `tests/test_stage_tools.py`:
```python
import check_stage_model


class TestGate(unittest.TestCase):
	def test_share_mae(self):
		self.assertAlmostEqual(check_stage_model.share_mae({"Rice": 5, "Pea": 5}, {"Rice": 10}), 0.2)  # (0.5 + 0.5)/5 stages
		self.assertEqual(check_stage_model.share_mae({}, {"Rice": 3}), 1.0)

	def test_spearman(self):
		self.assertAlmostEqual(check_stage_model.spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
		self.assertAlmostEqual(check_stage_model.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
```

- [ ] **Step 2: Run, expect failure**

Run: `venv/bin/python -m unittest tests.test_stage_tools -v`
Expected: ERROR, `No module named 'check_stage_model'`.

- [ ] **Step 3: Implement the gate**

`check_stage_model.py`:
```python
"""Gate for the bud-stage detector, on held-out plot photos and the worker's
counts for the same plants. A photo shows only part of the plot, so totals are
compared by rank (Spearman) and stages by their shares.

    venv/bin/python check_stage_model.py best.pt dataset/holdout.json [conf]
Writes gate.json; prints GATE PASS only when share MAE <= 0.15 and rho >= 0.6.
"""
import json
import sys

STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]
MAX_SHARE_MAE, MIN_RHO = 0.15, 0.6


def share_mae(pred, truth):
	tp, tt = sum(pred.values()), sum(truth.values())
	if not tp or not tt:
		return 1.0
	return sum(abs(pred.get(s, 0) / tp - truth.get(s, 0) / tt) for s in STAGES) / len(STAGES)


def _ranks(xs):
	order = sorted(range(len(xs)), key=lambda i: xs[i])
	r = [0.0] * len(xs)
	i = 0
	while i < len(order):
		j = i
		while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
			j += 1
		for k in range(i, j + 1):
			r[order[k]] = (i + j) / 2
		i = j + 1
	return r


def spearman(xs, ys):
	rx, ry = _ranks(xs), _ranks(ys)
	n = len(xs)
	mx, my = sum(rx) / n, sum(ry) / n
	cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
	vx = sum((a - mx) ** 2 for a in rx) ** 0.5
	vy = sum((b - my) ** 2 for b in ry) ** 0.5
	return cov / (vx * vy) if vx and vy else 0.0


def main(model_path, holdout_path, conf=0.25):
	from ultralytics import YOLO

	m = YOLO(model_path)
	rows = json.load(open(holdout_path))
	maes, pred_tot, true_tot = [], [], []
	for r in rows:
		res = m.predict(r["image"], imgsz=640, conf=conf, iou=0.5, max_det=300, verbose=False)[0]
		pred = {}
		for c in res.boxes.cls.tolist():
			pred[STAGES[int(c)]] = pred.get(STAGES[int(c)], 0) + 1
		maes.append(share_mae(pred, r["counts"]))
		pred_tot.append(sum(pred.values()))
		true_tot.append(sum(r["counts"].values()))
	out = {"photos": len(rows), "share_mae": round(sum(maes) / max(1, len(maes)), 3),
		"rho": round(spearman(pred_tot, true_tot), 3) if len(rows) > 2 else 0.0}
	out["passed"] = out["photos"] >= 20 and out["share_mae"] <= MAX_SHARE_MAE and out["rho"] >= MIN_RHO
	json.dump(out, open("gate.json", "w"), indent=1)
	print("GATE PASS" if out["passed"] else f"GATE FAIL {out}")
	return out


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 0.25)
```

- [ ] **Step 4: Run, expect PASS**

Run: `venv/bin/python -m unittest tests.test_stage_tools -v`
Expected: 5 tests OK.

- [ ] **Step 5: ERP client, dataset fetch, attach**

`erp_client.py`:
```python
"""Minimal Frappe REST client for the off-cloud scripts. Credentials come from
the environment (ERP_URL, ERP_KEY, ERP_SECRET): an API key pair of a System
Manager user on the target site. Never commit them."""
import json
import os
import urllib.parse
import urllib.request


class ErpClient:
	def __init__(self, url=None, key=None, secret=None):
		self.url = (url or os.environ["ERP_URL"]).rstrip("/")
		self.auth = f"token {key or os.environ['ERP_KEY']}:{secret or os.environ['ERP_SECRET']}"

	def _req(self, path, data=None, headers=None):
		h = {"Authorization": self.auth, "Accept": "application/json", **(headers or {})}
		req = urllib.request.Request(self.url + path, data=data, headers=h)
		with urllib.request.urlopen(req, timeout=300) as r:
			return r.read()

	def call(self, method, **kw):
		body = json.dumps(kw).encode()
		out = json.loads(self._req(f"/api/method/{method}", body, {"Content-Type": "application/json"}))
		return out.get("message", out)

	def get_file(self, file_url):
		return self._req(file_url if file_url.startswith("/") else "/" + file_url)

	def upload(self, path, doctype, docname, fieldname, is_private=0):
		boundary = "----upande" + os.urandom(8).hex()
		parts = []
		for k, v in {"doctype": doctype, "docname": docname, "fieldname": fieldname, "is_private": str(is_private)}.items():
			parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
		name = os.path.basename(path)
		parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'
			f"Content-Type: application/octet-stream\r\n\r\n".encode() + open(path, "rb").read() + b"\r\n")
		parts.append(f"--{boundary}--\r\n".encode())
		out = json.loads(self._req("/api/method/upload_file", b"".join(parts),
			{"Content-Type": f"multipart/form-data; boundary={boundary}"}))
		return out["message"]["file_url"]

	def set_value(self, doctype, name, field, value):
		return self.call("frappe.client.set_value", doctype=doctype, name=name, fieldname=field, value=value)
```

`fetch_stage_dataset.py`:
```python
"""Pull plot photos + counts from the ERP and refresh the Kaggle dataset.

    ERP_URL=... ERP_KEY=... ERP_SECRET=... venv/bin/python fetch_stage_dataset.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

from erp_client import ErpClient

HERE = Path(__file__).parent
DATA = HERE.parent / "kaggle" / "stage_data"
KAGGLE = str(HERE / "exportenv2" / "bin" / "kaggle")
SLUG = "teddywambua/bilashaka-stage-photos"
MIN_PHOTOS = 150

erp = ErpClient()
out = erp.call("upande_agriculture.forecast.dataset.export_stage_dataset")
print("dataset:", out)
if out["images"] < MIN_PHOTOS:
	sys.exit(f"Only {out['images']} photos; wait for {MIN_PHOTOS} before training.")
DATA.mkdir(parents=True, exist_ok=True)
(DATA / "stage_dataset.zip").write_bytes(erp.get_file(out["file_url"]))
for f in ("stage_label.py", "owl_label.py", "check_stage_model.py"):
	shutil.copy(HERE / f, DATA / f)
meta = DATA / "dataset-metadata.json"
if not meta.exists():
	meta.write_text('{"title": "bilashaka-stage-photos", "id": "%s", "licenses": [{"name": "other"}]}' % SLUG)
	subprocess.run([KAGGLE, "datasets", "create", "-p", str(DATA), "--dir-mode", "zip"], check=True)
else:
	subprocess.run([KAGGLE, "datasets", "version", "-p", str(DATA), "-m", "refresh", "--dir-mode", "zip"], check=True)
```

`attach_stage_model.py`:
```python
"""Attach a gated stage model to Agriculture Settings and switch on guided counts.

    ERP_URL=... ERP_KEY=... ERP_SECRET=... venv/bin/python attach_stage_model.py ../kaggle/stage_out
"""
import json
import sys
from pathlib import Path

from erp_client import ErpClient

out = Path(sys.argv[1])
gate = json.loads((out / "gate.json").read_text())
if not gate.get("passed"):
	sys.exit(f"Gate did not pass, not attaching: {gate}")
erp = ErpClient()
url = erp.upload(str(out / "stage_detector.tflite"), "Agriculture Settings", "Agriculture Settings", "stage_model", is_private=0)
erp.set_value("Agriculture Settings", "Agriculture Settings", "stage_model", url)
erp.set_value("Agriculture Settings", "Agriculture Settings", "camera_guided_counts", 1)
print("attached", url, gate)
```

- [ ] **Step 6: Kaggle job**

`/home/teddy5456/upande-vision/kaggle/stage_job/kernel-metadata.json`:
```json
{
  "id": "teddywambua/bilashaka-stage-detector-train",
  "title": "bilashaka-stage-detector-train",
  "code_file": "run.py",
  "language": "python",
  "kernel_type": "script",
  "is_private": true,
  "enable_gpu": true,
  "enable_internet": true,
  "machine_shape": "NvidiaTeslaT4",
  "dataset_sources": ["teddywambua/bilashaka-stage-photos"],
  "competition_sources": [],
  "kernel_sources": []
}
```

`/home/teddy5456/upande-vision/kaggle/stage_job/run.py`:
```python
"""Bilashaka bud-stage detector: label (OWLv2) -> train (YOLO11n, 5 stages) ->
gate (held-out plot photos vs worker counts) -> TFLite export.
Outputs in /kaggle/working: stage_detector.tflite, best.pt, gate.json, results.json."""
import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile

W = "/kaggle/working"
TR = f"{W}/train"
OUT = {}


def sh(cmd):
	print(f"\n$ {cmd}", flush=True)
	subprocess.run(cmd, shell=True, check=True)


hits = glob.glob("/kaggle/input/**/stage_label.py", recursive=True)
if not hits:
	sh("find /kaggle/input -maxdepth 4 | head -50")
	raise SystemExit("dataset not found under /kaggle/input")
IN = os.path.dirname(hits[0])
os.makedirs(TR, exist_ok=True)
for p in glob.glob(f"{IN}/*"):
	shutil.copy(p, TR) if os.path.isfile(p) else shutil.copytree(p, f"{TR}/{os.path.basename(p)}")
os.chdir(TR)
zips = glob.glob("**/stage_dataset.zip", recursive=True)
assert zips, "stage_dataset.zip missing"
sh('pip -q install "ultralytics==8.4.80" "transformers>=4.44"')
sh(f"{sys.executable} stage_label.py {zips[0]} dataset --device cuda")

from ultralytics import YOLO

YOLO("yolo11n.pt").train(data=f"{TR}/dataset/stages.yaml", imgsz=640, epochs=80, batch=32, workers=4,
	max_det=300, degrees=10, fliplr=0.5, mosaic=1.0, close_mosaic=10, patience=20,
	project=f"{W}/runs", name="stage", exist_ok=True, plots=False)
best = f"{W}/runs/stage/weights/best.pt"
shutil.copy(best, f"{W}/best.pt")
sh(f"{sys.executable} check_stage_model.py {best} dataset/holdout.json 0.25")
shutil.copy("gate.json", f"{W}/gate.json")
OUT["gate"] = json.load(open("gate.json"))

sh('pip -q install "numpy<2" "onnx>=1.12,<1.18" "onnx2tf>=1.26.3,<1.28" onnxslim sng4onnx onnx_graphsurgeon tf_keras ai-edge-litert')
sh(f"{sys.executable} -c \"from ultralytics import YOLO; YOLO('{best}').export(format='tflite', imgsz=640)\"")
tfl = sorted(glob.glob(f"{W}/runs/stage/weights/**/*float16.tflite", recursive=True))[0]
shutil.copy(tfl, f"{W}/stage_detector.tflite")
json.dump(OUT, open(f"{W}/results.json", "w"), indent=1)
shutil.rmtree(TR, ignore_errors=True)
print(json.dumps(OUT, indent=1))
```

- [ ] **Step 7: Dry check of the tooling (no training yet)**

Run:
```bash
cd /home/teddy5456/upande-vision/train && venv/bin/python -c "import erp_client, check_stage_model, stage_label; print('imports ok')" && venv/bin/python -m py_compile fetch_stage_dataset.py attach_stage_model.py ../kaggle/stage_job/run.py && echo compiled
```
Expected: `imports ok` then `compiled`. Real training waits for ≥150 plot photos; `fetch_stage_dataset.py` refuses below that.

---

### Task 19: Camera-guided plot counts on the phone

**Files:**
- Modify: `upande_agriculture/forecast/camera.py` (create) and `upande_agriculture/forecast/api.py` (`submit_plot_count(camera_counts=None)`)
- Test: `upande_agriculture/tests/test_forecast_camera.py`
- App: modify `src/services/budCounter.ts`; create `src/services/stageCounter.ts`; modify `src/services/api.ts`, `src/types/index.ts`, `src/screens/agriculture/PlotCountScreen.tsx`

**Interfaces:**
- Produces (ERP):
  - `camera.get_camera_config() -> {"stage_model_url": str, "stage_threshold": float, "guided": bool, "stages": STAGES}` (whitelisted)
  - `api.submit_plot_count(..., camera_counts=None)` stores the JSON on `Bed Sample.camera_counts`
- Produces (app):
  - `budCounter.ts` exports `loadModel(modelPath) → TfliteModel`, `letterbox(photoUri, size) → {input: Float32Array, dx, dy, w, h}`, `nms(boxes, iou)`
  - `stageCounter.ts` exports `countStages(photoUri, modelUrl, stages: string[], threshold) → Promise<{counts: Record<string, number>; total: number}>`
  - `api.ts` exports `getCameraConfig(): Promise<CameraConfig>`; type `CameraConfig { stage_model_url: string; stage_threshold: number; guided: boolean; stages: string[] }`

- [ ] **Step 1: Write the failing ERP test**

`upande_agriculture/tests/test_forecast_camera.py`:
```python
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from upande_agriculture.forecast import api, camera
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestCamera(FrappeTestCase):
	def test_config_empty_without_model(self):
		frappe.db.set_single_value("Agriculture Settings", "stage_model", None)
		cfg = camera.get_camera_config()
		self.assertEqual((cfg["stage_model_url"], cfg["guided"]), ("", False))
		self.assertEqual(cfg["stages"], ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"])

	def test_worker_counts_saved_camera_suggestion_kept(self):
		make_variety()
		gh = make_greenhouse("FC CAM", (("S1", 1, 9, "FC-ROSE", 1000),))
		plot = [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == gh][0]["plot"]
		cam = {"frames": 1, "counts": {"Rice": 9}, "total": 9}
		r = api.submit_plot_count("fc-cam-1", plot, '[{"stage_name": "Rice", "count": 7}]', 10,
			captured_at=f"{nowdate()} 06:00:00", camera_counts=json.dumps(cam))
		doc = frappe.get_doc("Bed Sample", r["name"])
		self.assertEqual(doc.total_count, 7)
		self.assertEqual(json.loads(doc.camera_counts)["counts"]["Rice"], 9)
```

- [ ] **Step 2: Run, expect failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_camera`
Expected: ImportError for `camera`.

- [ ] **Step 3: Implement the ERP side**

`upande_agriculture/forecast/camera.py`:
```python
"""Camera side of the forecast: the phone's stage-model config, bay imagery
scans (phone walks and drone photos) and the scan factor that corrects how
representative a section's plots are."""
import json

import frappe
from frappe.utils import add_days, getdate, nowdate

STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]  # detector class order
SCAN_WINDOW_DAYS = 14
SCAN_FACTOR_BOUNDS = (0.5, 1.5)


@frappe.whitelist()
def get_camera_config():
	get = lambda f: frappe.db.get_single_value("Agriculture Settings", f)  # not cached: switches take effect at once
	url = get("stage_model") or ""
	return {"stage_model_url": url, "stage_threshold": float(get("stage_threshold") or 0.25),
		"guided": bool(url and get("camera_guided_counts")), "stages": STAGES}
```

In `forecast/api.py`, change the signature to
`def submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None, camera_counts=None):`
and in the `frappe.get_doc({...})` dict add
`"camera_counts": camera_counts if isinstance(camera_counts, str) or camera_counts is None else json.dumps(camera_counts),`.

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_camera`
Expected: 2 tests OK.

- [ ] **Step 5: App, shared detector plumbing**

Refactor `src/services/budCounter.ts` with no behaviour change:
- Rename `getModel` to an exported `loadModel` (same body).
- Move the resize-and-letterbox block of `countBuds` into
  `export async function letterbox(photoUri: string, SIZE: number): Promise<{ input: Float32Array; dx: number; dy: number; w: number; h: number }>`.
  Its body runs from `let uri = photoUri;` up to and including the `input` fill loop, and returns `{ input, dx, dy, w, h }`.
- Move the NMS loop into `export function nms<T extends { x0: number; y0: number; x1: number; y1: number; score: number }>(boxes: T[], iouMax = IOU): T[]`:
  sort by score descending, keep boxes whose `iou` with every kept box is `< iouMax`.
- Export the `Box` type and `iou`.
- `countBuds` becomes:
```ts
export async function countBuds(photoUri: string, modelPath: string, threshold = 0.25): Promise<{ count: number; boxes: HeadBox[] }> {
  const model = await loadModel(modelPath);
  const SIZE = model.inputs[0].shape[1];
  const { input, dx, dy, w, h } = await letterbox(photoUri, SIZE);
  const [raw] = await model.run([input.buffer]);
  const out = new Float32Array(raw);
  const n = model.outputs[0].shape[2];
  let maxCoord = 0;
  for (let i = 0; i < 2 * n; i++) maxCoord = Math.max(maxCoord, out[i]);
  const k = maxCoord <= 2 ? SIZE : 1;
  const boxes: Box[] = [];
  for (let i = 0; i < n; i++) {
    const score = out[4 * n + i];
    if (score < threshold) continue;
    const cx = out[i] * k, cy = out[n + i] * k, bw = out[2 * n + i] * k, bh = out[3 * n + i] * k;
    boxes.push({ x0: cx - bw / 2, y0: cy - bh / 2, x1: cx + bw / 2, y1: cy + bh / 2, score });
  }
  const kept = nms(boxes);
  const r4 = (v: number) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
  return {
    count: kept.length,
    boxes: kept.map((b) => [r4((b.x0 - dx) / w), r4((b.y0 - dy) / h), r4((b.x1 - dx) / w), r4((b.y1 - dy) / h),
      Math.round(b.score * 1000) / 1000]),
  };
}
```

Create `src/services/stageCounter.ts`:
```ts
import { loadModel, letterbox, nms, Box } from './budCounter';

// Bud stages on a plot photo. YOLO11 multi-class head: [1, 4 + C, N] =
// cx, cy, w, h, then one score per stage (class order = the ERP's STAGES).
// Same letterbox and NMS as the bucket counter; NMS runs per stage.
export async function countStages(
  photoUri: string, modelUrl: string, stages: string[], threshold = 0.25,
): Promise<{ counts: Record<string, number>; total: number }> {
  const model = await loadModel(modelUrl);
  const SIZE = model.inputs[0].shape[1];
  const { input } = await letterbox(photoUri, SIZE);
  const [raw] = await model.run([input.buffer]);
  const out = new Float32Array(raw);
  const n = model.outputs[0].shape[2];
  const C = model.outputs[0].shape[1] - 4;
  let maxCoord = 0;
  for (let i = 0; i < 2 * n; i++) maxCoord = Math.max(maxCoord, out[i]);
  const k = maxCoord <= 2 ? SIZE : 1;
  const byClass: Box[][] = Array.from({ length: C }, () => []);
  for (let i = 0; i < n; i++) {
    let best = 0, score = out[4 * n + i];
    for (let c = 1; c < C; c++) {
      const s = out[(4 + c) * n + i];
      if (s > score) { score = s; best = c; }
    }
    if (score < threshold) continue;
    const cx = out[i] * k, cy = out[n + i] * k, bw = out[2 * n + i] * k, bh = out[3 * n + i] * k;
    byClass[best].push({ x0: cx - bw / 2, y0: cy - bh / 2, x1: cx + bw / 2, y1: cy + bh / 2, score });
  }
  const counts: Record<string, number> = {};
  let total = 0;
  byClass.forEach((boxes, c) => {
    const kept = nms(boxes).length;
    if (stages[c] && kept) { counts[stages[c]] = kept; total += kept; }
  });
  return { counts, total };
}
```

In `src/types/index.ts` add
```ts
export interface CameraConfig { stage_model_url: string; stage_threshold: number; guided: boolean; stages: string[] }
```
and add `camera_counts?: string;` to `PlotCountPayload`.

In `src/services/api.ts` add
```ts
export async function getCameraConfig(): Promise<CameraConfig> {
  const res = await apiPost<{ message?: CameraConfig }>('upande_agriculture.forecast.camera.get_camera_config', {});
  return (res as any).message ?? res;
}
```
(and import `CameraConfig`).

- [ ] **Step 6: App, guided counting in PlotCountScreen**

In `src/screens/agriculture/PlotCountScreen.tsx`:
- Imports: `import { getCameraConfig } from '../../services/api';`, `import { countStages } from '../../services/stageCounter';`, and add `CameraConfig` to the types import.
- State: `const [camCfg, setCamCfg] = useState<CameraConfig | null>(null);` and `const [camera, setCamera] = useState<{ frames: number; counts: Record<string, number>; total: number } | null>(null);`. Rename the existing JSX const `camera` to `cameraModal`, with its two uses.
- In the mount effect, after the plan loads, add:
```ts
      try {
        const cfg = await getCameraConfig();
        setCamCfg(cfg);
        await setSetting('camera_config', JSON.stringify(cfg));
      } catch {
        const c = await getSetting('camera_config');
        if (c) setCamCfg(JSON.parse(c));
      }
```
- In `openPlot`, add `setCamera(null);`.
- In `takePhoto`, after `setPhotos(...)`:
```ts
    if (camCfg?.guided && camCfg.stage_model_url) {
      try {
        let base = (await getApiUrl()).replace(/\/+$/, '');
        if (!/^https?:\/\//.test(base)) base = `https://${base}`;
        const url = /^https?:\/\//.test(camCfg.stage_model_url) ? camCfg.stage_model_url : `${base}${camCfg.stage_model_url}`;
        const r = await countStages(dest, url, camCfg.stages, camCfg.stage_threshold);
        setCamera((prev) => {
          const merged = { frames: (prev?.frames ?? 0) + 1, counts: { ...(prev?.counts ?? {}) }, total: (prev?.total ?? 0) + r.total };
          for (const [s, n] of Object.entries(r.counts)) merged.counts[s] = (merged.counts[s] ?? 0) + n;
          return merged;
        });
        // Pre-fill only untouched counters: the worker's own taps always win.
        setCounts((c) => {
          if (Object.values(c).some((v) => v > 0)) return c;
          return { ...r.counts };
        });
      } catch {}
    }
```
  Import `getApiUrl` from `../../database/settings`, and add `camCfg` to `takePhoto`'s dependency list.
- Above the stage rows, add:
```tsx
        {camera && (
          <Text style={styles.sub}>Camera saw {camera.total} buds in {camera.frames} photo{camera.frames === 1 ? '' : 's'}: check each stage and correct it.</Text>
        )}
```
- In `save`'s `job`, add `camera_counts: camera ? JSON.stringify(camera) : undefined,`.

- [ ] **Step 7: Type-check**

Run: `cd /home/teddy5456/bilashaka-harvest && npx tsc --noEmit -p . 2>&1 | grep -v vector-icons | grep -E "budCounter|stageCounter|PlotCount|services/api|types/index|PhotoHarvest"; echo done`
Expected: only `done`.

- [ ] **Step 8: Commit the ERP side**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture && git add upande_agriculture/forecast/camera.py upande_agriculture/forecast/api.py upande_agriculture/tests/test_forecast_camera.py
git commit -m "Harvest forecast: camera config and camera-suggested plot counts

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 20: Bay scans (phone walk and drone) and the off-cloud processor

**Files:**
- Modify: `upande_agriculture/forecast/camera.py`: `submit_bay_scan`, `get_pending_scans`, `post_scan_result`
- Test: append to `upande_agriculture/tests/test_forecast_camera.py`
- Create: `/home/teddy5456/upande-vision/train/process_bay_scans.py`
- App: modify `src/services/api.ts`, `src/types/index.ts`, `src/services/sync.ts`, `src/screens/agriculture/PlotCountScreen.tsx` (walk scan)

**Interfaces:**
- Produces (ERP, whitelisted):
  - `submit_bay_scan(client_uuid, greenhouse, section, frames, stage_density, source="Phone walk", photos=None, notes=None) -> {"name"}` (POST, idempotent, saved as Processed)
  - `get_pending_scans(limit=20) -> [{"name", "greenhouse", "section", "files": [file_url]}]` (System Manager)
  - `post_scan_result(name, frames=None, stage_density=None, error=None) -> {"status"}` (System Manager)
- Produces (app): queue action `'bayScan'` → `sendBayScan(job)`; `submitBayScan(payload)` in api.ts; type `BayScanPayload`.

- [ ] **Step 1: Write the failing tests**

Append to `TestCamera` in `test_forecast_camera.py`:
```python
	def _gh(self):
		make_variety()
		return make_greenhouse("FC CAM", (("S1", 1, 9, "FC-ROSE", 1000),))

	def test_phone_scan_saved_processed_once(self):
		gh = self._gh()
		args = dict(client_uuid="fc-scan-1", greenhouse=gh, section="S1", frames=20,
			stage_density=json.dumps({"Rice": 3.0, "Pea": 1.0}))
		a, b = camera.submit_bay_scan(**args), camera.submit_bay_scan(**args)
		self.assertEqual(a, b)
		d = frappe.get_doc("Bay Imagery Scan", a["name"])
		self.assertEqual((d.status, d.total_per_frame, d.source), ("Processed", 4.0, "Phone walk"))

	def test_drone_scan_pending_then_result_or_failure(self):
		gh = self._gh()
		ok = frappe.get_doc({"doctype": "Bay Imagery Scan", "greenhouse": gh, "section": "S1", "source": "Drone"}).insert()
		bad = frappe.get_doc({"doctype": "Bay Imagery Scan", "greenhouse": gh, "section": "S1", "source": "Drone"}).insert()
		names = [s["name"] for s in camera.get_pending_scans()]
		self.assertIn(ok.name, names)
		camera.post_scan_result(ok.name, frames=8, stage_density=json.dumps({"Opening": 0.5}))
		camera.post_scan_result(bad.name, error="no readable images")
		self.assertEqual(frappe.db.get_value("Bay Imagery Scan", ok.name, ["status", "total_per_frame"]), ("Processed", 0.5))
		self.assertEqual(frappe.db.get_value("Bay Imagery Scan", bad.name, ["status", "error"]), ("Failed", "no readable images"))
```

- [ ] **Step 2: Run, expect failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_camera`
Expected: AttributeError `submit_bay_scan`.

- [ ] **Step 3: Implement the endpoints**

Append to `forecast/camera.py`:
```python
@frappe.whitelist(methods=["POST"])
def submit_bay_scan(client_uuid, greenhouse, section, frames, stage_density, source="Phone walk", photos=None, notes=None):
	"""A walk scan the phone already ran the stage model on. Idempotent on client_uuid."""
	import base64

	existing = frappe.db.get_value("Bay Imagery Scan", {"client_uuid": client_uuid})
	if existing:
		return {"name": existing}
	photos = json.loads(photos) if isinstance(photos, str) else (photos or [])
	doc = frappe.get_doc({"doctype": "Bay Imagery Scan", "client_uuid": client_uuid, "greenhouse": greenhouse,
		"section": section, "source": source, "frames": int(frames or 0), "status": "Processed", "notes": notes,
		"stage_density": stage_density if isinstance(stage_density, str) else json.dumps(stage_density)}).insert()
	for i, b64 in enumerate(photos[:3], start=1):
		frappe.get_doc({"doctype": "File", "file_name": f"{doc.name}-{i}.jpg", "content": base64.b64decode(b64),
			"attached_to_doctype": "Bay Imagery Scan", "attached_to_name": doc.name, "is_private": 1,
		}).insert(ignore_permissions=True)
	return {"name": doc.name}


@frappe.whitelist()
def get_pending_scans(limit=20):
	"""Drone / Desk scans waiting for process_bay_scans.py, with their image files."""
	frappe.only_for("System Manager")
	out = []
	for s in frappe.get_all("Bay Imagery Scan", filters={"status": "Pending"},
			fields=["name", "greenhouse", "section"], order_by="creation", limit=int(limit)):
		files = frappe.get_all("File", pluck="file_url",
			filters={"attached_to_doctype": "Bay Imagery Scan", "attached_to_name": s.name})
		out.append({**s, "files": [f for f in files if f and f.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png")]})
	return out


@frappe.whitelist(methods=["POST"])
def post_scan_result(name, frames=None, stage_density=None, error=None):
	frappe.only_for("System Manager")
	doc = frappe.get_doc("Bay Imagery Scan", name)
	if error:
		doc.status, doc.error = "Failed", str(error)[:500]
	else:
		doc.status, doc.error = "Processed", None
		doc.frames = int(frames or 0)
		doc.stage_density = stage_density if isinstance(stage_density, str) else json.dumps(stage_density)
	doc.save(ignore_permissions=True)
	return {"status": doc.status}
```

- [ ] **Step 4: Run, expect PASS**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_camera`
Expected: 4 tests OK.

- [ ] **Step 5: The off-cloud processor**

`/home/teddy5456/upande-vision/train/process_bay_scans.py`:
```python
"""Process drone / Desk bay scans: run the stage detector on each scan's photos
and post detections per frame by stage back to the ERP.

    ERP_URL=... ERP_KEY=... ERP_SECRET=... venv/bin/python process_bay_scans.py best.pt [conf]
Run it after a drone flight's photos are attached to Bay Imagery Scan records."""
import io
import json
import sys

from PIL import Image

from erp_client import ErpClient

STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]


def density(model, images, conf):
	totals = {s: 0 for s in STAGES}
	for img in images:
		res = model.predict(img, imgsz=640, conf=conf, iou=0.5, max_det=300, verbose=False)[0]
		for c in res.boxes.cls.tolist():
			totals[STAGES[int(c)]] += 1
	return {s: round(n / len(images), 3) for s, n in totals.items() if n}


def main(model_path, conf=0.25):
	from ultralytics import YOLO

	erp, model = ErpClient(), YOLO(model_path)
	for scan in erp.call("upande_agriculture.forecast.camera.get_pending_scans"):
		images = []
		for url in scan["files"]:
			try:
				images.append(Image.open(io.BytesIO(erp.get_file(url))).convert("RGB"))
			except Exception as e:
				print("skip", url, e)
		if not images:
			erp.call("upande_agriculture.forecast.camera.post_scan_result", name=scan["name"], error="no readable images")
			continue
		d = density(model, images, conf)
		erp.call("upande_agriculture.forecast.camera.post_scan_result", name=scan["name"],
			frames=len(images), stage_density=json.dumps(d))
		print(scan["name"], len(images), d)


if __name__ == "__main__":
	main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 0.25)
```

Check: `cd /home/teddy5456/upande-vision/train && venv/bin/python -m py_compile process_bay_scans.py && echo ok`. Expected `ok`.

- [ ] **Step 6: App, the walk scan**

`src/types/index.ts`:
```ts
export interface BayScanPayload {
  client_uuid: string; greenhouse: string; section: string; frames: number;
  stage_density: string; // JSON {stage: detections per frame}
  source: 'Phone walk'; photos?: string[];
}
```

`src/services/api.ts`:
```ts
export async function submitBayScan(payload: BayScanPayload): Promise<{ name: string }> {
  const res = await apiPost<{ message?: { name: string } }>('upande_agriculture.forecast.camera.submit_bay_scan', payload);
  return (res as any).message ?? res;
}
```

In `PlotCountScreen.tsx`:
- Add state `const [walk, setWalk] = useState<{ greenhouse: string; section: string; frames: number; totals: Record<string, number>; samples: string[] } | null>(null);` and `const walkTimer = useRef<ReturnType<typeof setInterval> | null>(null);`.
- Add `startWalk(p: PlotRow)`:
  - If `!camCfg?.stage_model_url`, show the error `'Walk scans need the camera stage model. It is switched on once enough plot photos have trained it.'` and return.
  - Otherwise `setWalk({ greenhouse: p.greenhouse, section: p.section, frames: 0, totals: {}, samples: [] })` and `setCameraOpen(true)`.
- While `walk` is set and the camera is open, start a 2-second interval that:
  1. calls `cameraRef.current?.takePictureAsync({ quality: 0.5 })`;
  2. runs `countStages` on the picture (model URL resolved as in Task 19);
  3. adds the counts into `walk.totals` and increments `frames`;
  4. keeps the first 3 picture URIs in `samples` and deletes the others.

  Guard with a `busy` ref so frames never overlap. Clear the interval when `walk` becomes null or the modal closes.
- In the camera modal, when `walk` is set:
  - the title shows `Walk the path slowly: ${walk.frames} frames`;
  - the shutter button is replaced by a **Stop** button that stops the timer, builds the job below, queues it, deletes nothing (the sync deletes the samples) and shows a success confirm.
```ts
const job = { client_uuid: `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, greenhouse: walk.greenhouse,
  section: walk.section, frames: walk.frames, source: 'Phone walk' as const,
  stage_density: JSON.stringify(Object.fromEntries(Object.entries(walk.totals).map(([s, n]) => [s, Math.round((n / Math.max(1, walk.frames)) * 1000) / 1000]))),
  photo_uris: walk.samples };
await addToSyncQueue('bayScan', job);
```
- In the plot list, under each greenhouse label, add a small `TouchableOpacity` per section, "Walk scan {section}", calling `startWalk(firstPlotOfThatSection)`.
- Export:
```ts
export async function sendBayScan(job: Omit<BayScanPayload, 'photos'> & { photo_uris?: string[] }) {
  const { photo_uris = [], ...rest } = job;
  const photos = await Promise.all(photo_uris.map((u) => FileSystem.readAsStringAsync(u, { encoding: FileSystem.EncodingType.Base64 }).catch(() => null)));
  const res = await submitBayScan({ ...rest, photos: photos.filter((p): p is string => !!p) });
  await Promise.all(photo_uris.map((u) => FileSystem.deleteAsync(u, { idempotent: true })));
  return res;
}
```
- In `src/services/sync.ts`: import `sendBayScan` from `PlotCountScreen` alongside `sendPlotCount`, and add the branch `} else if (entry.action === 'bayScan') { await sendBayScan(payload);`.

- [ ] **Step 7: Type-check**

Run: `cd /home/teddy5456/bilashaka-harvest && npx tsc --noEmit -p . 2>&1 | grep -v vector-icons | grep -E "PlotCount|stageCounter|services/(api|sync)|types/index"; echo done`
Expected: only `done`.

- [ ] **Step 8: Commit the ERP side**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture && git add upande_agriculture/forecast/camera.py upande_agriculture/tests/test_forecast_camera.py
git commit -m "Harvest forecast: bay scans from phone walks and drone photos

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 21: Scan factor in the forecast

**Files:**
- Modify: `upande_agriculture/forecast/camera.py` (`scan_factor`), `upande_agriculture/forecast/service.py`
- Test: `upande_agriculture/tests/test_forecast_scan_factor.py`

**Interfaces:**
- Produces:
  - `camera.scan_factor(greenhouse, section, as_of) -> float | None`
  - `service.forecast_section` multiplies counted buds by the factor (when not None) and adds `flags["scan_adjusted"]` (bool) plus a top-level `"scan_factor"` (float or None)

- [ ] **Step 1: Write the failing test**

`upande_agriculture/tests/test_forecast_scan_factor.py`:
```python
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from upande_agriculture.forecast import camera, data, service
from upande_agriculture.tests.forecast_fixtures import make_count, make_greenhouse, make_plot, make_variety


class TestScanFactor(FrappeTestCase):
	def setUp(self):
		make_variety()
		self.gh = make_greenhouse("FC SF", (("S1", 1, 9, "FC-ROSE", 1000),))
		self.plot = make_plot(self.gh, "S1")
		self.row = [r for r in data.sections(greenhouse=self.gh) if r.section == "S1"][0]

	def _scan(self, per_frame):
		frappe.get_doc({"doctype": "Bay Imagery Scan", "greenhouse": self.gh, "section": "S1", "status": "Processed",
			"frames": 10, "stage_density": json.dumps({"Opening": per_frame})}).insert()

	def test_no_scan_no_factor(self):
		make_count(self.plot, nowdate(), {"Opening": 10})
		self.assertIsNone(camera.scan_factor(self.gh, "S1", nowdate()))
		self.assertFalse(service.forecast_section(self.row)["flags"]["scan_adjusted"])

	def test_scan_scales_buds_and_is_clipped(self):
		s = frappe.get_doc("Bed Sample", make_count(self.plot, nowdate(), {"Opening": 10}))
		s.db_set("camera_counts", json.dumps({"frames": 1, "counts": {"Opening": 4}, "total": 4}))
		self._scan(3.2)  # 3.2 / 4 = 0.8
		self.assertAlmostEqual(camera.scan_factor(self.gh, "S1", nowdate()), 0.8)
		base = sum(d["buds"] for d in service.forecast_section(self.row)["daily"])
		self.assertAlmostEqual(base, 0.8 * 1000 * 0.99, delta=10)
		self._scan(100)  # mean (3.2 + 100) / 2 / 4 >> 1.5: clipped
		self.assertEqual(camera.scan_factor(self.gh, "S1", nowdate()), 1.5)
```

- [ ] **Step 2: Run, expect failure**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_scan_factor`
Expected: AttributeError `scan_factor`.

- [ ] **Step 3: Implement**

Append to `forecast/camera.py`:
```python
def scan_factor(greenhouse, section, as_of):
	"""How the whole bay compares with its plots, through the same camera:
	buds per frame on bay scans / buds per photo on plot counts (last 14 days).
	None without both, so the forecast is untouched."""
	as_of = getdate(as_of)
	since = add_days(as_of, -SCAN_WINDOW_DAYS)
	scans = frappe.get_all("Bay Imagery Scan", pluck="total_per_frame",
		filters={"greenhouse": greenhouse, "section": section, "status": "Processed",
			"scan_date": ["between", [since, as_of]]})
	plot_rates = []
	for c in frappe.get_all("Bed Sample", pluck="camera_counts",
			filters={"greenhouse": greenhouse, "section": section, "camera_counts": ["is", "set"],
				"sampling_date": ["between", [since, as_of]]}):
		cc = json.loads(c or "{}")
		if cc.get("frames"):
			plot_rates.append(float(cc.get("total") or 0) / cc["frames"])
	scans = [float(s) for s in scans if s]
	if not scans or not plot_rates or not sum(plot_rates):
		return None
	f = (sum(scans) / len(scans)) / (sum(plot_rates) / len(plot_rates))
	return round(min(SCAN_FACTOR_BOUNDS[1], max(SCAN_FACTOR_BOUNDS[0], f)), 4)
```

In `forecast/service.py`:
- add `from upande_agriculture.forecast import camera` to the imports;
- after `res = engine.section_forecast(...)`, insert:
```python
	factor = camera.scan_factor(row.greenhouse, row.section, as_of)
	if factor is not None:
		res["buds"] = [b * factor for b in res["buds"]]
		res["stems"] = [b + g for b, g in zip(res["buds"], res["regrowth"])]
```
- in the returned dict, add `"scan_factor": factor,` and add `"scan_adjusted": factor is not None` inside `flags`.

The Part 1 report `FLAG_TEXT` (section report) gets `"scan_adjusted": "Adjusted by bay scan"`. Edit `section_harvest_forecast.py` accordingly.

- [ ] **Step 4: Run, expect PASS (and Part 1 service tests still pass)**

Run: `bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_scan_factor && bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.test_forecast_service`
Expected: 2 + 4 tests OK.

- [ ] **Step 5: Commit**

```bash
git add upande_agriculture/forecast/camera.py upande_agriculture/forecast/service.py upande_agriculture/upande_agriculture/report/section_harvest_forecast/section_harvest_forecast.py upande_agriculture/tests/test_forecast_scan_factor.py
git commit -m "Harvest forecast: bay scans correct how representative the plots are

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 22: Learned correction (residual model)

**Files:**
- Create: `upande_agriculture/forecast/residual.py`
- Modify: `upande_agriculture/forecast/calibrate.py` (fit after the physical fit), `upande_agriculture/forecast/service.py` (apply)
- Test: `upande_agriculture/tests/test_forecast_residual.py` (pure, numpy)

**Interfaces:**
- Produces:
  - `FEATURES: list[str]`
  - `features(phys, buds, regrowth, h, day, days_since_count, temp_delta, recent_picks) -> list[float]`
  - `fit(X, y, lam=1.0) -> list[float]` (ridge weights)
  - `predict(w, x) -> float` (log correction, clipped to ±0.7)
  - `walk_forward(rows: list[dict], lam=1.0) -> {"weights", "gain", "enabled", "phys_mape", "model_mape"}`. Each row has keys `date, x (features), phys, actual`.
  - `apply(model_json: str, x) -> float` (a multiplier, 1.0 when disabled)

- [ ] **Step 1: Write the failing tests**

`upande_agriculture/tests/test_forecast_residual.py`:
```python
"""Pure: python -m unittest upande_agriculture.tests.test_forecast_residual"""
import datetime
import math
import random
import unittest

from upande_agriculture.forecast import residual

D0 = datetime.date(2026, 1, 5)  # a Monday


def rows(bias_by_dow, noise=0.0, n=300, seed=1):
	rnd = random.Random(seed)
	out = []
	for i in range(n):
		day = D0 + datetime.timedelta(days=i // 3)
		phys = 100.0 + 20 * math.sin(i / 7)
		actual = phys * bias_by_dow[day.weekday()] * (1 + rnd.uniform(-noise, noise))
		x = residual.features(phys, phys * 0.8, phys * 0.2, i % 21, day, i % 3, 0.0, 100.0)
		out.append({"date": day, "x": x, "phys": phys, "actual": actual})
	return out


class TestResidual(unittest.TestCase):
	def test_learns_a_weekday_pattern(self):
		r = residual.walk_forward(rows([1.3, 1.0, 1.0, 1.0, 1.0, 1.0, 0.6]))
		self.assertTrue(r["enabled"])
		self.assertGreater(r["gain"], 0.2)

	def test_pure_noise_is_not_enabled(self):
		r = residual.walk_forward(rows([1.0] * 7, noise=0.3))
		self.assertFalse(r["enabled"])

	def test_apply_disabled_is_one(self):
		self.assertEqual(residual.apply('{"enabled": false, "weights": [1]}', [1.0]), 1.0)
		self.assertEqual(residual.apply(None, [1.0]), 1.0)

	def test_correction_is_clipped(self):
		self.assertEqual(residual.predict([100.0], [1.0]), 0.7)
```

- [ ] **Step 2: Run, expect failure**

Run: `cd /home/teddy5456/frappe-bench/apps/upande_agriculture && ../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_residual -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/residual.py`:
```python
"""Learned correction on top of the physical forecast: ridge regression on
log(actual / physical) with features the physics ignores (weekday harvest
habits, how stale the count is, temperature, recent picking level). It is used
only when walk-forward validation (train on the older 70% of days, test on the
newer 30%) shows it reduces the error by at least MIN_GAIN. numpy only."""
import json
import math

import numpy as np

MIN_GAIN = 0.05
CLIP = 0.7
FEATURES = ["bias", "log_phys", "bud_share", "h", "h2", "mon", "tue", "wed", "thu", "fri", "sat",
	"days_since_count", "temp_delta", "log_recent_picks"]


def features(phys, buds, regrowth, h, day, days_since_count, temp_delta, recent_picks):
	dow = [1.0 if day.weekday() == i else 0.0 for i in range(6)]  # Sunday is the baseline
	share = buds / phys if phys > 0 else 0.0
	return [1.0, math.log1p(phys), share, h / 21, (h / 21) ** 2, *dow,
		days_since_count / 35, temp_delta or 0.0, math.log1p(recent_picks or 0.0)]


def fit(X, y, lam=1.0):
	X, y = np.asarray(X, float), np.asarray(y, float)
	A = X.T @ X + lam * np.eye(X.shape[1])
	A[0, 0] -= lam  # do not shrink the intercept
	return np.linalg.solve(A, X.T @ y).tolist()


def predict(w, x):
	v = float(np.dot(w, x))
	return max(-CLIP, min(CLIP, v))


def _mape(pairs):
	vals = [abs(a - f) / a for f, a in pairs if a > 0]
	return sum(vals) / len(vals) if vals else 0.0


def walk_forward(rows, lam=1.0):
	rows = sorted(rows, key=lambda r: r["date"])
	usable = [r for r in rows if r["phys"] > 0 and r["actual"] >= 0]
	if len(usable) < 60:
		return {"weights": [], "gain": 0.0, "enabled": False, "phys_mape": None, "model_mape": None,
			"reason": f"only {len(usable)} forecast-days"}
	cut = int(len(usable) * 0.7)
	train, test = usable[:cut], usable[cut:]
	target = lambda r: math.log1p(r["actual"]) - math.log1p(r["phys"])
	w = fit([r["x"] for r in train], [target(r) for r in train], lam)
	phys_mape = _mape([(r["phys"], r["actual"]) for r in test])
	model_mape = _mape([(math.expm1(math.log1p(r["phys"]) + predict(w, r["x"])), r["actual"]) for r in test])
	gain = (phys_mape - model_mape) / phys_mape if phys_mape else 0.0
	w_all = fit([r["x"] for r in usable], [target(r) for r in usable], lam)  # final model on all data
	return {"weights": w_all, "gain": round(gain, 4), "enabled": gain >= MIN_GAIN,
		"phys_mape": round(phys_mape, 4), "model_mape": round(model_mape, 4)}


def apply(model_json, x):
	"""Multiplier for the physical forecast (1.0 when off)."""
	if not model_json:
		return 1.0
	m = json.loads(model_json) if isinstance(model_json, str) else model_json
	if not m.get("enabled") or not m.get("weights"):
		return 1.0
	phys_log = x[1]
	corrected = math.expm1(phys_log + predict(m["weights"], x))
	base = math.expm1(phys_log)
	return corrected / base if base > 0 else 1.0
```

- [ ] **Step 4: Run, expect PASS**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_residual -v`
Expected: 4 tests OK.

- [ ] **Step 5: Fit it in calibration, apply it in service**

In `forecast/calibrate.py`:
- add `from upande_agriculture.forecast import residual` to the imports;
- after the bands loop and before `doc.update(...)`, build rows and fit:
```python
	res_rows = []
	for c in cases:
		b, gr = _curves(c, stages, k, R, c["tf"])
		recent = sum(v for d, v in c["cuts"].items() if (c["date"] - d).days < 7) / 7
		for i, (x, y) in enumerate(zip(b, gr)):
			phys = s * x + g * y
			day = add_days(c["date"], i + 1)
			res_rows.append({"date": day, "phys": phys, "actual": c["actual"][i],
				"x": residual.features(phys, s * x, g * y, i + 1, day, i + 1, (c["tf"] - 1.0), recent)})
	rm = residual.walk_forward(res_rows)
```
- add to `doc.update({...})`: `"residual_model": json.dumps(rm), "residual_gain": rm["gain"], "residual_enabled": int(rm["enabled"]),`.

In `forecast/data.py` `params()`:
- add `"residual_model": None` to the defaults dict;
- read `residual_model` in the `get_value` field list;
- set `p["residual_model"] = cal.residual_model` inside the fitted branch.

In `forecast/service.py`:
- add `from upande_agriculture.forecast import residual` and `import frappe`;
- before building `daily`:
```python
	use_learned = prm.get("residual_model") and frappe.db.get_single_value("Agriculture Settings", "learned_correction")
	recent = sum(v for d, v in history.items() if (as_of - d).days < 7) / 7
	days_since = (as_of - rnd["date"]).days if rnd else 35
```
- inside the daily loop, before computing the band:
```python
		if use_learned and stems > 0:
			x = residual.features(stems, res["buds"][i], res["regrowth"][i], (start - as_of).days + i,
				add_days(start, i), days_since + i, tf - 1.0, recent)
			stems = stems * residual.apply(prm["residual_model"], x)
```
  (`stems` is the loop variable from `enumerate(res["stems"])`; reassigning it before `lo, hi` keeps the band proportional.)
- add `"learned": bool(use_learned)` to `flags`.

- [ ] **Step 6: Run the residual, calibrate and service tests**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_residual && cd ../.. && for m in test_forecast_calibrate test_forecast_service test_forecast_api test_forecast_reports; do bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.$m 2>&1 | tail -2; done`
Expected: all OK. The simulated crop has no weekday pattern, so the calibration test leaves `residual_enabled` 0 or barely positive. Either is fine; nothing asserts on it there.

- [ ] **Step 7: Commit**

```bash
cd apps/upande_agriculture && git add upande_agriculture/forecast/residual.py upande_agriculture/forecast/calibrate.py upande_agriculture/forecast/data.py upande_agriculture/forecast/service.py upande_agriculture/tests/test_forecast_residual.py
git commit -m "Harvest forecast: learned correction, used only when it beats the physics on held-out weeks

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 23: Sequence model (GRU trained offline, numpy inference)

**Files:**
- Create: `upande_agriculture/forecast/sequence.py`
- Modify: `upande_agriculture/forecast/service.py` (apply per horizon bucket)
- Test: `upande_agriculture/tests/test_forecast_sequence.py` (pure)
- Create: `/home/teddy5456/upande-vision/train/seq_forecast.py` (+ a parity test in `tests/test_stage_tools.py`)

**Interfaces:**
- Produces (ERP):
  - `SEQ_DAYS = 28`, `OUT_DAYS = 14`
  - `gru_forward(weights: dict, xs: list[list[float]]) -> list[float]` (final hidden state)
  - `predict(model: dict, xs) -> list[float]` (OUT_DAYS stems, de-log1p)
  - `inputs_for(history_picks: list[float], phys: list[float], rates: list[float], temp_delta: float) -> list[list[float]]`
  - `export_forecast_history(variety=None) -> {"file_url", "rows"}` (whitelisted, System Manager): JSON lines per section-day `{greenhouse, section, variety, date, picks, phys, rates: [5], temp_delta}`, where `phys` is the physical forecast for that day made 1 day earlier
  - `apply(model, ...)`, used by service
  - Model JSON: `{"meta": {"buckets_better": ["0-2", ...], "val": {...}, "trained_on": n}, "weights": {"w_ih": [[...]], "w_hh": [[...]], "b_ih": [...], "b_hh": [...], "w_out": [[...]], "b_out": [...]}, "hidden": H, "inputs": I}`
- Produces (offline): `seq_forecast.py <history.jsonl> <out.json>`

- [ ] **Step 1: Write the failing numpy tests**

`upande_agriculture/tests/test_forecast_sequence.py`:
```python
"""Pure: python -m unittest upande_agriculture.tests.test_forecast_sequence"""
import math
import unittest

from upande_agriculture.forecast import sequence


def sig(v):
	return 1 / (1 + math.exp(-v))


class TestSequence(unittest.TestCase):
	def test_one_unit_gru_matches_the_formula(self):
		# PyTorch GRU gate order in w_ih/w_hh rows: reset, update, new.
		w = {"w_ih": [[0.5], [0.2], [0.3]], "w_hh": [[0.1], [0.4], [0.6]], "b_ih": [0.0, 0.1, 0.0], "b_hh": [0.0, 0.0, 0.2]}
		h = 0.0
		for x in (1.0, -0.5):
			r = sig(0.5 * x + 0.1 * h)
			z = sig(0.2 * x + 0.1 + 0.4 * h)
			n = math.tanh(0.3 * x + r * (0.6 * h + 0.2))
			h = (1 - z) * n + z * h
		self.assertAlmostEqual(sequence.gru_forward(w, [[1.0], [-0.5]])[0], h, places=9)

	def test_predict_returns_out_days(self):
		H, I = 2, 1
		model = {"hidden": H, "inputs": I, "weights": {
			"w_ih": [[0.1]] * (3 * H), "w_hh": [[0.0] * H for _ in range(3 * H)], "b_ih": [0.0] * (3 * H),
			"b_hh": [0.0] * (3 * H), "w_out": [[0.0] * H for _ in range(sequence.OUT_DAYS)], "b_out": [math.log1p(50)] * sequence.OUT_DAYS},
			"meta": {"buckets_better": ["0-2"]}}
		out = sequence.predict(model, [[0.0]] * 3)
		self.assertEqual(len(out), sequence.OUT_DAYS)
		self.assertAlmostEqual(out[0], 50, places=6)

	def test_apply_only_better_buckets(self):
		model = {"meta": {"buckets_better": ["0-2"]}}
		self.assertEqual(sequence.choose([100.0] * 5, [80.0] * 5, model), [80.0, 80.0, 80.0, 100.0, 100.0])
		self.assertEqual(sequence.choose([100.0] * 2, [80.0] * 2, None), [100.0, 100.0])
```

- [ ] **Step 2: Run, expect failure**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_sequence -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

`upande_agriculture/forecast/sequence.py`:
```python
"""Sequence model: a small GRU over each section's last 28 days (picks, the
physical forecast, stage rates, temperature) predicting the next 14 days.
Trained offline in torch (upande-vision/train/seq_forecast.py, which refuses
too little history); here only the forward pass runs, in numpy. It replaces
the physical forecast only in the horizon buckets where it validated better."""
import io
import json
import math

import frappe
import numpy as np
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import curves

SEQ_DAYS, OUT_DAYS = 28, 14
STAGES = ["Rice", "Pea", "Chickpea", "Showing colour", "Opening"]


def _sig(v):
	return 1.0 / (1.0 + np.exp(-v))


def gru_forward(weights, xs):
	"""PyTorch-compatible single-layer GRU; returns the last hidden state."""
	w_ih, w_hh = np.asarray(weights["w_ih"], float), np.asarray(weights["w_hh"], float)
	b_ih, b_hh = np.asarray(weights["b_ih"], float), np.asarray(weights["b_hh"], float)
	H = w_hh.shape[1]
	h = np.zeros(H)
	for x in xs:
		gi = w_ih @ np.asarray(x, float) + b_ih
		gh = w_hh @ h + b_hh
		r = _sig(gi[:H] + gh[:H])
		z = _sig(gi[H:2 * H] + gh[H:2 * H])
		n = np.tanh(gi[2 * H:] + r * gh[2 * H:])
		h = (1 - z) * n + z * h
	return h.tolist()


def predict(model, xs):
	w = model["weights"]
	h = np.asarray(gru_forward(w, xs))
	y = np.asarray(w["w_out"], float) @ h + np.asarray(w["b_out"], float)
	return [float(max(0.0, math.expm1(v))) for v in y[:OUT_DAYS]]


def inputs_for(history_picks, phys, rates, temp_delta):
	"""One row per past day: log1p(picks), log1p(phys), stage rates, temp delta."""
	return [[math.log1p(p), math.log1p(f), *rates, temp_delta or 0.0] for p, f in zip(history_picks, phys)]


def choose(physical, learned, model):
	"""Per day: the learned value where its horizon bucket validated better."""
	better = set((model or {}).get("meta", {}).get("buckets_better") or [])
	return [learned[i] if i < len(learned) and curves.bucket(i) in better else p for i, p in enumerate(physical)]


def load_model():
	url = frappe.db.get_single_value("Agriculture Settings", "sequence_model")
	if not url:
		return None
	name = frappe.db.get_value("File", {"file_url": url})
	return json.loads(frappe.get_doc("File", name).get_content()) if name else None


@frappe.whitelist()
def export_forecast_history(variety=None):
	"""JSON lines, one per section-day, for the offline trainer: picks, the
	physical forecast made the day before, the latest round's stage rates."""
	frappe.only_for("System Manager")
	from upande_agriculture.forecast import data, service

	today = getdate(nowdate())
	lines = []
	for row in data.sections(variety=variety):
		if not row.variety:
			continue
		first = frappe.get_all("Bed Sample", pluck="sampling_date", order_by="sampling_date",
			filters={"greenhouse": row.greenhouse, "section": row.section}, limit=1)
		if not first:
			continue
		picks = data.cuts(row.greenhouse, row.section, first[0], today)
		day = getdate(first[0])
		while day < today:
			f = service.forecast_section(row, start=day, days=1, as_of=add_days(day, -1))
			rnd = data.latest_round(row.greenhouse, row.section, add_days(day, -1))
			rates = [round((rnd["rates"].get(s, 0.0) if rnd else 0.0), 4) for s in STAGES]
			lines.append(json.dumps({"greenhouse": row.greenhouse, "section": row.section, "variety": row.variety,
				"date": str(day), "picks": picks.get(day, 0.0), "phys": round(f["daily"][0]["stems"], 2),
				"rates": rates, "temp_delta": 0.0}))
			day = add_days(day, 1)
	f = frappe.get_doc({"doctype": "File", "file_name": f"forecast-history-{today}.jsonl",
		"content": "\n".join(lines).encode(), "is_private": 1}).insert(ignore_permissions=True)
	return {"file_url": f.file_url, "rows": len(lines)}
```

- [ ] **Step 4: Run, expect PASS**

Run: `../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_sequence -v`
Expected: 3 tests OK.

- [ ] **Step 5: Apply it in service**

In `forecast/service.py`:
- add `from upande_agriculture.forecast import sequence` to the imports;
- after the scan factor and before the daily loop (leaving the residual in place), insert:
```python
	seq = sequence.load_model()
	if seq and plants and rnd:
		past = [history.get(add_days(as_of, -SEQ) , 0.0) for SEQ in range(sequence.SEQ_DAYS, 0, -1)]
		rates = [rnd["rates"].get(s, 0.0) for s in sequence.STAGES]
		xs = sequence.inputs_for(past, past, rates, tf - 1.0)
		learned = sequence.predict(seq, xs)
		res["stems"] = sequence.choose(res["stems"], learned[(start - as_of).days:], seq)
```
  Past physical forecasts aren't stored per day, so `past` stands in for `phys` at inference. The trainer must train on the same substitution: Step 6 uses `phys = picks` for the input window too. Using exported phys only for evaluation keeps training and serving consistent.
- add `"sequence": bool(seq and plants and rnd)` to `flags`.

- [ ] **Step 6: The offline trainer**

`/home/teddy5456/upande-vision/train/seq_forecast.py`:
```python
"""Train the sequence model from export_forecast_history's JSON lines.

    venv/bin/python seq_forecast.py history.jsonl seq_model.json

Windows of 28 days -> next 14 days, per section. Train on the oldest 70% of
windows, validate on the newest 30%, against the physical forecast for the
same days. Writes weights + the horizon buckets where the GRU was better.
Refuses with fewer than MIN_WINDOWS windows (months of history needed)."""
import json
import math
import sys
from collections import defaultdict

import torch

SEQ, OUT, H = 28, 14, 16
MIN_WINDOWS = 300
BUCKETS = [(0, 2, "0-2"), (3, 6, "3-6"), (7, 13, "7-13")]


def windows(lines):
	by = defaultdict(list)
	for r in lines:
		by[(r["greenhouse"], r["section"])].append(r)
	out = []
	for rows in by.values():
		rows.sort(key=lambda r: r["date"])
		for i in range(SEQ, len(rows) - OUT + 1):
			past, fut = rows[i - SEQ:i], rows[i:i + OUT]
			x = [[math.log1p(p["picks"]), math.log1p(p["picks"]), *fut[0]["rates"], p["temp_delta"]] for p in past]
			out.append((fut[0]["date"], x, [math.log1p(f["picks"]) for f in fut], [f["phys"] for f in fut], [f["picks"] for f in fut]))
	return sorted(out, key=lambda w: w[0])


class Net(torch.nn.Module):
	def __init__(self, inputs):
		super().__init__()
		self.gru = torch.nn.GRU(inputs, H, batch_first=True)
		self.out = torch.nn.Linear(H, OUT)

	def forward(self, x):
		_, h = self.gru(x)
		return self.out(h[-1])


def mape(pred, actual):
	v = [abs(a - p) / a for p, a in zip(pred, actual) if a > 0]
	return sum(v) / len(v) if v else None


def main(src, dst):
	lines = [json.loads(l) for l in open(src) if l.strip()]
	ws = windows(lines)
	if len(ws) < MIN_WINDOWS:
		sys.exit(f"Only {len(ws)} training windows; need {MIN_WINDOWS} (about 10 sections x 30+ days beyond the first 42).")
	cut = int(len(ws) * 0.7)
	tr, va = ws[:cut], ws[cut:]
	X = torch.tensor([w[1] for w in tr], dtype=torch.float32)
	Y = torch.tensor([w[2] for w in tr], dtype=torch.float32)
	net = Net(X.shape[2])
	opt = torch.optim.Adam(net.parameters(), lr=3e-3)
	for epoch in range(300):
		opt.zero_grad()
		loss = torch.nn.functional.l1_loss(net(X), Y)
		loss.backward()
		opt.step()
	with torch.no_grad():
		P = torch.expm1(net(torch.tensor([w[1] for w in va], dtype=torch.float32))).clamp(min=0).tolist()
	better, val = [], {}
	for lo, hi, label in BUCKETS:
		seq_m = mape([p for w, ps in zip(va, P) for i, p in enumerate(ps) if lo <= i <= hi],
			[a for w in va for i, a in enumerate(w[4]) if lo <= i <= hi])
		phys_m = mape([p for w in va for i, p in enumerate(w[3]) if lo <= i <= hi],
			[a for w in va for i, a in enumerate(w[4]) if lo <= i <= hi])
		val[label] = {"sequence": seq_m, "physical": phys_m}
		if seq_m is not None and phys_m is not None and seq_m < phys_m:
			better.append(label)
	sd = net.state_dict()
	model = {"hidden": H, "inputs": X.shape[2], "meta": {"buckets_better": better, "val": val, "trained_on": len(tr)},
		"weights": {"w_ih": sd["gru.weight_ih_l0"].tolist(), "w_hh": sd["gru.weight_hh_l0"].tolist(),
			"b_ih": sd["gru.bias_ih_l0"].tolist(), "b_hh": sd["gru.bias_hh_l0"].tolist(),
			"w_out": sd["out.weight"].tolist(), "b_out": sd["out.bias"].tolist()}}
	json.dump(model, open(dst, "w"))
	print(json.dumps(model["meta"], indent=1))


if __name__ == "__main__":
	main(sys.argv[1], sys.argv[2])
```

Add the torch/numpy parity test to `/home/teddy5456/upande-vision/train/tests/test_stage_tools.py`:
```python
class TestSequenceParity(unittest.TestCase):
	def test_numpy_gru_matches_torch(self):
		import sys
		sys.path.insert(0, "/home/teddy5456/frappe-bench/apps/upande_agriculture")
		import torch
		from seq_forecast import Net

		torch.manual_seed(0)
		net = Net(7)
		x = torch.randn(1, 28, 7)
		want = torch.expm1(net(x))[0].clamp(min=0).tolist()
		sd = net.state_dict()
		w = {"w_ih": sd["gru.weight_ih_l0"].tolist(), "w_hh": sd["gru.weight_hh_l0"].tolist(),
			"b_ih": sd["gru.bias_ih_l0"].tolist(), "b_hh": sd["gru.bias_hh_l0"].tolist(),
			"w_out": sd["out.weight"].tolist(), "b_out": sd["out.bias"].tolist()}
		import importlib.util
		spec = importlib.util.spec_from_file_location("seqnp",
			"/home/teddy5456/frappe-bench/apps/upande_agriculture/upande_agriculture/forecast/sequence.py")
		# sequence.py imports frappe; test the pure functions by exec'ing them without frappe
		src = open(spec.origin).read().split("@frappe.whitelist()")[0]
		src = src.replace("import frappe\n", "").replace("from frappe.utils import add_days, getdate, nowdate\n", "")
		src = src.replace("from upande_agriculture.forecast import curves\n", "")
		ns = {}
		exec(src, ns)
		got = ns["predict"]({"weights": w}, x[0].tolist())
		for a, b in zip(got, want):
			self.assertAlmostEqual(a, b, places=4)
```

- [ ] **Step 7: Run all**

Run:
```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture && ../../env/bin/python -m unittest upande_agriculture.tests.test_forecast_sequence
cd /home/teddy5456/upande-vision/train && venv/bin/python -m unittest tests.test_stage_tools -v
cd /home/teddy5456/frappe-bench && for m in test_forecast_service test_forecast_api; do bench --site bhcloud.local run-tests --app upande_agriculture --module upande_agriculture.tests.$m 2>&1 | tail -2; done
```
Expected: all OK, including `test_numpy_gru_matches_torch`.

- [ ] **Step 8: Commit**

```bash
cd /home/teddy5456/frappe-bench/apps/upande_agriculture && git add upande_agriculture/forecast/sequence.py upande_agriculture/forecast/service.py upande_agriculture/tests/test_forecast_sequence.py
git commit -m "Harvest forecast: GRU sequence model (offline torch, numpy inference), used per horizon only where it validated better

Co-Authored-By: claude-flow <ruv@ruv.net>"
```

---

### Task 24: Ship Part 2

- [ ] **Step 1:** Run every forecast test module (Part 1 list plus `test_forecast_imagery_doctypes`, `test_forecast_camera`, `test_forecast_scan_factor`), the pure suites (`curves, engine, residual, sequence`) and `upande-vision/train/tests`. Expected: all OK.
- [ ] **Step 2:** `git push -q origin bilashaka` (upande_agriculture). `git status` shows only the untouched files from the Part 1 global constraints.
- [ ] **Step 3:** OTA:
```bash
cd /home/teddy5456/bilashaka-harvest && NODE_OPTIONS="--dns-result-order=ipv4first --network-family-autoselection-attempt-timeout=10000" npx eas update --channel preview --message "Camera-guided plot counts and bay walk scans" --non-interactive 2>&1 | tail -6
```
- [ ] **Step 4:** Record in memory:
  - the off-cloud tools (`stage_label.py`, `check_stage_model.py`, `fetch_stage_dataset.py`, `attach_stage_model.py`, `process_bay_scans.py`, `seq_forecast.py`, `kaggle/stage_job`);
  - the thresholds: ≥150 photos to train the stage model; ≥300 windows for the sequence model; gates MAE ≤ 0.15 / ρ ≥ 0.6; residual gain ≥ 5%;
  - the operating loop: weekly `fetch_stage_dataset.py` → Kaggle push → `watch` → `attach_stage_model.py` if the gate passes; after drone flights, `process_bay_scans.py`; monthly `export_forecast_history` → `seq_forecast.py` → attach JSON to Agriculture Settings `sequence_model`.
