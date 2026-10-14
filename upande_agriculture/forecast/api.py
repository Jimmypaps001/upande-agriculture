"""Phone endpoints for plot counting and the bay forecast."""
import base64
import hashlib
import io
import json

import frappe
import qrcode
from frappe.utils import getdate, nowdate
from frappe.utils.pdf import get_pdf
from PIL import Image

from upande_agriculture.forecast import calibrate as calib
from upande_agriculture.forecast import data, service

COUNT_INTERVAL_DAYS = 3  # ponytail: one interval for every section; make it a setting when sections differ
SPOTS, SPOT_PLANTS = 8, 5  # 8 spots of 5 plants per section beat 1-2 big plots (plot-to-plot CV ~35-50%)
POSITIONS = ("¼", "½", "¾")
MAX_PHOTO_BYTES = 8 * 1024 * 1024
FAST_SECONDS = 15  # quicker than this for a 5-plant spot was not a real count


def spot_layout(key, from_bed, to_bed):
	"""[(bed, position)] for SPOTS spots spread systematically over the section:
	never the first or last bed, a start seeded from `key` so it never moves,
	and the position along the bed rotating ¼, ½, ¾. Bed None when the section
	has no bed range (the set-up scan fills it in)."""
	if not from_bed or not to_bed:
		return [(None, POSITIONS[i % 3]) for i in range(SPOTS)]
	lo, n = int(from_bed) + 1, int(to_bed) - int(from_bed) - 1
	if n < 1:  # one or two beds: use them all
		lo, n = int(from_bed), int(to_bed) - int(from_bed) + 1
	step = n / SPOTS
	start = int(hashlib.md5(key.encode()).hexdigest(), 16) % 1000 / 1000 * step
	# a section of under 3 usable beds has fewer than 8 distinct places: one spot each
	return list(dict.fromkeys((lo + int(start + i * step), POSITIONS[i % 3]) for i in range(SPOTS)))


def ensure_plots(row):
	"""Tops the section up to its spot_layout spots of SPOT_PLANTS plants, when
	the plan is asked for (and once at migrate), so new sections need no setup.
	Existing spots stay as they are; a section whose spots were all switched off
	stays off. Returns True if it created any."""
	layout = spot_layout(f"{row.greenhouse}|{row.section}", row.from_bed, row.to_bed)
	# Two phones loading the plan at once must not both top up: lock the greenhouse,
	# then read the spots with a locking read (it sees rows committed while we waited).
	frappe.db.sql("SELECT name FROM `tabWarehouse` WHERE name = %s FOR UPDATE", row.greenhouse)
	spots = frappe.db.sql("""SELECT bed, active, plot_no FROM `tabSample Plot`
		WHERE greenhouse = %s AND section = %s FOR UPDATE""", (row.greenhouse, row.section), as_dict=True)
	active = [p.bed for p in spots if p.active]
	if len(active) >= len(layout) or (spots and not active):
		return False
	for bed in active:  # an existing spot stands in for the planned one nearest it
		layout.remove(min(layout, key=lambda s: abs((s[0] or 0) - (bed or 0))))
	n = max([p.plot_no or 0 for p in spots] or [0])
	for bed, position in layout:
		n += 1
		frappe.get_doc({"doctype": "Sample Plot", "greenhouse": row.greenhouse, "section": row.section,
			"plot_no": n, "bed": bed, "position": position, "plants": SPOT_PLANTS, "set_up": 0,
		}).insert(ignore_permissions=True)
	return True


def _stage_counts(names):
	"""{bed sample: {stage: count}}"""
	out = {n: {} for n in names}
	for r in frappe.get_all("Bed Sample Stage", fields=["parent", "stage_name", "count"],
			filters={"parenttype": "Bed Sample", "parent": ["in", list(names) or [""]]}):
		out[r.parent][r.stage_name] = out[r.parent].get(r.stage_name, 0) + (r.count or 0)
	return out


def _last_counts(plots):
	"""{plot: {date, counts, blind_shoots, missing_plants}} from each plot's latest count."""
	if not plots:
		return {}
	rows = frappe.db.sql(
		"""SELECT b.name, b.sample_plot, b.sampling_date, b.blind_shoots, b.missing_plants
		   FROM `tabBed Sample` b
		   JOIN (SELECT sample_plot, MAX(sampling_date) AS d FROM `tabBed Sample`
		         WHERE sample_plot IN %(plots)s GROUP BY sample_plot) m
		     ON m.sample_plot = b.sample_plot AND m.d = b.sampling_date
		   ORDER BY b.creation""", {"plots": tuple(plots)}, as_dict=True)
	latest = {r.sample_plot: r for r in rows}  # the day's last count wins
	counts = _stage_counts([r.name for r in latest.values()])
	return {p: {"date": str(r.sampling_date), "counts": counts[r.name], "blind_shoots": r.blind_shoots or 0,
		"missing_plants": r.missing_plants or 0} for p, r in latest.items()}


