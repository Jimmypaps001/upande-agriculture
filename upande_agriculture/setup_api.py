"""The phone's Setup screen: who picks which beds, what grows where, and each
crop's stage timing. One save keeps Crop Cycle, Crop Protocol, the Warehouse's
sections and its Varieties per GH table in step."""
import frappe
from frappe.utils import cint, flt, getdate, nowdate

from upande_agriculture.forecast import curves, data

WRITE_ROLES = ("System Manager", "Agriculture Manager", "Farm Manager")
READ_ROLES = WRITE_ROLES + ("Stock Manager",)
PLACEHOLDER_DATE = "2026-01-01"
PLACEHOLDER_NOTE = "Planting date unknown — placeholder set by Setup, please correct."


def _can_edit():
	return bool(set(WRITE_ROLES) & set(frappe.get_roles()))


def _has_vpg():
	return frappe.db.table_exists("Varieties per GH")


def _greenhouse(name):
	if not name or frappe.db.get_value("Warehouse", name, "warehouse_type") != "Greenhouse":
		frappe.throw(f"{name} is not a greenhouse.")
	return name


def _variety(name):
	if not name or not frappe.db.exists("Item", name):
		frappe.throw(f"No variety {name}.")
	return name


def _stage_rows(protocol):
	return [{"stage_name": r.stage_name, "days_to_harvest": r.days_to_harvest, "spread_days": r.spread_days,
		"survival": r.survival} for r in frappe.get_all("Crop Protocol Growth Stage",
			filters={"parent": protocol, "parenttype": "Crop Protocol"},
			fields=["stage_name", "days_to_harvest", "spread_days", "survival"], order_by="idx")]


def default_stage_rows():
	"""curves.DEFAULT_STAGES in protocol shape (survival as a percent)."""
	return [{"stage_name": s["stage_name"], "days_to_harvest": s["days_to_harvest"],
		"spread_days": s.get("spread_days"), "survival": s["survival"] * 100} for s in curves.DEFAULT_STAGES]


def ensure_default_protocol(variety):
	"""The variety's default Crop Protocol, made from the farm's default stages if missing."""
	name = data.default_protocol(variety)
	if name:
		return name
	return frappe.get_doc({"doctype": "Crop Protocol", "protocol_name": variety, "variety_item": variety,
		"growth_stages": default_stage_rows()}).insert(ignore_permissions=True).name


def _active_cycle(greenhouse, variety):
	return frappe.db.get_value("Crop Cycle", {"greenhouse": greenhouse, "variety": variety, "status": "Active"},
		order_by="creation desc")


def _vpg_plants(greenhouse, variety):
	if not _has_vpg():
		return 0
	return sum(data._int(p) for p in frappe.get_all("Varieties per GH", pluck="no_of_plants",
		filters={"parent": greenhouse, "parenttype": "Warehouse", "parentfield": "custom_varieties_grown",
			"variety": variety}))


def _set_vpg(greenhouse, variety, plants):
	"""One Varieties per GH row for the variety (duplicates collapsed, size_m2 summed);
	plants None removes the variety's rows."""
	if not _has_vpg():
		return
	wh = frappe.get_doc("Warehouse", greenhouse)
	mine = [r for r in wh.custom_varieties_grown if r.variety == variety]
	if plants is None:
		if not mine:
			return
		wh.custom_varieties_grown = [r for r in wh.custom_varieties_grown if r.variety != variety]
	else:
		if mine:
			keep = mine[0]
			sizes = [r.size_m2 for r in mine if (r.size_m2 or "").strip()]
			try:
				keep.size_m2 = str(round(sum(float(str(s).replace(",", "")) for s in sizes), 2)) if sizes else keep.size_m2
			except ValueError:
				pass  # a non-numeric size stays as the first row had it
			keep.no_of_plants = str(plants)
			wh.custom_varieties_grown = [r for r in wh.custom_varieties_grown if r.variety != variety or r is keep]
		else:
			wh.append("custom_varieties_grown", {"variety": variety, "no_of_plants": str(plants)})
		for i, r in enumerate(wh.custom_varieties_grown, start=1):
			r.idx = i
	wh.save(ignore_permissions=True)


