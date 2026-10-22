"""DB fixtures for the harvest forecast tests (bhcloud.local: upande_core + upande_harvest + upande_agriculture)."""
import frappe

from upande_agriculture.tests import default_company, default_uom


def make_variety(name="FC-ROSE", has_variants=1):
	"""A rose variety: an Item template harvested as Length variants."""
	if not frappe.db.exists("Item", name):
		frappe.get_doc({"doctype": "Item", "item_code": name, "item_name": name,
			"item_group": "All Item Groups", "stock_uom": default_uom(), "is_stock_item": 1,
			"has_variants": has_variants, "attributes": [{"attribute": "Length"}] if has_variants else [],
		}).insert(ignore_permissions=True)
	return name


def make_variant(variety, length="60cm"):
	from erpnext.controllers.item_variant import create_variant, get_variant
	return get_variant(variety, {"Length": length}) or \
		create_variant(variety, {"Length": length}).insert(ignore_permissions=True).name


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


def make_greenhouse(name="FC GH", units=(("FC-ROSE", 1000, "1-9"),)):
	"""units: (variety, plants, bed_range) tuples, each an Active Crop Cycle."""
	existing = frappe.db.get_value("Warehouse", {"warehouse_name": name})
	if existing:
		return existing
	gh = frappe.get_doc({
		"doctype": "Warehouse", "warehouse_name": name, "company": default_company(),
		"warehouse_type": "Greenhouse", "custom_farm": _farm(),
	}).insert(ignore_permissions=True).name
	for variety, plants, beds in units:
		make_cycle(gh, variety, plants, beds)
	return gh


def make_cycle(greenhouse, variety, plants, beds=None):
	cc = frappe.get_doc({"doctype": "Crop Cycle", "greenhouse": greenhouse, "variety": variety, "status": "Active",
		"planting_date": "2025-01-06", "qty_planted": plants}).insert(ignore_permissions=True)
	if beds:  # stored as typed: a real range needs Bed records the forecast does not read
		cc.db_set("bed_range", beds)
	return cc.name


def make_plot(greenhouse, section, plot_no=1, plants=10, variety=None):
	name = frappe.db.get_value("Sample Plot", {"greenhouse": greenhouse, "section": section, "plot_no": plot_no})
	return name or frappe.get_doc({"doctype": "Sample Plot", "greenhouse": greenhouse, "section": section,
		"variety": variety or section, "plot_no": plot_no, "bed": 1, "plants": plants}).insert(ignore_permissions=True).name


def make_count(plot, day, counts, plants=10, uuid=None):
	"""counts: {stage_name: buds counted on the plot's plants}."""
	return frappe.get_doc({"doctype": "Bed Sample", "sample_plot": plot, "sampling_date": day,
		"plants_counted": plants, "client_uuid": uuid or frappe.generate_hash(length=12),
		"stages": [{"stage_name": k, "count": v} for k, v in counts.items()],
	}).insert(ignore_permissions=True).name


def make_harvest(greenhouse, section, item, day, qty):
	"""A Harvesting entry; a variety (template) is picked as its 60cm variant."""
	if frappe.db.get_value("Item", item, "has_variants"):
		item = make_variant(item)
	se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Harvesting", "company": default_company(),
		"posting_date": day, "set_posting_time": 1, "custom_greenhouse": greenhouse, "custom_section": section,
		"items": [{"item_code": item, "qty": qty, "t_warehouse": greenhouse, "basic_rate": 1,
			"allow_zero_valuation_rate": 1}]})
	se.insert(ignore_permissions=True)
	frappe.flags.warehouse_account_map = {}  # ERPNext caches it per run; new test greenhouses are missing from it
	se.submit()
	return se.name