@frappe.whitelist()
def get_plot_plan(farm=None):
	"""Every active spot on the farm's sections, the user's own sections first,
	then spots due a count, then the walking route (by bed) within a section."""
	frappe.has_permission("Sample Plot", "read", throw=True)
	today = getdate(nowdate())
	me = frappe.db.get_value("Employee", {"user_id": frappe.session.user})
	found = []
	created = False
	can_create = frappe.has_permission("Sample Plot", "create")  # read-only users (sales) never write
	for row in data.sections(farm=farm or None):
		if not row.variety:
			continue
		if can_create:
			created = ensure_plots(row) or created
		stages = [{"stage_name": n, "days_to_harvest": round(v[0])} for n, v in data.stages(row.variety).items()]
		spots = frappe.get_all("Sample Plot", filters={"greenhouse": row.greenhouse, "section": row.section, "active": 1},
			fields=["name", "title", "plot_no", "bed", "position", "plants", "set_up"])
		spots.sort(key=lambda p: (not p.bed, p.bed or 0, p.plot_no))
		found += [(row, stages, n, p) for n, p in enumerate(spots, start=1)]
	last = _last_counts([p.name for _r, _s, _n, p in found])
	plots = []
	for row, stages, route_no, p in found:
		lc = last.get(p.name)
		d = getdate(lc["date"]) if lc else None
		plots.append({"plot": p.name, "title": p.title, "greenhouse": row.greenhouse,
			"greenhouse_name": row.warehouse_name, "section": row.section, "variety": row.variety,
			"plants": p.plants, "bed": p.bed, "plot_no": p.plot_no, "position": p.position or None,
			"set_up": p.set_up, "route_no": route_no, "from_bed": row.from_bed, "to_bed": row.to_bed, "stages": stages, "last": lc,
			"last_counted": lc["date"] if lc else None,
			"due": not d or (today - d).days >= COUNT_INTERVAL_DAYS,
			"mine": bool(me and row.employee == me)})
	plots.sort(key=lambda p: (not p["mine"], not p["due"], p["greenhouse_name"] or "", p["section"], p["route_no"]))
	if created:
		frappe.db.commit()  # plots made by ensure_plots
	return {"count_interval_days": COUNT_INTERVAL_DAYS, "plots": plots}


def quality_flags(counts, prev, order, duration_seconds=None, missing_plants=0):
	"""Short checks on one spot count against its previous one. `order`: stage
	names youngest first (a bud moves at most one stage between counts)."""
	flags = []
	if prev is not None:
		for name in sorted(set(counts) | set(prev), key=lambda n: order.index(n) if n in order else len(order)):
			a, b = prev.get(name, 0), counts.get(name, 0)
			if abs(b - a) > max(3, 0.5 * a):
				flags.append(f"jump: {name} {a}→{b}")
		for younger, name in zip(order, order[1:]):
			limit = prev.get(name, 0) + prev.get(younger, 0) + 2
			if counts.get(name, 0) > limit:
				flags.append(f"flow: {name} {counts.get(name, 0)} > {limit} possible from the last count")
	if duration_seconds is not None and duration_seconds < FAST_SECONDS:
		flags.append(f"fast: {duration_seconds} s for the spot")
	if missing_plants:
		flags.append(f"missing: {missing_plants} plant{'s' if missing_plants > 1 else ''}")
	return flags


def _whole(value, label):
	try:
		v = int(value or 0)
	except (TypeError, ValueError):
		v = -1
	if v < 0:
		frappe.throw(f"{label} must be a whole number, 0 or more.")
	return v


def _flag_list(text):
	return [f for f in (text or "").split("\n") if f]


