# Bed sampling: the phone counts shoots per growth stage on a measured stretch
# of bed. This phase only GATHERS samples (plus photos for a later "tight bud"
# model); the forecast that scales them up to a greenhouse comes next.

import base64
import json
import math
import random

import frappe
from frappe.utils import getdate, nowdate

# Used when a variety's Crop Protocol has no Growth Stages yet, so sampling can
# start before the protocols are filled in. Days to harvest stay blank.
DEFAULT_STAGES = ["Rice", "Pea", "Chickpea", "Marble", "Colour showing"]
DEFAULT_SAMPLE_LENGTH_M = 1
# ponytail: fixed 5% of beds (at least 3) per crop cycle per week; make it a
# setting once the forecast shows how many samples the margin of error needs.
SAMPLE_FRACTION, MIN_SAMPLES = 0.05, 3


@frappe.whitelist()
def get_sampling_plan():
	"""Every active crop cycle with its stages, beds and this week's suggested beds."""
	year, week, _ = getdate(nowdate()).isocalendar()
	week_start = frappe.utils.add_days(nowdate(), -(getdate(nowdate()).weekday()))
	cycles = []
	for c in frappe.get_all(
		"Crop Cycle",
		filters={"status": "Active"},
		fields=["name", "greenhouse", "variety", "crop_protocol", "bed_range", "title"],
		order_by="greenhouse, variety",
	):
		beds = _beds(c)
		k = min(len(beds), max(MIN_SAMPLES, math.ceil(len(beds) * SAMPLE_FRACTION)))
		# Same suggestion on every phone for the whole week; a new draw each week.
		suggested = sorted(random.Random(f"{c.name}-{year}-{week}").sample(beds, k))
		cycles.append({
			"crop_cycle": c.name,
			"greenhouse": c.greenhouse,
			"variety": c.variety,
			"title": c.title or f"{c.greenhouse} · {c.variety}",
			"stages": _stages(c.crop_protocol),
			"beds": beds,
			"suggested": suggested,
			"sampled_this_week": sorted(set(frappe.get_all(
				"Bed Sample",
				filters={"crop_cycle": c.name, "sampling_date": [">=", week_start]},
				pluck="bed_number",
			))),
		})
	return {"sample_length_m": DEFAULT_SAMPLE_LENGTH_M, "week": week, "cycles": cycles}


@frappe.whitelist(methods=["POST"])
def submit_bed_sample(client_uuid, crop_cycle, bed_number, counts, sample_length_m=None,
					  suggested=0, notes=None, captured_at=None, photos=None):
	"""Saves one sample. Replaying the same client_uuid (offline queue after a
	lost response) returns the first record instead of a duplicate."""
	existing = frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid})
	if existing:
		return {"name": existing}
	counts = json.loads(counts) if isinstance(counts, str) else counts
	photos = json.loads(photos) if isinstance(photos, str) else (photos or [])
	protocol = frappe.db.get_value("Crop Cycle", crop_cycle, "crop_protocol")
	days = {s["stage_name"]: s["days_to_harvest"] for s in _stages(protocol)}

	doc = frappe.get_doc({
		"doctype": "Bed Sample",
		"client_uuid": client_uuid,
		"crop_cycle": crop_cycle,
		"bed_number": int(bed_number),
		"sample_length_m": float(sample_length_m or DEFAULT_SAMPLE_LENGTH_M),
		"suggested": int(bool(int(suggested or 0))),
		"notes": notes,
		"sampling_date": getdate(captured_at) if captured_at else nowdate(),
		"captured_at": captured_at,
		"stages": [
			{"stage_name": r["stage_name"], "count": int(r.get("count") or 0), "days_to_harvest": days.get(r["stage_name"])}
			for r in counts
		],
	}).insert()

	for i, b64 in enumerate(photos[:2], start=1):
		f = frappe.get_doc({
			"doctype": "File",
			"file_name": f"{doc.name}-{i}.jpg",
			"content": base64.b64decode(b64),
			"attached_to_doctype": "Bed Sample",
			"attached_to_name": doc.name,
			"attached_to_field": f"photo_{i}",
			"is_private": 1,
		}).insert(ignore_permissions=True)
		# Inserting the File does not fill the Attach field itself.
		doc.db_set(f"photo_{i}", f.file_url)
	return {"name": doc.name}


def _stages(protocol):
	rows = protocol and frappe.get_all(
		"Crop Protocol Growth Stage",
		filters={"parent": protocol, "parenttype": "Crop Protocol"},
		fields=["stage_name", "days_to_harvest"],
		order_by="idx",
	)
	return rows or [{"stage_name": s, "days_to_harvest": None} for s in DEFAULT_STAGES]


def _beds(cycle):
	"""Bed numbers of a crop cycle: its Beds table, else its bed range text
	("1-20, 25-30"), else every bed in the greenhouse."""
	beds = frappe.get_all(
		"Crop Cycle Bed", filters={"parent": cycle.name, "parenttype": "Crop Cycle"}, pluck="bed"
	)
	if beds:
		return sorted(set(frappe.get_all("Bed", filters={"name": ["in", beds]}, pluck="bed")))
	nums = set()
	for part in (cycle.bed_range or "").replace(";", ",").split(","):
		lo, _, hi = part.strip().partition("-")
		if lo.strip().isdigit():
			nums.update(range(int(lo), int(hi if hi.strip().isdigit() else lo) + 1))
	if nums:
		return sorted(nums)
	return sorted(set(frappe.get_all("Bed", filters={"greenhouse": cycle.greenhouse}, pluck="bed")))