def _upsert_cycle(greenhouse, variety, plants, planting_date=None, bed_range=None, protocol=None):
	name = _active_cycle(greenhouse, variety)
	doc = frappe.get_doc("Crop Cycle", name) if name else frappe.get_doc({"doctype": "Crop Cycle",
		"greenhouse": greenhouse, "variety": variety, "status": "Active",
		"farm": frappe.db.get_value("Warehouse", greenhouse, "custom_farm")})
	# plants_standing is qty_planted less the uproot log: plant back what was uprooted so it reads `plants`.
	doc.qty_planted = plants + sum(cint(u.plants) for u in (doc.uproot_log or []))
	doc.plants_standing = plants
	if bed_range is not None:
		doc.bed_range = bed_range or None
	doc.crop_protocol = protocol or doc.crop_protocol or ensure_default_protocol(variety)
	notes = (doc.notes or "").replace(PLACEHOLDER_NOTE, "").strip()
	if planting_date:
		doc.planting_date = getdate(planting_date)
	elif not doc.planting_date:
		doc.planting_date = getdate(PLACEHOLDER_DATE)
		notes = f"{notes}\n{PLACEHOLDER_NOTE}".strip()
	elif PLACEHOLDER_NOTE in (doc.notes or ""):
		notes = f"{notes}\n{PLACEHOLDER_NOTE}".strip()
	doc.notes = notes or None
	doc.save(ignore_permissions=True)
	return doc


def _crops(houses):
	"""{greenhouse: [crop]}: Active Crop Cycles and Varieties per GH rows, matched by variety."""
	out = {h: {} for h in houses}
	if not houses:
		return {}
	if _has_vpg():
		for r in frappe.db.sql(
				"""SELECT parent, variety, no_of_plants FROM `tabVarieties per GH`
				   WHERE parenttype = 'Warehouse' AND parentfield = 'custom_varieties_grown' AND parent IN %s
				     AND IFNULL(variety, '') != '' ORDER BY parent, idx""", (tuple(houses),), as_dict=True):
			c = out[r.parent].setdefault(r.variety, {"variety": r.variety, "plants": 0, "cycle": None,
				"protocol": None, "planting_date": None, "bed_range": None, "notes_placeholder_date": False})
			c["plants"] += data._int(r.no_of_plants)
	for r in frappe.get_all("Crop Cycle", filters={"status": "Active", "greenhouse": ["in", list(houses)]},
			fields=["name", "greenhouse", "variety", "plants_standing", "qty_planted", "crop_protocol",
				"planting_date", "bed_range", "notes"], order_by="creation asc"):
		out[r.greenhouse][r.variety] = {"variety": r.variety, "plants": cint(r.plants_standing or r.qty_planted),
			"cycle": r.name, "protocol": r.crop_protocol, "planting_date": str(r.planting_date) if r.planting_date else None,
			"bed_range": r.bed_range, "notes_placeholder_date": PLACEHOLDER_NOTE in (r.notes or "")}
	return {h: sorted(v.values(), key=lambda c: c["variety"]) for h, v in out.items()}


def _sections(houses):
	out = {h: [] for h in houses}
	if houses:
		for r in frappe.get_all("Warehouse Section", fields=["parent", "idx", "section", "from_bed", "to_bed",
				"custom_employee", "custom_employee_name", "custom_variety"],
				filters={"parenttype": "Warehouse", "parentfield": "custom_sections", "parent": ["in", list(houses)]},
				order_by="parent, idx"):
			out[r.parent].append({"row": r.idx, "section": r.section, "from_bed": r.from_bed, "to_bed": r.to_bed,
				"employee": r.custom_employee, "employee_name": r.custom_employee_name, "variety": r.custom_variety})
	return out