@frappe.whitelist(methods=["POST"])
def submit_plot_count(client_uuid, sample_plot, counts, plants_counted=None, notes=None, captured_at=None, photos=None,
		plant_counts=None, blind_shoots=0, missing_plants=0, duration_seconds=None):
	"""One spot count. Replaying the same client_uuid (offline queue after a
	lost response) returns the first record instead of a duplicate. Returns the
	quality flags worked out against the spot's previous count."""
	frappe.has_permission("Bed Sample", "create", throw=True)
	if not (client_uuid or "").strip():
		frappe.throw("client_uuid is required.")
	existing = frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid}, ["name", "quality_flags"], as_dict=True)
	if existing:
		return {"name": existing.name, "flags": _flag_list(existing.quality_flags)}
	counts = json.loads(counts) if isinstance(counts, str) else counts
	photos = json.loads(photos) if isinstance(photos, str) else (photos or [])
	plant_counts = json.loads(plant_counts) if isinstance(plant_counts, str) and plant_counts.strip() else plant_counts
	for r in counts:
		if not r.get("stage_name"):
			frappe.throw("Every count needs a stage_name.")
		_whole(r.get("count"), f"Count for {r.get('stage_name')}")
	if plant_counts is not None:
		if not isinstance(plant_counts, list) or not all(isinstance(p, dict) and isinstance(p.get("counts"), dict) for p in plant_counts):
			frappe.throw('plant_counts must be a list of {"plant": n, "counts": {stage: n}}.')
		for p in plant_counts:
			for k, v in p["counts"].items():
				_whole(v, f"Plant {p.get('plant')} count for {k}")
	blind_shoots = _whole(blind_shoots, "blind_shoots")
	missing_plants = _whole(missing_plants, "missing_plants")
	duration_seconds = None if duration_seconds in (None, "") else _whole(duration_seconds, "duration_seconds")
	plot = frappe.get_doc("Sample Plot", sample_plot)
	if plants_counted in (None, ""):
		plants_counted = max(0, (plot.plants or SPOT_PLANTS) - missing_plants)
	params = data.stages(plot.variety)
	days = {n: round(v[0]) for n, v in params.items()}
	day = getdate(captured_at) if captured_at else getdate(nowdate())
	totals = {}
	for r in counts:
		totals[r["stage_name"]] = totals.get(r["stage_name"], 0) + int(r.get("count") or 0)
	prev = frappe.get_all("Bed Sample", filters={"sample_plot": plot.name, "sampling_date": ["<", day]},
		pluck="name", order_by="sampling_date desc, creation desc", limit=1)
	order = sorted(params, key=lambda n: -params[n][0])  # youngest (most days out) first
	flags = quality_flags(totals, _stage_counts(prev)[prev[0]] if prev else None, order, duration_seconds, missing_plants)
	recount = frappe.db.exists("Bed Sample", {"sample_plot": plot.name, "sampling_date": day,
		"counted_by": ["!=", frappe.session.user]})
	try:
		doc = frappe.get_doc({
		"doctype": "Bed Sample", "client_uuid": client_uuid, "sample_plot": plot.name,
		"plants_counted": int(plants_counted), "notes": notes,
		"sampling_date": day, "captured_at": captured_at,
		"stages": [{"stage_name": r["stage_name"], "count": int(r.get("count") or 0),
			"days_to_harvest": days.get(r["stage_name"])} for r in counts],
		"plant_counts": json.dumps(plant_counts) if plant_counts is not None else None,
		"blind_shoots": blind_shoots, "missing_plants": missing_plants, "duration_seconds": duration_seconds,
		"quality_flags": "\n".join(flags), "is_recount": 1 if recount else 0, "counted_by": frappe.session.user,
		}).insert()
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):  # replay raced the first insert
		return {"name": frappe.db.get_value("Bed Sample", {"client_uuid": client_uuid}), "flags": flags}
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
	out = {"name": doc.name, "flags": flags}
	if skipped:
		doc.db_set("notes", "\n".join(filter(None, [doc.notes, *skipped])))
		out["photo_notes"] = skipped
	return out


@frappe.whitelist(methods=["POST"])
def confirm_plot_bed(sample_plot, bed):
	"""The set-up scan: the bed the spot really is on. Safe to repeat."""
	doc = frappe.get_doc("Sample Plot", sample_plot)
	doc.check_permission("write")
	bed = _whole(bed, "bed")
	rng = frappe.db.get_value("Warehouse Section", {"parent": doc.greenhouse, "parenttype": "Warehouse",
		"section": doc.section}, ["from_bed", "to_bed"], as_dict=True)
	if not bed or (rng and rng.from_bed and rng.to_bed and not rng.from_bed <= bed <= rng.to_bed):
		frappe.throw(f"Bed {bed} is not in section {doc.section}" + (f" (beds {rng.from_bed}-{rng.to_bed})." if rng and rng.from_bed else "."))
	if doc.bed != bed or not doc.set_up or not doc.started_on:
		doc.update({"bed": bed, "set_up": 1, "started_on": doc.started_on or nowdate()})
		doc.save()
	return {"name": doc.name, "bed": doc.bed, "set_up": 1}


