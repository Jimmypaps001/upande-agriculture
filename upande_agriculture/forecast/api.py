"""Phone endpoints for plot counting and the bay forecast."""
import base64
import io
import json

import frappe
from frappe.utils import getdate, nowdate
from PIL import Image

from upande_agriculture.forecast import calibrate as calib
from upande_agriculture.forecast import data, service

COUNT_INTERVAL_DAYS = 3  # ponytail: one interval for every section; make it a setting when sections differ
PLOT_PLANTS = 10
MAX_PHOTO_BYTES = 8 * 1024 * 1024


def ensure_plots(row):
	"""Two plots per section, a third and two thirds of the way along its beds
	(one plot when the section is a single bed). Created the first time the
	plan is asked for, so new sections need no setup. Returns True if it created any."""
	if frappe.db.exists("Sample Plot", {"greenhouse": row.greenhouse, "section": row.section}):
		return False
	lo = int(row.from_bed or 1)
	hi = int(row.to_bed or lo)
	beds = [lo + (hi - lo) // 3, lo + 2 * (hi - lo) // 3] if hi > lo else [lo]
	for n, bed in enumerate(beds, start=1):
		frappe.get_doc({"doctype": "Sample Plot", "greenhouse": row.greenhouse, "section": row.section,
			"plot_no": n, "bed": bed, "plants": PLOT_PLANTS}).insert(ignore_permissions=True)
	return True


@frappe.whitelist()
def get_plot_plan(farm=None):
	"""Every active plot on the farm's sections, the user's own sections first,
	then plots due a count."""
	frappe.has_permission("Sample Plot", "read", throw=True)
	today = getdate(nowdate())
	me = frappe.db.get_value("Employee", {"user_id": frappe.session.user})
	plots = []
	created = False
	for row in data.sections(farm=farm or None):
		if not row.variety:
			continue
		created = ensure_plots(row) or created
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
	if created:
		frappe.db.commit()  # plots made by ensure_plots
	return {"count_interval_days": COUNT_INTERVAL_DAYS, "plots": plots}


@frappe.whitelist(methods=["POST"])
def submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None):
	"""One plot count. Replaying the same client_uuid (offline queue after a
	lost response) returns the first record instead of a duplicate."""
	frappe.has_permission("Bed Sample", "create", throw=True)
	if not (client_uuid or "").strip():
		frappe.throw("client_uuid is required.")
	existing = frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid})
	if existing:
		return {"name": existing}
	counts = json.loads(counts) if isinstance(counts, str) else counts
	photos = json.loads(photos) if isinstance(photos, str) else (photos or [])
	for r in counts:
		if not r.get("stage_name"):
			frappe.throw("Every count needs a stage_name.")
		try:
			ok = int(r.get("count") or 0) >= 0
		except (TypeError, ValueError):
			ok = False
		if not ok:
			frappe.throw(f"Count for {r.get('stage_name')} must be a whole number, 0 or more.")
	plot = frappe.get_doc("Sample Plot", sample_plot)
	days = {n: round(v[0]) for n, v in data.stages(plot.variety).items()}
	try:
		doc = frappe.get_doc({
		"doctype": "Bed Sample", "client_uuid": client_uuid, "sample_plot": plot.name,
		"plants_counted": int(plants_counted or plot.plants or PLOT_PLANTS), "notes": notes,
		"sampling_date": getdate(captured_at) if captured_at else nowdate(), "captured_at": captured_at,
		"stages": [{"stage_name": r["stage_name"], "count": int(r.get("count") or 0),
			"days_to_harvest": days.get(r["stage_name"])} for r in counts],
		}).insert()
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):  # replay raced the first insert
		return {"name": frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid})}
	skipped = []
	for i, b64 in enumerate(photos[:2], start=1):
		try:
			raw = base64.b64decode(b64, validate=True)
		except (ValueError, TypeError):
			skipped.append(f"photo {i} skipped: not valid base64")
			continue
		if len(raw) > MAX_PHOTO_BYTES:
			skipped.append(f"photo {i} skipped: larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB")
			continue
		try:
			Image.open(io.BytesIO(raw)).verify()
		except Exception:
			skipped.append(f"photo {i} skipped: not an image")
			continue
		f = frappe.get_doc({"doctype": "File", "file_name": f"{doc.name}-{i}.jpg",
			"content": raw, "attached_to_doctype": "Bed Sample",
			"attached_to_name": doc.name, "attached_to_field": f"photo_{i}", "is_private": 1,
		}).insert(ignore_permissions=True)
		doc.db_set(f"photo_{i}", f.file_url)  # inserting a File does not fill the Attach field
	if skipped:
		doc.db_set("notes", "\n".join(filter(None, [doc.notes, *skipped])))
	return {"name": doc.name}


@frappe.whitelist()
def get_bay_forecast(greenhouse, section, days=7):
	frappe.has_permission("Sample Plot", "read", throw=True)
	try:
		days = min(max(int(days), 2), 60)
	except (TypeError, ValueError):
		frappe.throw("days must be a whole number.")
	row = next((r for r in data.sections(greenhouse=greenhouse) if r.section == section), None)
	if not row:
		frappe.throw(f"Section {section} is not in {greenhouse}.")
	f = service.forecast_section(row, days=days)
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