@frappe.whitelist()
def get_setup(farm=None):
	frappe.only_for(READ_ROLES)
	filters = {"warehouse_type": "Greenhouse", "disabled": 0, "is_group": 0}
	if farm:
		filters["custom_farm"] = farm
	leaves = frappe.get_all("Warehouse", filters=filters,
		fields=["name", "warehouse_name", "parent_warehouse", "custom_farm", "custom_number_of_beds"], order_by="name")
	parents = {p for p in (w.parent_warehouse for w in leaves) if p}
	# A greenhouse group holds only leaves; a parent with sub-groups (e.g. "Flower Operations") is not one.
	not_groups = set(frappe.get_all("Warehouse", pluck="parent_warehouse",
		filters={"parent_warehouse": ["in", list(parents) or [""]], "is_group": 1}))
	group_names = dict(frappe.get_all("Warehouse", filters={"name": ["in", list(parents) or [""]]},
		fields=["name", "warehouse_name"], as_list=True))
	houses = [w.name for w in leaves]
	sections, crops = _sections(houses), _crops(houses)
	groups = {}
	for w in leaves:
		key = w.parent_warehouse if w.parent_warehouse in parents - not_groups else w.name
		g = groups.setdefault(key, {"group": key, "group_name": group_names.get(key) or w.warehouse_name,
			"farm": w.custom_farm, "houses": []})
		g["houses"].append({"name": w.name, "warehouse_name": w.warehouse_name,
			"beds": max([s["to_bed"] or 0 for s in sections[w.name]], default=0) or cint(w.custom_number_of_beds),
			"sections": sections[w.name], "crops": crops[w.name]})
	stages = {}
	for r in frappe.get_all("Crop Protocol Growth Stage", filters={"parenttype": "Crop Protocol"},
			fields=["parent", "stage_name", "days_to_harvest", "spread_days", "survival"], order_by="parent, idx"):
		stages.setdefault(r.parent, []).append({"stage_name": r.stage_name, "days_to_harvest": r.days_to_harvest,
			"spread_days": r.spread_days, "survival": r.survival})
	return {
		"can_edit": _can_edit(),
		"employees": frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"],
			order_by="employee_name asc", limit=3000),
		"varieties": frappe.get_all("Item", filters={"has_variants": 1}, pluck="name", order_by="name asc"),
		"protocols": [{"name": p.name, "variety": p.variety_item, "stages": stages.get(p.name, [])}
			for p in frappe.get_all("Crop Protocol", fields=["name", "variety_item"], order_by="name asc")],
		"default_stages": default_stage_rows(),
		"greenhouses": sorted(groups.values(), key=lambda g: g["group_name"] or ""),
	}


@frappe.whitelist(methods=["POST"])
def save_sections(greenhouse, sections):
	frappe.only_for(WRITE_ROLES)
	_greenhouse(greenhouse)
	rows = []
	for s in frappe.parse_json(sections) or []:
		lo, hi = cint(s.get("from_bed")), cint(s.get("to_bed"))
		if lo < 1 or hi < 1:
			frappe.throw("Every section needs a from bed and a to bed (1 or more).")
		if lo > hi:
			frappe.throw(f"Beds {lo}-{hi}: the from bed is after the to bed.")
		employee = s.get("employee") or None
		if employee and not frappe.db.exists("Employee", employee):
			frappe.throw(f"No employee {employee}.")
		variety = s.get("variety") or None
		if variety:
			_variety(variety)
		rows.append({"section": (s.get("section") or "").strip() or f"Beds {lo}-{hi}", "from_bed": lo, "to_bed": hi,
			"custom_employee": employee, "custom_variety": variety,
			"custom_employee_name": employee and frappe.db.get_value("Employee", employee, "employee_name")})
	ordered = sorted(rows, key=lambda r: (r["from_bed"], r["to_bed"]))
	for a, b in zip(ordered, ordered[1:]):
		if b["from_bed"] <= a["to_bed"]:
			frappe.throw(f"Beds {b['from_bed']}-{b['to_bed']} overlap Beds {a['from_bed']}-{a['to_bed']}.")
	labels = [r["section"] for r in rows]
	dup = next((x for x in labels if labels.count(x) > 1), None)
	if dup:
		frappe.throw(f"Two sections are called {dup}.")
	# The Section docs are rebuilt from the new rows by upande_core's Warehouse hook. Old ones would
	# collide with the new ranges (Section refuses overlaps), so clear them and the beds' links first.
	frappe.db.set_value("Bed", {"greenhouse": greenhouse}, "section", None, update_modified=False)
	frappe.db.delete("Section", {"greenhouse": greenhouse})
	wh = frappe.get_doc("Warehouse", greenhouse)
	wh.set("custom_sections", rows)
	wh.save(ignore_permissions=True)
	return _sections([greenhouse])[greenhouse]