def _qr(text):
	buf = io.BytesIO()
	qrcode.make(text, box_size=8, border=1).save(buf, format="PNG")
	return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def plot_tags_html(greenhouse, section=None):
	"""ID-card tags (86 x 54 mm), 2 x 5 per A4 like upande_harvest's bucket tags:
	QR of the plot name, where the spot is, and where to start counting."""
	filters = {"greenhouse": greenhouse, "active": 1}
	if section:
		filters["section"] = section
	spots = frappe.get_all("Sample Plot", filters=filters, fields=["name", "section", "plot_no", "bed", "position", "plants"])
	gh = frappe.db.get_value("Warehouse", greenhouse, "warehouse_name") or greenhouse
	by_section = {}
	for p in sorted(spots, key=lambda p: (p.section, not p.bed, p.bed or 0, p.plot_no)):
		by_section.setdefault(p.section, []).append(p)
	esc = frappe.utils.escape_html
	tags = []
	for sec, rows in by_section.items():
		for n, p in enumerate(rows, start=1):
			where = f"Bed {p.bed}" if p.bed else "Bed ______"
			tags.append(f"""<div class="qr"><img src="{_qr(p.name)}"></div><div class="text">
<div class="big">{esc(gh)} {esc(sec)}</div><div class="mid">{where}{f" · {p.position} along" if p.position else ""}</div>
<div class="small">Spot {n} of {len(rows)} · plants 1–{p.plants or SPOT_PLANTS}</div>
<div class="note">Start at this plant. Skip the first 2 m of the bed.</div><div class="id">{p.name}</div></div>""")
	tag_w, tag_h, cols, rows_, gap_x, gap_y = 86, 54, 2, 5, 6, 3
	x0, y0 = (210 - (cols * tag_w + (cols - 1) * gap_x)) / 2, (297 - (rows_ * tag_h + (rows_ - 1) * gap_y)) / 2
	pages = []
	for start in range(0, len(tags), cols * rows_):
		cells = "".join(
			f'<div class="tag" style="left:{x0 + (i % cols) * (tag_w + gap_x)}mm; top:{y0 + (i // cols) * (tag_h + gap_y)}mm;">{t}</div>'
			for i, t in enumerate(tags[start:start + cols * rows_]))
		pages.append(f'<div class="page">{cells}</div>')
	return """<!DOCTYPE html><html><head><meta charset="UTF-8"><style>
@page { size: A4 portrait; margin: 0; }
body { margin: 0; padding: 0; font-family: Arial, Helvetica, sans-serif; color: #000; }
.page { width: 209mm; height: 296mm; position: relative; overflow: hidden; }
.page + .page { page-break-before: always; }
.tag { position: absolute; width: 86mm; height: 54mm; border: 0.3mm dashed #999; box-sizing: border-box; border-radius: 3mm; }
.qr { position: absolute; left: 3mm; top: 6mm; width: 42mm; height: 42mm; }
.qr img { width: 42mm; height: 42mm; }
.text { position: absolute; left: 47mm; right: 2mm; top: 4mm; }
.big { font-size: 5.5mm; font-weight: 800; }
.mid { margin-top: 2mm; font-size: 4mm; font-weight: 700; white-space: nowrap; }
.small { margin-top: 1.5mm; font-size: 3mm; }
.note { margin-top: 2mm; font-size: 2.6mm; }
.id { margin-top: 2mm; font-size: 2.4mm; color: #444; letter-spacing: 0.3mm; }
</style></head><body>""" + "".join(pages) + "</body></html>"


@frappe.whitelist()
def plot_tags_pdf(greenhouse, section=None):
	"""Printable spot tags for a greenhouse (or one section) as a PDF download."""
	frappe.has_permission("Sample Plot", "read", throw=True)
	if not frappe.db.exists("Warehouse", greenhouse):
		frappe.throw(f"No greenhouse {greenhouse}.")
	html = plot_tags_html(greenhouse, section or None)
	frappe.local.response.filename = f"plot-tags-{greenhouse}{'-' + section if section else ''}.pdf"
	frappe.local.response.filecontent = get_pdf(html, options={"page-size": "A4", "disable-smart-shrinking": "", "encoding": "UTF-8", "margin-top": "0mm",
		"margin-bottom": "0mm", "margin-left": "0mm", "margin-right": "0mm"})
	frappe.local.response.type = "pdf"


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
		"flags": f["flags"], "last_count": str(f["last_count"]) if f["last_count"] else None,
		"ratio": round(f["ratio"], 3) if f["ratio"] else None, "ratio_days": f["ratio_days"]}


@frappe.whitelist()
def recalibrate(variety):
	frappe.only_for(("System Manager", "Agriculture Manager"))
	doc = calib.calibrate(variety)
	return {"status": doc.status, "fitted_on": doc.fitted_on}