@frappe.whitelist(methods=["POST"])
def save_crop(greenhouse, variety, plants, planting_date=None, bed_range=None, protocol=None):
	frappe.only_for(WRITE_ROLES)
	_greenhouse(greenhouse)
	_variety(variety)
	plants = cint(plants)
	if plants < 0:
		frappe.throw("Plants cannot be negative.")
	if protocol and not frappe.db.exists("Crop Protocol", protocol):
		frappe.throw(f"No Crop Protocol {protocol}.")
	_upsert_cycle(greenhouse, variety, plants, planting_date or None, bed_range, protocol or None)
	_set_vpg(greenhouse, variety, plants)
	return next(c for c in _crops([greenhouse])[greenhouse] if c["variety"] == variety)


@frappe.whitelist(methods=["POST"])
def remove_crop(greenhouse, variety):
	frappe.only_for(WRITE_ROLES)
	_greenhouse(greenhouse)
	name = _active_cycle(greenhouse, variety)
	has_rows = _has_vpg() and frappe.db.exists("Varieties per GH",
		{"parent": greenhouse, "parenttype": "Warehouse", "variety": variety})
	if not name and not has_rows:
		frappe.throw(f"{variety} is not grown in {greenhouse}.")
	if name:
		doc = frappe.get_doc("Crop Cycle", name)
		doc.status, doc.cycle_end_date = "Ended", nowdate()
		doc.save(ignore_permissions=True)
	_set_vpg(greenhouse, variety, None)
	return {"greenhouse": greenhouse, "variety": variety, "ended_cycle": name}


def _clean_stages(stages):
	rows, seen = [], set()
	for s in frappe.parse_json(stages) or []:
		name = (s.get("stage_name") or "").strip()
		if not name:
			frappe.throw("Every stage needs a name.")
		if name.lower() in seen:
			frappe.throw(f"Stage {name} is listed twice.")
		seen.add(name.lower())
		days = flt(s.get("days_to_harvest"))
		if days <= 0:
			frappe.throw(f"{name}: days to harvest must be more than 0.")
		spread = s.get("spread_days")
		spread = None if spread in (None, "") else flt(spread)
		if spread is not None and spread < 0:
			frappe.throw(f"{name}: spread days cannot be negative.")
		survival = s.get("survival")
		survival = None if survival in (None, "") else flt(survival)
		if survival is not None and not 0 <= survival <= 100:
			frappe.throw(f"{name}: survival is a percent, 0 to 100.")
		rows.append({"stage_name": name, "days_to_harvest": days, "spread_days": spread, "survival": survival})
	if not rows:
		frappe.throw("Give at least one stage.")
	return rows


@frappe.whitelist(methods=["POST"])
def save_stages(variety, stages, greenhouse=None):
	frappe.only_for(WRITE_ROLES)
	_variety(variety)
	rows = _clean_stages(stages)
	default = ensure_default_protocol(variety)
	if greenhouse:
		_greenhouse(greenhouse)
		cycle = _active_cycle(greenhouse, variety)
		plants = None if cycle else _vpg_plants(greenhouse, variety)
		if not cycle and not plants:
			frappe.throw(f"{variety} is not grown in {greenhouse}: add the crop first.")
		name = f"{variety} – {frappe.db.get_value('Warehouse', greenhouse, 'warehouse_name')}"
		if frappe.db.exists("Crop Protocol", name):
			doc = frappe.get_doc("Crop Protocol", name)
		else:
			doc = frappe.copy_doc(frappe.get_doc("Crop Protocol", default))
			doc.protocol_name = name
	else:
		doc = frappe.get_doc("Crop Protocol", default)
	notes = {r.stage_name.lower(): r.description for r in doc.growth_stages if r.description}
	doc.set("growth_stages", [dict(r, description=notes.get(r["stage_name"].lower())) for r in rows])
	doc.save(ignore_permissions=True)
	if greenhouse:
		if cycle:
			c = frappe.get_doc("Crop Cycle", cycle)
			c.crop_protocol = doc.name
			c.save(ignore_permissions=True)
		else:
			_upsert_cycle(greenhouse, variety, plants, protocol=doc.name)
			_set_vpg(greenhouse, variety, plants)
	return doc.name


@frappe.whitelist(methods=["POST"])
def reset_stages(variety, greenhouse):
	frappe.only_for(WRITE_ROLES)
	cycle = _active_cycle(_greenhouse(greenhouse), _variety(variety))
	if not cycle:
		frappe.throw(f"{variety} has no active crop in {greenhouse}.")
	c = frappe.get_doc("Crop Cycle", cycle)
	c.crop_protocol = ensure_default_protocol(variety)
	c.save(ignore_permissions=True)
	return c.crop_protocol
