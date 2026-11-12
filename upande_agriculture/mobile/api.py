# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Mobile API — server scripts called by the Upande mobile apps, ported
# verbatim from DB Server Scripts (bench + kaitet-group v15 live) into
# version-controlled whitelisted methods. Bodies keep frappe.form_dict /
# frappe.response exactly as the live scripts used them.

import datetime
import json

import frappe
from frappe import _

from upande_agriculture.upande_agriculture.doctype.production_plan_form.production_plan_form import (
	can_edit_plan,
	can_enter_actuals,
	ensure_plan_editable,
)


def resolve_item_group_stem_limit(item_code):
	"""Resolves the configured max-stems-per-bucket cap for an item, from
	Production Settings' Harvest Item Group Config child table.

	The config is keyed by item group, but it is usually set on a PARENT
	group (e.g. "Standard Roses", "Spray Roses"), while real items live in a
	leaf group (e.g. "Standard Roses - Intermediate", "Spray Roses - The
	Classics" -- items are never assigned directly to the parent group
	itself). So this walks the item's whole ancestor chain and uses the
	NEAREST configured ancestor -- shared by both createHarvestStockEntry
	(Standard Roses) and createGradingStockEntry (Spray Roses) so a cap set
	once applies consistently to both harvest paths.

	Returns (max_limit, matched_group) -- max_limit is 0 if unconfigured
	(0 / unset means no cap).
	"""
	item_group = frappe.db.get_value("Item", item_code, "item_group") if item_code else None
	max_limit = 0
	matched_group = item_group
	if not item_group:
		return max_limit, matched_group

	configs = frappe.get_all(
		"Harvest Item Group Config",
		filters={"parent": "Production Settings", "parenttype": "Production Settings"},
		fields=["item_group", "max_stems_per_bucket"],
	)
	config_map = {c["item_group"]: c["max_stems_per_bucket"] for c in configs}
	if not config_map:
		return max_limit, matched_group

	# Ancestors of the item's group (self + parents), nearest first.
	bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	if bounds:
		ancestors = frappe.get_all(
			"Item Group",
			filters={"lft": ["<=", bounds.lft], "rgt": [">=", bounds.rgt]},
			fields=["name"],
			order_by="lft desc",  # deepest (most specific) first
		)
		for a in ancestors:
			if a["name"] in config_map:
				max_limit = config_map[a["name"]] or 0
				matched_group = a["name"]
				break
	return max_limit, matched_group


def validate_bucket_stem_limit(item_code, bucket_id, new_stems, exclude_stock_entry=None):
	"""Returns an error message string if adding `new_stems` of `item_code`
	to `bucket_id` would exceed the configured per-bucket cap (Production
	Settings' Harvest Item Group Config), or None if it's fine.

	Cumulative across TODAY's other Harvesting entries for this bucket, same
	as the spray check this generalizes: a Standard Roses bucket only ever
	carries one live entry at a time (re-harvesting an In Use bucket is
	blocked outright), so summing "other entries" is a no-op there and this
	reduces to the plain single-entry comparison it always was; a Spray
	Roses bucket can carry several scans before being received, so the sum
	is what actually enforces the cap. `exclude_stock_entry` leaves the
	entry currently being corrected (see amendHarvestEntry) out of its own
	"existing stems" total -- otherwise a correction would double-count
	itself against the cap it's trying to satisfy.

	Shared by createHarvestStockEntry, createGradingStockEntry and
	amendHarvestEntry so the cap is enforced the same way whether stems are
	added by a fresh scan or by a Bucket Status correction -- a correction
	that skipped this check could push a bucket over the cap that scanning
	itself would have refused.
	"""
	if not (item_code and bucket_id):
		return None
	max_limit, matched_group = resolve_item_group_stem_limit(item_code)
	if not max_limit:
		return None

	filters = {
		"custom_bucket_id": bucket_id,
		"stock_entry_type": "Harvesting",
		"posting_date": frappe.utils.today(),
		"docstatus": ["<", 2],
	}
	if exclude_stock_entry:
		filters["name"] = ["!=", exclude_stock_entry]
	other_entries = frappe.get_all("Stock Entry", filters=filters, fields=["name"])

	existing_stems = 0
	if other_entries:
		detail_rows = frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": ["in", [e.name for e in other_entries]]},
			fields=["qty"],
		)
		existing_stems = sum((row.qty or 0) for row in detail_rows)

	try:
		new_stems = float(new_stems or 0)
	except (TypeError, ValueError):
		return f"'{new_stems}' isn't a number of stems."
	total = existing_stems + new_stems
	if total > float(max_limit):
		return f"Maximum {int(max_limit)} {matched_group} stems per bucket."
	return None


def item_group_is_under(item_code, root_group):
	"""True if item_code's own item group IS root_group, or any descendant
	of it in the Item Group tree.

	Real items are never assigned directly to a parent group like "Spray
	Roses" -- they live in leaf groups underneath it (e.g. "Spray Roses -
	Bombastics"), so a flat `item_group == "Spray Roses"` check NEVER
	matches a real spray item. That exact flat check is what let 295 real
	Spray Roses harvests through the Standards form on 2026-09-09 alone
	(the "Block Spray Roses" guard in createHarvestStockEntry had never
	actually fired). Uses the Item Group nested-set (lft/rgt), same
	technique resolve_item_group_stem_limit already uses for the stem cap.
	"""
	if not item_code:
		return False
	item_group = frappe.db.get_value("Item", item_code, "item_group")
	if not item_group:
		return False
	if item_group == root_group:
		return True
	root_bounds = frappe.db.get_value("Item Group", root_group, ["lft", "rgt"], as_dict=True)
	item_bounds = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"], as_dict=True)
	if not root_bounds or not item_bounds:
		return False
	return root_bounds.lft <= item_bounds.lft and item_bounds.rgt <= root_bounds.rgt


def validate_variety_planted_in_greenhouse(variety, greenhouse):
	"""Returns None if `variety` is planted in `greenhouse`, or an error
	message string otherwise. Shared by createGradingStockEntry (Spray Roses
	at grading-time) and amendHarvestEntry (correcting either rose type's
	variety after the fact) -- keeping this in one place is exactly what
	resolve_item_group_stem_limit above already does for the stem cap, for
	the same reason: two copies of the same check drift apart silently.

	Varieties standing in a greenhouse come from the Greenhouse doctype's
	varieties_grown ledger; active Crop Cycles (one per variety) are the
	fallback for houses without a ledger yet.
	"""
	if not (variety and greenhouse):
		return None

	vg_rows = frappe.get_all(
		"Greenhouse Variety",
		filters={"parent": greenhouse, "parenttype": "Greenhouse", "parentfield": "varieties_grown"},
		fields=["variety"],
	)
	varieties_planted = [r.variety for r in vg_rows]
	if not varieties_planted:
		varieties_planted = frappe.get_all(
			"Crop Cycle",
			filters={"greenhouse": greenhouse, "status": ["!=", "Ended"]},
			pluck="variety",
		)

	if variety not in varieties_planted:
		return f"Item '{variety}' is not planted in warehouse '{greenhouse}'. Please verify the correct location."
	return None


def cleanup_bucket_reuse_anomalies(bucket_id, farm, greenhouse, variety, harvest_stock_entry_name):
	"""Port of v15's bucket-reuse cleanup (see upande-quality's legacy
	kaitet-web/harvestingStockEntry.py, "RE-USE CLEANUP"). A bucket that's
	physically back in the greenhouse for a NEW journey can still be
	carrying stale state from its previous life on the shelf:
	  - still listed as a Shelf Item (never actually issued out), or
	  - still pending on an undischarged Discard Request row (never
	    actually discarded).
	Left alone, either one causes a real error later: shelving this same
	bucket again fails with "already on the shelf". Harvesting/grading are
	the FIRST point of contact back at the greenhouse -- a bucket only
	starts a fresh journey there -- so that's where this runs, not at
	shelving time. Called only when a NEW journey starts (every Standard
	Roses harvest; only the first Spray Roses scan of a session) -- not on
	every grading scan, since a bucket mid-session hasn't left and come
	back.

	Best-effort: a cleanup hiccup must never block the harvest itself.
	"""
	try:
		stale_shelf_items = frappe.get_all(
			"Shelf Item",
			filters={"bucket_id": bucket_id},
			fields=["name", "parent", "variety", "stem_qty"],
		)
		stale_disc = frappe.get_all(
			"Discard Request Bucket",
			filters={"bucket_id": bucket_id, "parenttype": "Discard Request", "discarded": ["!=", 1]},
			fields=["name", "parent"],
		)
		if not stale_shelf_items and not stale_disc:
			return

		# Discarding is the more serious skipped step (the bucket was meant
		# to be emptied and voided, not just moved) -- flag that first if
		# both artifacts happen to be present.
		skipped_step = "Discarding Skipped" if stale_disc else "Issuing Skipped"
		prev_shelf = stale_shelf_items[0]["parent"] if stale_shelf_items else None
		prev_variety = stale_shelf_items[0].get("variety") if stale_shelf_items else variety
		prev_stems = sum((shi.get("stem_qty") or 0) for shi in stale_shelf_items)
		prev_dr = stale_disc[0]["parent"] if stale_disc else None

		for shi in stale_shelf_items:
			frappe.delete_doc("Shelf Item", shi["name"], force=1, ignore_permissions=True)
		for drb in stale_disc:
			frappe.db.set_value("Discard Request Bucket", drb["name"], "discarded", 1, update_modified=False)

		frappe.get_doc(
			{
				"doctype": "Bucket Reuse Anomaly",
				"bucket_id": bucket_id,
				"skipped_step": skipped_step,
				"detected_on": frappe.utils.now(),
				"farm": farm,
				"greenhouse": greenhouse,
				"variety": prev_variety,
				"stems": prev_stems,
				"previous_shelf": prev_shelf,
				"discard_request": prev_dr,
				"harvest_stock_entry": harvest_stock_entry_name,
			}
		).insert(ignore_permissions=True)

		frappe.db.commit()
	except Exception:
		frappe.log_error(title=f"Bucket reuse cleanup failed for {bucket_id}")


@frappe.whitelist()
def createGradingStockEntry():
	try:
		data = frappe.request.get_json()
		bunch_qr_doc = frappe.get_doc("Bunch QR Code", data.get("bunch_id"))
		farm = bunch_qr_doc.farm
		stock_entry_type = data.get("stock_entry_type")
		graded_by = data.get("graded_by")
		# The mobile app now sends an explicit stem_length, picked from a
		# dropdown at grading time rather than trusting whatever was baked
		# into the Bunch QR Code record when the bunch tag was printed --
		# that field doesn't reliably reflect what's actually in the bucket.
		# bunch_qr_doc.stem_length is only a fallback for older app builds
		# that don't send it yet.
		stem_length = data.get("stem_length") or bunch_qr_doc.stem_length
		bunch_id = data.get("bunch_id")
		variety = bunch_qr_doc.item_code
		qty = data.get("qty")
		rose_type = data.get("rose_type")
		source_warehouse = data.get("source_warehouse")
		raw_bunch_size = bunch_qr_doc.bunch_size
		bucket_id = data.get("bucket_id")

		frappe.log_error("Grading Payload", data)

		# --- Spray bucket stem limit validation (Production Settings) ---
		# Spray roses are graded straight from the greenhouse: each scan harvests a bunch
		# into the physical bucket. Enforce the per-bucket spray maximum cumulatively across
		# today's Harvesting entries for this bucket so a scan that would overfill is blocked
		# before any stock entry is created.
		if rose_type == "Spray Roses" and bucket_id:
			size_digits = "".join([c for c in str(raw_bunch_size) if c.isdigit()])
			bunch_size_stems = int(size_digits) if size_digits else 0
			new_stems = float(qty or 0) * bunch_size_stems
			# Shared with the Standard Roses cap in createHarvestStockEntry
			# and with corrections in amendHarvestEntry -- see
			# validate_bucket_stem_limit's docstring.
			limit_error = validate_bucket_stem_limit(variety, bucket_id, new_stems)
			if limit_error:
				frappe.response["http_status_code"] = 400
				frappe.response["error"] = limit_error
				frappe.response["message"] = limit_error
				frappe.throw(limit_error)
		# --- end spray bucket stem limit validation ---
		# ======================================
		# VALIDATION: Check if item is planted in the source warehouse/greenhouse
		# ======================================
		if source_warehouse and variety:
			try:
				not_planted_error = validate_variety_planted_in_greenhouse(variety, source_warehouse)

				# Check if the item being graded is planted in this warehouse
				if not_planted_error:
					frappe.response["http_status_code"] = 400
					frappe.response["error"] = not_planted_error
					frappe.response["message"] = not_planted_error
				else:
					timestamp = frappe.utils.now_datetime()
					harvest_batch_id = f"{bucket_id}-{farm}-{variety}-{timestamp}"
					digits = "".join([c for c in str(raw_bunch_size) if c.isdigit()])
					if digits:
						bunch_size = int(digits)
					else:
						frappe.throw(f"Invalid bunch size: '{raw_bunch_size}'")
						frappe.log_error("Invalid bunch size", raw_bunch_size)

					farm_doc = frappe.get_doc("Farm", farm)
					company = farm_doc.company
					if rose_type == "Spray Roses":
						if not bucket_id:
							frappe.throw("Bucket ID is required for Spray Roses")

						if not frappe.db.exists("Bucket QR Code", bucket_id):
							new_bucket = frappe.new_doc("Bucket QR Code")
							new_bucket.id = bucket_id
							new_bucket.insert()
							frappe.log_error(
								"Grading Debug", f"Submitted new Bucket QR Code: {new_bucket.name}"
							)

						harvest_doc = frappe.new_doc("Stock Entry")
						harvest_doc.stock_entry_type = "Harvesting"
						harvest_doc.custom_bucket_id = bucket_id
						harvest_doc.custom_greenhouse = source_warehouse
						harvest_doc.farm = farm  # Farm accounting dimension (mirrored to custom_farm by hook)
						harvest_doc.custom_business_unit = "Roses"
						harvest_doc.company = company
						harvest_doc.custom_harvester = graded_by
						harvest_doc.custom_stem_length = stem_length
						harvest_doc.custom_harvest_batch_no = harvest_batch_id
						harvest_doc.append(
							"items",
							{
								"t_warehouse": source_warehouse,
								"item_code": variety,
								"qty": qty * bunch_size,
								"uom": "Stems",
								"allow_zero_valuation_rate": 1,
							},
						)
						harvest_doc.insert(ignore_permissions=True)
						harvest_doc.submit()
						frappe.log_error("Grading Debug", f"Harvest Stock Entry created: {harvest_doc.name}")

						# Spray roses skip createHarvestStockEntry entirely (this
						# IS the harvest, done in the field during grading), so it
						# never got the same bucket status/last_stock_entry update
						# that function gives every other harvest -- meaning
						# receiving could never tell a spray bucket was "In Use"
						# at all. Mirror that same update here so the bucket
						# lifecycle is consistent across both rose types. Several
						# grading scans can legitimately top up the same bucket
						# before it's received, so this only ever moves it INTO
						# "In Use" (never flips an already-In-Use bucket back);
						# createReceivingStockEntry aggregates every harvest
						# entry accumulated since the last receive, not just this
						# one.
						spray_bucket_doc = frappe.get_doc("Bucket QR Code", bucket_id, for_update=True)
						if spray_bucket_doc.status != "In Use":
							# Only THIS transition (Available -> In Use) starts a
							# new journey -- a later scan in the same session
							# finds the bucket already In Use and must not move
							# the marker, or it would orphan the earlier scans
							# of this same session from Receiving's aggregation.
							spray_bucket_doc.status = "In Use"
							spray_bucket_doc.current_journey_start = harvest_doc.name
						spray_bucket_doc.last_stock_entry = harvest_doc.name
						spray_bucket_doc.save(ignore_permissions=True)
						frappe.db.commit()

						if spray_bucket_doc.current_journey_start == harvest_doc.name:
							# Only fires on the scan that just started this
							# journey (see comment above) -- this bucket's
							# first point of contact back at the greenhouse,
							# same as the Standards side.
							cleanup_bucket_reuse_anomalies(
								bucket_id, farm, source_warehouse, variety, harvest_doc.name
							)

					if rose_type == "Spray Roses":
						# Spray roses are graded in place: source and target are the
						# greenhouse itself, so no Scan Location Mapping is needed.
						mapped_source_wh = source_warehouse
						mapped_target_wh = source_warehouse
					else:
						mapping_name = f"{farm}-MAP"
						try:
							mapping_doc = frappe.get_doc("Scan Location Mapping", mapping_name)
						except frappe.DoesNotExistError:
							frappe.throw(f"No Scan Location Mapping found named {mapping_name}")
						row = None
						for i in mapping_doc.items:
							if (i.action or "").lower() == stock_entry_type.lower():
								row = i
								break
						if not row:
							frappe.throw(
								f"No mapping row found for stock_entry_type '{stock_entry_type}' in {mapping_name}."
							)
						mapped_source_wh = row.get("source_warehouse")
						mapped_target_wh = source_warehouse

					frappe.log_error(
						"Grading Debug - Stock Entry",
						json.dumps(
							{
								"mapped_source_wh": mapped_source_wh,
								"mapped_target_wh": mapped_target_wh,
								"qty": qty,
							},
							default=str,
						),
					)

					ref_harvest = None
					if bucket_id:
						ref_harvest = frappe.db.get_value(
							"Stock Entry",
							{"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting", "docstatus": 1},
							["custom_greenhouse", "custom_harvester", "custom_cut_stage", "posting_date"],
							as_dict=True,
							order_by="creation desc",
						)
					se = frappe.new_doc("Stock Entry")
					se.stock_entry_type = stock_entry_type
					se.farm = farm  # Farm accounting dimension (mirrored to custom_farm by hook)
					se.company = company
					se.custom_business_unit = "Roses"
					se.custom_scanned_grading = 1
					se.custom_harvest_batch_no = harvest_batch_id
					se.custom_graded_by = graded_by
					se.custom_stem_length = stem_length
					se.custom_bunched_by = raw_bunch_size
					se.custom_bunch_id = bunch_id
					se.custom_greenhouse = source_warehouse or (
						ref_harvest.custom_greenhouse if ref_harvest else None
					)
					se.custom_bucket_id = bucket_id
					se.custom_harvester = (ref_harvest.custom_harvester if ref_harvest else None) or graded_by
					if ref_harvest and ref_harvest.get("custom_cut_stage"):
						se.custom_cut_stage = ref_harvest.custom_cut_stage
					se.custom_harvest_date = ref_harvest.posting_date if ref_harvest else None
					se.custom_grading_date = frappe.utils.nowdate()
					se.from_warehouse = mapped_source_wh
					se.to_warehouse = mapped_target_wh
					se.append(
						"items",
						{
							"s_warehouse": mapped_source_wh,
							"t_warehouse": mapped_target_wh,
							"item_code": variety,
							"qty": qty,
							"uom": raw_bunch_size,
							"conversion_factor": bunch_size,
							"allow_zero_valuation_rate": 1,
						},
					)
					se.insert(ignore_permissions=True)
					se.submit()
					frappe.log_error("Grading Debug", f"Final Stock Entry created: {se.name}")

					if rose_type == "Spray Roses":
						# Real, persisted link between the two entries this
						# single scan creates -- custom_harvest_batch_no was
						# meant to be this link but is not a real column, so
						# no stored connection between them existed before.
						# amendHarvestEntry needs custom_grading_entry to find
						# and correct this Grading entry too when the
						# Harvesting side is edited. Raw db.set_value rather
						# than a full doc.save(): both are already-submitted
						# Stock Entries, and re-running full validate on a
						# doctype this size for a single field is unnecessary
						# overhead neither side's data actually needs.
						frappe.db.set_value(
							"Stock Entry",
							harvest_doc.name,
							"custom_grading_entry",
							se.name,
							update_modified=False,
						)
						frappe.db.set_value(
							"Stock Entry",
							se.name,
							"custom_harvest_entry",
							harvest_doc.name,
							update_modified=False,
						)

					frappe.response["message"] = "Stock Entry submitted successfully"
					frappe.response["stock_entry"] = se.name
			except frappe.DoesNotExistError:
				# If warehouse doesn't exist or doesn't have the custom field, log and continue
				frappe.log_error(
					"Grading Validation Warning",
					f"Could not validate varieties for warehouse '{source_warehouse}'",
				)

	except Exception as e:
		frappe.log_error("Grading Error", str(e))
		frappe.response["http_status_code"] = 500
		frappe.response["message"] = "An error occurred while submitting the stock entry"
		frappe.response["error"] = str(e)


def head_size_mandatory():
	"""Production Settings > Production > Enable Head Size Mandatory."""
	if not frappe.get_meta("Production Settings").has_field("enable_head_size_mandatory"):
		return False
	return bool(frappe.db.get_single_value("Production Settings", "enable_head_size_mandatory"))


@frappe.whitelist()
def createHarvestStockEntry():
	try:
		# Required data from json:
		# farm
		# greenhouse
		# harvester
		# bucket_id
		# stem_length
		# item_code
		# quantity

		data = frappe.request.get_json()
		frappe.log_error("harvest payload", data)

		farm = data.get("farm")
		greenhouse = data.get("greenhouse")
		harvester = data.get("harvester")
		stem_length = data.get("stem_length")
		item_code = data.get("item_code")
		quantity = data.get("quantity")
		bucket_data = data.get("bucket_id")
		cut_stage = data.get("cut_stage")
		head_size = data.get("head_size")

		# Resolve harvester to an Employee id. custom_harvester is a Link to
		# Employee, so it needs the id; accept an employee_name too (older app
		# builds send the display name).
		if harvester and not frappe.db.exists("Employee", harvester):
			resolved_harvester = frappe.db.get_value("Employee", {"employee_name": harvester}, "name")
			if resolved_harvester:
				harvester = resolved_harvester

		# quantity must be a whole number of stems -- the app now strips
		# non-digits as it's typed, but a direct API caller (or an older app
		# build) could still send something like "100,200". Left as-is, that
		# reaches validate_bucket_stem_limit's float(new_stems or 0) below and
		# raises there instead of ever being compared against the limit, so
		# reject it explicitly and by name before it gets that far.
		try:
			quantity = int(quantity)
		except (TypeError, ValueError):
			frappe.throw(_(f"Quantity must be a whole number of stems, got '{quantity}'."))

		# Handle both string and JSON dict formats for bucket_id
		if isinstance(bucket_data, dict):
			bucket_id = list(bucket_data.keys())[0]
		else:
			bucket_id = str(bucket_data).strip()

		# Enforce the configurable per-bucket limit (Production Settings) --
		# shared with the Spray Roses cap in createGradingStockEntry, and
		# with corrections in amendHarvestEntry, so a limit configured once
		# applies consistently everywhere stems can land in a bucket.
		limit_error = validate_bucket_stem_limit(item_code, bucket_id, quantity)
		if limit_error:
			frappe.log_error("Bucket Rate Error", data)
			frappe.throw(_(limit_error))

		# Block Spray Roses -- they're harvested on the grading page instead
		# (see item_group_is_under's docstring for why this used to be a
		# flat, always-false check).
		if item_group_is_under(item_code, "Spray Roses"):
			frappe.log_error("Attempt to harvest sprays from the harvest form", data)
			frappe.throw(_("Spray Roses can only be harvested on the grading page"))

		stock_entry = frappe.new_doc("Stock Entry")

		farm_doc = frappe.get_doc("Farm", farm)
		stock_entry.stock_entry_type = "Harvesting"
		stock_entry.company = farm_doc.company
		# Set the Farm accounting dimension (source of truth). The
		# sync_accounting_dimensions validate hook mirrors it to custom_farm.
		stock_entry.farm = farm
		stock_entry.custom_greenhouse = greenhouse
		stock_entry.custom_harvester = harvester
		stock_entry.custom_bucket_id = bucket_id
		stock_entry.to_warehouse = greenhouse
		stock_entry.custom_stem_length = stem_length
		if cut_stage and frappe.db.exists("Cut Stage", cut_stage):
			stock_entry.custom_cut_stage = cut_stage
		if not head_size and head_size_mandatory():
			frappe.throw(_("Head size is required."))
		if head_size:
			if not frappe.db.exists("Head Size", head_size):
				frappe.throw(_(f"Head size '{head_size}' does not exist."))
			stock_entry.custom_head_size = head_size

		# create bucket if it does not exist
		if not frappe.db.exists("Bucket QR Code", bucket_id):
			frappe.get_doc({"doctype": "Bucket QR Code", "id": bucket_id, "status": "Available"}).insert(
				ignore_permissions=True
			)

		# ======================================
		# BUCKET STATUS VALIDATION (with row lock)
		# ======================================
		# Lock the bucket row to prevent race conditions from simultaneous harvest requests
		bucket_qr_doc = frappe.get_doc("Bucket QR Code", bucket_id, for_update=True)
		last_se = bucket_qr_doc.last_stock_entry
		last_se_doc = None
		last_farm = ""
		last_receiving_date = ""

		if last_se:
			last_se_doc = frappe.get_doc("Stock Entry", last_se)
			last_farm = last_se_doc.custom_farm

			days_ago = frappe.utils.date_diff(frappe.utils.today(), last_se_doc.posting_date)

			if days_ago == 0:
				last_receiving_date = "today"
			else:
				last_receiving_date = f"{days_ago} day(s) ago"

		# Block harvesting if bucket is already in use
		if bucket_qr_doc.status == "In Use":
			frappe.response["http_status_code"] = 400
			frappe.response["error"] = (
				f"Bucket {bucket_id} is already in use. Please receive it first before harvesting again."
			)
			frappe.throw(
				f"Bucket {bucket_id} is already in use. Please receive it first before harvesting again."
			)

		# ======================================
		# COMMENTED OUT: AUTO-RECEIVING LOGIC
		# ======================================
		# This logic was causing discrepancies between harvested and received quantities
		# It would auto-receive previous harvests when a new harvest was created
		#
		# if bucket_qr_doc.status == "In Use":
		#     # Check if 12 hours have passed since last stock entry
		#     if last_se_doc:
		#         hours_since_last_harvest = frappe.utils.time_diff_in_hours(
		#             frappe.utils.now_datetime(),
		#             last_se_doc.creation
		#         )
		#
		#         # Prevent double harvesting within 12 hours
		#         if hours_since_last_harvest < 12:
		#             hours_remaining = 12 - hours_since_last_harvest
		#             frappe.response["http_status_code"] = 400
		#             frappe.response["error"] = f"Bucket already harvested."
		#             frappe.throw(f"Bucket already harvested.")
		#
		#         # Only auto-receive if 12 hours have passed
		#         if hours_since_last_harvest >= 12:
		#             # Auto-receive the bucket with the posting date of last stock entry
		#             company = "Karen Roses"
		#             cost_center = "Main - KR"
		#
		#             # Get item info from last stock entry
		#             item_row = frappe.db.get_all(
		#                 "Stock Entry Detail",
		#                 filters={"parent": last_se},
		#                 fields=["item_code", "qty", "uom", "t_warehouse"],
		#                 order_by="idx asc",
		#                 limit_page_length=1
		#             )
		#
		#             if not item_row:
		#                 frappe.throw(f"No item found in last stock entry {last_se}")
		#
		#             item = item_row[0]
		#             last_item_code = item["item_code"]
		#             last_quantity = float(item["qty"])
		#             last_uom = item["uom"]
		#             from_warehouse = item["t_warehouse"]
		#             to_warehouse = f"{last_farm} Receiving Cold Store - KR"
		#
		#             # Get batch number from last stock entry
		#             batch_no = last_se_doc.custom_harvest_batch_no
		#
		#             # Create Receiving Stock Entry with posting date from last stock entry
		#             receiving_stock_entry = frappe.get_doc({
		#                 "doctype": "Stock Entry",
		#                 "stock_entry_type": "Receiving",
		#                 "custom_bucket_id": bucket_id,
		#                 "custom_harvest_batch_no": batch_no,
		#                 "set_posting_time": 1,
		#                 "posting_date": last_se_doc.posting_date,
		#                 "company": company,
		#                 "from_warehouse": from_warehouse,
		#                 "to_warehouse": to_warehouse,
		#                 "cost_center": cost_center,
		#                 "custom_farm": last_farm,
		#                 "custom_greenhouse": last_se_doc.custom_greenhouse,
		#                 "custom_harvester": last_se_doc.custom_harvester,
		#                 "custom_business_unit": "Roses",
		#                 "items": [
		#                     {
		#                         "item_code": last_item_code,
		#                         "qty": last_quantity,
		#                         "uom": last_uom,
		#                         "stock_uom": last_uom,
		#                         "t_warehouse": to_warehouse,
		#                         "s_warehouse": from_warehouse,
		#                         "cost_center": cost_center,
		#                     }
		#                 ]
		#             })
		#
		#             receiving_stock_entry.insert(ignore_permissions=True)
		#             receiving_stock_entry.submit()
		#
		#             # Mark last stock entry as scanned
		#             frappe.get_doc("Stock Entry", last_se).db_set("custom_scanned", 1)
		#             frappe.db.commit()

		timestamp = frappe.utils.now_datetime()
		harvest_batch_id = f"{bucket_id}-{farm}-{item_code}-{timestamp}"

		stock_entry.custom_harvest_batch_no = harvest_batch_id
		stock_entry.custom_business_unit = "Roses"

		stock_entry.append("items", {"item_code": item_code, "qty": quantity, "allow_zero_valuation_rate": 1})

		se = stock_entry.insert()
		se_submit = stock_entry.submit()

		if se and se_submit:
			# ======================================
			# UPDATE BUCKET STATUS TO "In Use"
			# ======================================
			# Mark bucket as In Use and link this stock entry so subsequent
			# harvest attempts on the same bucket are blocked until receiving.
			# This harvest is ALWAYS a new journey's start -- the guard above
			# already refused to reach here while the bucket was In Use, so
			# every Standard Roses harvest is, by construction, the one entry
			# of its own journey. current_journey_start is what Receiving and
			# Bucket Status scope aggregation to, so an earlier abandoned
			# journey's leftover unclaimed entries (stems never received,
			# long gone) can never get swept into this one.
			bucket_qr_doc.status = "In Use"
			bucket_qr_doc.last_stock_entry = stock_entry.name
			bucket_qr_doc.current_journey_start = stock_entry.name
			bucket_qr_doc.save(ignore_permissions=True)
			frappe.db.commit()

			# This is the bucket's first point of contact back at the
			# greenhouse for a new journey -- clear any stale shelf/discard
			# residue from its previous life now, not when it fails at
			# shelving time later.
			cleanup_bucket_reuse_anomalies(bucket_id, farm, greenhouse, item_code, stock_entry.name)

			frappe.response["message"] = "Harvesting Stock Entry submitted successfully"
			frappe.response["stock_entry"] = stock_entry.name

	except frappe.PermissionError as e:
		# frappe.log_error("Permission Error - Stock Entry Creation", f"User {frappe.session.user} does not have permission to create Stock Entry")
		frappe.response["http_status_code"] = 200
		frappe.response["error"] = (
			"You do not have sufficient permissions to create Stock Entry. Please contact your IT Administrator"
		)

		# Don't ever remove this throw line. The validation on frontend will fail
		#
		#
		frappe.throw("Permission denied: You do not have sufficient permissions to create Stock Entry")

	except frappe.ValidationError as e:
		# Deliberate business rejections raised above (bucket already in use,
		# spray roses on the harvest form, over the per-bucket stem cap). Surface
		# them as a clean 400 with the specific message — must be caught BEFORE the
		# generic handler, or it would mask them as a 500 "An error occurred".
		frappe.response["http_status_code"] = frappe.response.get("http_status_code") or 400
		frappe.response["message"] = str(e)
		frappe.response["error"] = frappe.response.get("error") or str(e)

	except Exception as e:
		frappe.log_error("Harvesting Error", str(e))
		frappe.response["http_status_code"] = 500
		frappe.response["message"] = "An error occurred while submitting stock entry"
		frappe.response["error"] = str(e)


@frappe.whitelist()
def fetchBucketContents():
	data = frappe.request.get_json() or {}
	bucket_id = (data.get("bucket_id") or "").strip()
	contents = []
	total = 0
	if bucket_id:
		ses = frappe.get_all(
			"Stock Entry",
			filters={
				"custom_bucket_id": bucket_id,
				"stock_entry_type": "Harvesting",
				"docstatus": 1,
				"posting_date": frappe.utils.today(),
			},
			pluck="name",
		)
		if ses:
			rows = frappe.get_all(
				"Stock Entry Detail",
				filters={"parent": ["in", ses]},
				fields=["item_code", "qty"],
			)
			agg = {}
			for r in rows:
				v = r.get("item_code") or "Unspecified"
				q = int(r.get("qty") or 0)
				agg[v] = agg.get(v, 0) + q
				total = total + q
			for v in sorted(agg):
				contents.append({"variety": v, "stems": agg[v]})
	frappe.response["message"] = {"bucket_id": bucket_id, "contents": contents, "total_stems": total}


@frappe.whitelist()
def getBucketStatus():
	"""Read-only "what's in this bucket" lookup for the mobile Bucket Status
	screen: always the LATEST harvest journey for this bucket, whether or
	not it's been received yet -- a bucket harvested a week ago and still
	genuinely unreceived must keep showing that, not silently reset to
	"nothing here" the moment a day boundary passes.

	"Latest journey" = every Harvesting entry sharing the same fate:
	  - if the most recent Harvesting entry is still unclaimed, the journey
	    is every unclaimed entry from Bucket QR Code.current_journey_start
	    onward, when that marker exists (mirrors createReceivingStockEntry's
	    own scoping -- exactly what a receive would pick up right now).
	    When it doesn't exist, this is just the single latest entry -- never
	    a guessed aggregate of whatever else happens to share this
	    bucket_id, since bucket ids are not farm-scoped and can collide;
	  - if it's already been received, the journey is every Harvesting
	    entry that was linked to that SAME Receiving entry, and the
	    response says so explicitly (received=True, plus when).

	Deliberately NOT scoped to today (unlike fetchBucketContents, the live
	tally on the grading screen) and NOT gated on Bucket QR Code.status --
	that field only tracks "can this be harvested into right now", not
	"has the latest journey been received", which is what this answers.

	Spray Roses naturally show one row per grading scan (each is its own
	Harvesting entry, possibly mixed varieties/lengths); Standard Roses
	show a single row, since a second harvest into an In Use bucket is
	blocked outright.
	"""
	data = frappe.request.get_json() or {}
	bucket_id = (data.get("bucket_id") or "").strip()
	if not bucket_id:
		frappe.response["data"] = {"error": "bucket_id is required."}
		return
	if not frappe.db.exists("Bucket QR Code", bucket_id):
		frappe.response["data"] = {"error": "Bucket " + bucket_id + " does not exist."}
		return

	harvests = frappe.get_all(
		"Stock Entry",
		filters={"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting", "docstatus": 1},
		fields=[
			"name",
			"farm",
			"custom_greenhouse",
			"custom_stem_length",
			"creation",
			"custom_receiving_entry",
			"custom_cut_stage",
			"custom_head_size",
			"custom_harvester",
		],
		order_by="creation desc",
	)
	if not harvests:
		frappe.response["data"] = {
			"bucket_id": bucket_id,
			"received": None,
			"received_at": None,
			"entries": [],
			"total_stems": 0,
		}
		return

	latest_receiving_entry = harvests[0].get("custom_receiving_entry")
	if latest_receiving_entry:
		journey = [h for h in harvests if h.get("custom_receiving_entry") == latest_receiving_entry]
	else:
		# Latest only -- never guess a journey boundary by aggregating
		# historical "unclaimed" entries. That invented aggregation (not
		# something asked for) is what actually caused a real 2026-09-09
		# incident: bucket "e36be6" had unclaimed Harvesting entries at FOUR
		# different farms spanning six weeks (a bucket_id collision seeded
		# by a migration/backfill run), and aggregating "every unclaimed
		# entry" merged all of it into one Receiving entry.
		#
		# Scope to the CURRENT journey via the marker current_journey_start
		# (set on every harvest that starts a fresh journey; see
		# createHarvestStockEntry / createGradingStockEntry) when it's
		# there -- several spray grading scans can legitimately share one
		# real session. When it's not there (a bucket whose journey started
		# before this field existed, or any other gap), don't fall back to
		# scanning history at all: show just the single latest Harvesting
		# entry, same as createReceivingStockEntry now does.
		journey_start = frappe.db.get_value("Bucket QR Code", bucket_id, "current_journey_start")
		start_creation = (
			frappe.db.get_value("Stock Entry", journey_start, "creation") if journey_start else None
		)
		if start_creation:
			journey = [
				h
				for h in harvests
				if not h.get("custom_receiving_entry") and h.get("creation") >= start_creation
			]
		else:
			journey = [harvests[0]]

	received_at = None
	if latest_receiving_entry:
		received_at = frappe.db.get_value("Stock Entry", latest_receiving_entry, "creation")

	# custom_harvester is a Link to Employee (an id like "003") -- resolve to
	# the display name once for every harvester appearing in this journey,
	# rather than a get_value per row.
	harvester_ids = [h.get("custom_harvester") for h in journey if h.get("custom_harvester")]
	harvester_names = {}
	if harvester_ids:
		for row in frappe.get_all(
			"Employee", filters={"name": ["in", harvester_ids]}, fields=["name", "employee_name"]
		):
			harvester_names[row["name"]] = row.get("employee_name") or row["name"]

	entries = []
	total = 0
	for h in journey:
		rows = frappe.get_all(
			"Stock Entry Detail", filters={"parent": h["name"]}, fields=["item_code", "qty"]
		)
		for r in rows:
			qty = int(r.get("qty") or 0)
			total = total + qty
			entries.append(
				{
					"name": h.get("name"),
					"variety": r.get("item_code") or "Unspecified",
					"stem_length": h.get("custom_stem_length") or "Unspecified",
					"greenhouse": h.get("custom_greenhouse") or "Unspecified",
					"cut_stage": h.get("custom_cut_stage") or "Unspecified",
					"head_size": h.get("custom_head_size") or "Unspecified",
					"harvester": harvester_names.get(h.get("custom_harvester"))
					or h.get("custom_harvester")
					or "Unspecified",
					"qty": qty,
					"time": str(h.get("creation")),
				}
			)

	frappe.response["data"] = {
		"bucket_id": bucket_id,
		"received": bool(latest_receiving_entry),
		"received_at": str(received_at) if received_at else None,
		"entries": entries,
		"total_stems": total,
	}


@frappe.whitelist()
def amendHarvestEntry():
	"""Corrects a still-pending Harvesting entry's variety, stem length, cut
	stage and/or quantity -- from the mobile Bucket Status screen's per-row
	Edit button.

	A Harvesting Stock Entry is already submitted (docstatus=1) by the time
	it shows up on Bucket Status, so a mis-scanned value can't just be
	field-edited -- this goes through Frappe's proper cancel+amend flow
	instead (cancel, then a fresh amended doc carrying amended_from), which
	is what keeps the audit trail honest.

	Spray Roses create BOTH a Harvesting entry and a Grading entry from the
	same scan (see createGradingStockEntry) -- correcting only the
	Harvesting side would leave the Grading entry (the one actually carrying
	the graded/processed stock movement) silently wrong. custom_grading_entry
	is the real, persisted link between the two (see
	add_harvest_grading_link_fields), so when it's set this cascades the
	same correction to that Grading entry too, via its own cancel+amend.
	Variety/stem_length/cut_stage/head_size carry over as-is (same field, same value on
	both docs); qty does not -- the Harvesting item is always in raw stems,
	while the Grading item is in bunches (qty * conversion_factor = stems),
	so the corrected stem qty is converted back to bunches using that same
	Grading item's own conversion_factor (the bunch size itself is not being
	changed here).

	Deliberately refuses once the entry has been received (custom_
	receiving_entry set): at that point its stems are already aggregated
	into a Receiving entry's quantities, and correcting those too is a
	separate, bigger change this does not attempt. Bucket Status itself
	only shows the Edit button while received=false, but this is enforced
	here too in case the bucket gets received in the gap between loading
	the screen and saving the edit.
	"""
	data = frappe.request.get_json() or {}
	stock_entry_name = (data.get("stock_entry_name") or "").strip()
	new_variety = (data.get("variety") or "").strip() or None
	new_stem_length = (data.get("stem_length") or "").strip() or None
	new_cut_stage = (data.get("cut_stage") or "").strip() or None
	new_head_size = (data.get("head_size") or "").strip() or None
	raw_qty = data.get("qty")
	new_qty = None
	if raw_qty is not None and str(raw_qty).strip() != "":
		try:
			new_qty = float(raw_qty)
		except (TypeError, ValueError):
			frappe.response["data"] = {"error": "Quantity must be a number."}
			return
		if new_qty <= 0:
			frappe.response["data"] = {"error": "Quantity must be a positive number."}
			return

	if not stock_entry_name:
		frappe.response["data"] = {"error": "stock_entry_name is required."}
		return
	if (
		not new_variety
		and not new_stem_length
		and not new_cut_stage
		and not new_head_size
		and new_qty is None
	):
		frappe.response["data"] = {
			"error": "Nothing to change -- provide a variety, stem_length, cut_stage, head_size and/or qty."
		}
		return
	if new_head_size and not frappe.db.exists("Head Size", new_head_size):
		frappe.response["data"] = {"error": "Head size " + new_head_size + " does not exist."}
		return
	if not frappe.db.exists("Stock Entry", stock_entry_name):
		frappe.response["data"] = {"error": "Stock Entry " + stock_entry_name + " does not exist."}
		return

	original = frappe.get_doc("Stock Entry", stock_entry_name)

	if original.stock_entry_type != "Harvesting":
		frappe.response["data"] = {"error": "Only Harvesting entries can be edited here."}
		return
	if original.docstatus != 1:
		frappe.response["data"] = {"error": "This entry isn't submitted -- nothing to amend."}
		return
	if original.custom_receiving_entry:
		frappe.response["data"] = {
			"error": "This entry has already been received and can no longer be edited."
		}
		return

	current_variety = original.items[0].item_code if original.items else None
	current_stem_length = original.custom_stem_length
	current_cut_stage = original.custom_cut_stage
	current_head_size = original.custom_head_size
	current_qty = original.items[0].qty if original.items else None
	final_variety = new_variety or current_variety
	final_stem_length = new_stem_length or current_stem_length
	final_cut_stage = new_cut_stage or current_cut_stage
	final_head_size = new_head_size or current_head_size
	final_qty = new_qty if new_qty is not None else current_qty

	if (
		final_variety == current_variety
		and final_stem_length == current_stem_length
		and final_cut_stage == current_cut_stage
		and final_head_size == current_head_size
		and final_qty == current_qty
	):
		# Nothing actually changed -- skip the cancel+amend churn entirely.
		frappe.response["data"] = {
			"stock_entry": original.name,
			"variety": current_variety,
			"stem_length": current_stem_length,
			"cut_stage": current_cut_stage,
			"head_size": current_head_size,
			"qty": current_qty,
			"amended": False,
		}
		return

	if final_variety != current_variety:
		not_planted_error = validate_variety_planted_in_greenhouse(final_variety, original.custom_greenhouse)
		if not_planted_error:
			frappe.response["data"] = {"error": not_planted_error}
			return

	bucket_id = original.custom_bucket_id

	if final_qty != current_qty or final_variety != current_variety:
		# A correction must obey the same per-bucket stem cap a fresh scan
		# would have -- exclude_stock_entry leaves this entry's OWN current
		# qty out of "today's other entries" (see validate_bucket_stem_limit)
		# so it isn't double-counted against the cap it's being checked
		# against.
		limit_error = validate_bucket_stem_limit(
			final_variety, bucket_id, final_qty, exclude_stock_entry=original.name
		)
		if limit_error:
			frappe.response["data"] = {"error": limit_error}
			return

	was_last_entry = bool(
		bucket_id and frappe.db.get_value("Bucket QR Code", bucket_id, "last_stock_entry") == original.name
	)
	linked_grading_name = original.custom_grading_entry

	# A submitted Grading entry still pointing at this Harvesting entry via
	# custom_harvest_entry blocks Frappe's own check_no_back_links_exist --
	# cancel() refuses to proceed while another submitted doc still links
	# here. Break that reciprocal link first; it's re-established (pointing
	# at the new amended docs) once both sides are done below.
	if linked_grading_name and frappe.db.exists("Stock Entry", linked_grading_name):
		frappe.db.set_value(
			"Stock Entry", linked_grading_name, "custom_harvest_entry", None, update_modified=False
		)

	original.cancel()

	amended = frappe.copy_doc(original)
	amended.amended_from = original.name
	if amended.items:
		amended.items[0].item_code = final_variety
		amended.items[0].qty = final_qty
	amended.custom_stem_length = final_stem_length
	amended.custom_cut_stage = final_cut_stage
	amended.custom_head_size = final_head_size
	amended.insert(ignore_permissions=True)
	amended.submit()

	if was_last_entry:
		frappe.db.set_value(
			"Bucket QR Code", bucket_id, "last_stock_entry", amended.name, update_modified=False
		)

	amended_grading_name = None
	if linked_grading_name and frappe.db.exists("Stock Entry", linked_grading_name):
		grading_original = frappe.get_doc("Stock Entry", linked_grading_name)
		if grading_original.docstatus == 1:
			# Same problem, the other direction: `amended` (just created,
			# submitted) still carries the OLD grading name in
			# custom_grading_entry -- copy_doc copied it forward from
			# `original` -- which would block cancelling the grading side.
			frappe.db.set_value(
				"Stock Entry", amended.name, "custom_grading_entry", None, update_modified=False
			)

			grading_original.cancel()

			grading_amended = frappe.copy_doc(grading_original)
			grading_amended.amended_from = grading_original.name
			if grading_amended.items:
				grading_amended.items[0].item_code = final_variety
				if new_qty is not None:
					# Harvest qty is raw stems; the Grading item is in bunches
					# (qty * conversion_factor = stems) -- convert back using
					# its own existing conversion_factor (bunch size), which
					# is not being changed here.
					conversion_factor = grading_amended.items[0].conversion_factor or 1
					grading_amended.items[0].qty = final_qty / conversion_factor
			grading_amended.custom_stem_length = final_stem_length
			grading_amended.custom_cut_stage = final_cut_stage
			grading_amended.custom_head_size = final_head_size
			grading_amended.custom_harvest_entry = amended.name
			grading_amended.insert(ignore_permissions=True)
			grading_amended.submit()
			amended_grading_name = grading_amended.name

			# amended (harvest side) still points at the OLD grading entry --
			# copy_doc carried that stale value forward from `original`.
			frappe.db.set_value(
				"Stock Entry",
				amended.name,
				"custom_grading_entry",
				amended_grading_name,
				update_modified=False,
			)
		else:
			# Orphaned link (grading side already cancelled some other way) --
			# don't block the harvest-side fix over it, just surface that the
			# grading entry wasn't touched.
			frappe.log_error(
				"amendHarvestEntry: linked Grading entry not in a submitted state",
				f"harvest={amended.name} grading={linked_grading_name} docstatus={grading_original.docstatus}",
			)

	frappe.db.commit()

	frappe.response["data"] = {
		"stock_entry": amended.name,
		"grading_entry": amended_grading_name,
		"variety": final_variety,
		"stem_length": final_stem_length,
		"cut_stage": final_cut_stage,
		"head_size": final_head_size,
		"qty": final_qty,
		"amended": True,
	}


@frappe.whitelist()
def getGreenhouseData():
	try:
		data = frappe.request.get_json()
		greenhouse_name = data.get("greenhouse_name")
		if not greenhouse_name:
			frappe.throw("Greenhouse name is required.")

		# Varieties come from the Greenhouse doctype's live ledger
		# (varieties_grown) — exactly what is standing in the house today.
		# This is the sole source: a greenhouse whose ledger is empty shows no
		# varieties (the notice below tells the user to build it), rather than
		# falling back to Crop Cycles.
		varieties = []
		seen = []
		vg_rows = frappe.get_all(
			"Greenhouse Variety",
			filters={"parent": greenhouse_name, "parenttype": "Greenhouse", "parentfield": "varieties_grown"},
			fields=["variety", "area_m2"],
			order_by="area_m2 desc",
		)
		for r in vg_rows:
			v = r.get("variety")
			if v and v not in seen:
				seen.append(v)
				item_group = frappe.db.get_value("Item", v, "item_group")
				# A real ancestor-aware check, not a string match against
				# item_group -- callers that need "Standard Roses only"
				# (the harvest form) can't safely filter on item_group
				# itself, since real items always live in a leaf group like
				# "Spray Roses - Bombastics", never the bare parent name.
				varieties.append(
					{
						"variety": v,
						"area": r.get("area_m2"),
						"item_group": item_group,
						"is_spray_rose": item_group_is_under(v, "Spray Roses"),
					}
				)

		# Fetch employees (harvesters) linked to this greenhouse via the
		# custom_greenhouses child table. A harvester can belong to several
		# greenhouses, so match any Employee Greenhouse row for this greenhouse.
		greenhouse_links = frappe.get_all(
			"Employee Greenhouse",
			filters={"greenhouse": greenhouse_name, "parenttype": "Employee"},
			fields=["parent"],
		)
		employee_ids = []
		for row in greenhouse_links:
			pid = row.get("parent")
			if pid and pid not in employee_ids:
				employee_ids.append(pid)
		employees = []
		if employee_ids:
			employees = frappe.get_all(
				"Employee",
				filters={"name": ["in", employee_ids]},
				# `name` (the actual Employee id, e.g. "500505" here -- naming
				# is by employee number, not employee_name) is required: the
				# app was submitting employee_name as if it were the id into
				# custom_harvester/graded_by, both Link(Employee) fields, so
				# every harvest/grading entry naming a harvester failed Link
				# validation ("Could not find Employee: <full name>").
				fields=["name", "employee_name"],
			)

		# Cut stages come from the Cut Stage master (named by the cutstage value).
		cut_stage_rows = frappe.get_all("Cut Stage", fields=["cutstage"], order_by="cutstage asc")
		cut_stages = []
		for cs in cut_stage_rows:
			v = cs.get("cutstage")
			if v and v not in cut_stages:
				cut_stages.append(v)

		head_sizes = []
		if frappe.db.table_exists("Head Size"):
			head_sizes = frappe.get_all("Head Size", pluck="name", order_by="name asc")

		notice = None
		if not varieties:
			notice = "Nothing is standing in this greenhouse yet. Create a crop cycle to load its varieties."

		frappe.response["data"] = {
			"varieties": varieties,
			"employees": employees,
			"cut_stages": cut_stages,
			"head_sizes": head_sizes,
			"head_size_mandatory": head_size_mandatory(),
			"notice": notice,
		}
	except Exception as e:
		frappe.log_error(message=e, title="Get greenhouse data error")
		frappe.throw(_("Error fetching greenhouse data: ") + str(e))


def forecast_lock_weeks():
	"""Production Settings > Production > Forecast Locked for the Next (Weeks).
	Defaults to 5 when the setting is missing or blank."""
	if not frappe.get_meta("Production Settings").has_field("forecast_lock_weeks"):
		return 5
	# get_single_value turns a never-saved Int into 0; read the raw row so an
	# unset value falls back to the default instead of unlocking everything.
	rows = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'Production Settings' and field = 'forecast_lock_weeks'"
	)
	value = rows[0][0] if rows else None
	if value is None or value == "":
		return 5
	return max(0, frappe.utils.cint(value))


def forecast_week_editable(year, week, lock_weeks=None):
	"""A revised forecast can only change for weeks more than `lock_weeks`
	ahead of the current week -- e.g. in week 30 with 5, weeks 36 onward."""
	if lock_weeks is None:
		lock_weeks = forecast_lock_weeks()
	try:
		target = datetime.date.fromisocalendar(int(year), int(week), 1)
	except ValueError:
		return False
	today = frappe.utils.getdate(frappe.utils.nowdate())
	this_monday = today - datetime.timedelta(days=today.weekday())
	return (target - this_monday).days // 7 > lock_weeks


def _forecast_window(year):
	"""First and last week a new Production Forecast for `year` covers."""
	iso = frappe.utils.getdate(frappe.utils.nowdate()).isocalendar()
	lastweek = frappe.utils.getdate(str(year) + "-12-28").isocalendar()[1]
	startweek = iso[1] if iso[0] == year else 1
	return startweek, lastweek


def _new_production_forecast(greenhouse, variety, year):
	startweek, lastweek = _forecast_window(year)
	window = lastweek - startweek + 1
	if window < 1:
		window = 1
	fdoc = frappe.new_doc("Production Forecast")
	fdoc.greenhouse = greenhouse
	fdoc.variety = variety
	fdoc.forecast_year = year
	fdoc.start_week = startweek
	fdoc.window_weeks = window
	fdoc.insert(ignore_permissions=True)
	frappe.db.commit()
	return fdoc.name


@frappe.whitelist()
def getProductionProjection():
	greenhouse = frappe.form_dict.get("greenhouse")
	variety = frappe.form_dict.get("variety")
	year = frappe.form_dict.get("year")

	if not greenhouse or not variety or not year:
		frappe.throw("Missing required fields: greenhouse, variety, year")

	year = int(year)

	# Budget: the system-generated Production Projection for this house x variety.
	proj = frappe.db.get_value(
		"Production Projection",
		{"greenhouse": greenhouse, "variety": variety, "projection_year": year},
		"name",
	)
	budget = {}
	if proj:
		for r in frappe.get_all(
			"Projection Week", filters={"parent": proj}, fields=["week", "projected_stems"]
		):
			budget[int(r.get("week") or 0)] = int(r.get("projected_stems") or 0)

	# Revisions live on Production Forecast now. Make sure an Active one exists
	# so the app's Revised column has editable rows to write into.
	fc = frappe.get_all(
		"Production Forecast",
		filters={"greenhouse": greenhouse, "variety": variety, "forecast_year": year, "status": "Active"},
		order_by="revision desc",
		limit=1,
		pluck="name",
	)
	fcname = fc[0] if fc else _new_production_forecast(greenhouse, variety, year)

	fcrows = {}
	for r in frappe.get_all(
		"Production Forecast Week",
		filters={"parent": fcname, "grade": "all"},
		fields=[
			"name",
			"week_number",
			"budget_stems",
			"manual_budget_stems",
			"revised_forecast_stems",
			"actual_stems",
			"note",
		],
	):
		fcrows[int(r.get("week_number") or 0)] = r

	# Live actuals straight from Harvesting entries, per ISO week (WEEK mode 3).
	actuals = frappe.db.sql(
		"""
        SELECT WEEK(se.posting_date, 3) AS week_no, SUM(sed.qty) AS total_stems
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.stock_entry_type = 'Harvesting'
          AND (se.custom_greenhouse = %(greenhouse)s OR sed.t_warehouse = %(greenhouse)s)
          AND sed.item_code = %(variety)s
          AND se.posting_date BETWEEN %(first)s AND %(last)s
        GROUP BY WEEK(se.posting_date, 3)
    """,
		{
			"greenhouse": greenhouse,
			"variety": variety,
			"first": str(year) + "-01-01",
			"last": str(year) + "-12-31",
		},
		as_dict=True,
	)
	actuals_map = {}
	for row in actuals:
		actuals_map[int(row.week_no)] = int(row.total_stems)

	weeknums = []
	for wk in budget:
		if wk not in weeknums:
			weeknums.append(wk)
	for wk in fcrows:
		if wk not in weeknums:
			weeknums.append(wk)
	weeknums = sorted(weeknums)

	lock_weeks = forecast_lock_weeks()
	weeks = []
	for wk in weeknums:
		fr = fcrows.get(wk)
		weeks.append(
			{
				# Rows inside the forecast window carry the real child-row name and
				# are editable; rows outside it are budget-only and read back as
				# skipped if the app tries to save them.
				"name": fr.get("name") if fr else "week-" + str(wk),
				"week_no": wk,
				"projected_qty": (fr and int(fr.get("manual_budget_stems") or 0))
				or budget.get(wk, int(fr.get("budget_stems") or 0) if fr else 0),
				"actual_harvest": actuals_map.get(wk, int(fr.get("actual_stems") or 0) if fr else 0),
				"revised_forecast": int(fr.get("revised_forecast_stems") or 0) if fr else 0,
				"custom_comment": (fr.get("note") or "") if fr else "",
				"editable": forecast_week_editable(year, wk, lock_weeks),
			}
		)

	area = 0.0
	for c in frappe.get_all(
		"Crop Cycle",
		filters={"greenhouse": greenhouse, "variety": variety, "status": ["!=", "Ended"]},
		fields=["planted_area"],
	):
		area = area + float(c.get("planted_area") or 0)

	crop_type = frappe.db.get_value("Item", variety, "item_group") or ""
	colour = ""
	try:
		colour = frappe.db.get_value("Item", variety, "custom_colour") or ""
	except Exception:
		colour = ""

	wh = frappe.db.get_value("Warehouse", greenhouse, "custom_farm") or ""

	frappe.response["message"] = {
		"status": "success",
		"name": fcname,
		"farm": wh,
		"greenhouse": greenhouse,
		"crop_variety": variety,
		"crop_type": crop_type,
		"colour": colour,
		"area": area,
		"year": year,
		"weeks": weeks,
	}


@frappe.whitelist()
def productionCreateCropCycle():
	data = frappe.request.get_json() or {}
	greenhouse = (data.get("greenhouse") or "").strip()
	farm = (data.get("farm") or "").strip()
	company = (data.get("company") or "").strip()
	plants_per_sqm = data.get("plants_per_sqm")
	gross_area = data.get("gross_area")
	bed_config = data.get("bed_config") or []

	if not greenhouse:
		frappe.response["data"] = {"error": "greenhouse is required."}
	elif not bed_config:
		frappe.response["data"] = {"error": "At least one bed range is required."}
	else:
		try:
			# New model: ONE Crop Cycle per variety. The app's bed_config rows
			# (each with its own variety) are grouped by variety and become one
			# cycle each; the Greenhouse ledger is built automatically by the
			# Crop Cycle controller on save.
			groups = {}
			order = []
			for bc in bed_config:
				variety = (bc.get("variety") or "").strip()
				from_bed = int(bc.get("from_bed") or 0)
				to_bed = int(bc.get("to_bed") or 0)
				if not variety or from_bed <= 0 or to_bed < from_bed:
					continue
				if variety not in groups:
					groups[variety] = {"ranges": [], "bed_length": 0.0, "bed_width": 0.0, "planting_date": ""}
					order.append(variety)
				g = groups[variety]
				g["ranges"].append([from_bed, to_bed])
				if bc.get("bed_length"):
					g["bed_length"] = float(bc.get("bed_length"))
				if bc.get("bed_width"):
					g["bed_width"] = float(bc.get("bed_width"))
				pd = (bc.get("planting_date") or "").strip()
				if pd and not g["planting_date"]:
					g["planting_date"] = pd

			if not groups:
				frappe.response["data"] = {
					"error": "At least one valid bed range (variety + bed numbers) is required."
				}
			else:
				# Bed records must exist before a cycle can claim them; create
				# any that are missing, carrying the typed dimensions.
				existing = {}
				for b in frappe.get_all("Bed", filters={"greenhouse": greenhouse}, fields=["bed"]):
					try:
						existing[int(b.get("bed"))] = 1
					except Exception:
						pass
				created_beds = 0
				for variety in order:
					g = groups[variety]
					length = g["bed_length"] or 30.0
					width = g["bed_width"] or 1.0
					for rng in g["ranges"]:
						n = rng[0]
						while n <= rng[1]:
							if n not in existing:
								bed = frappe.new_doc("Bed")
								bed.greenhouse = greenhouse
								bed.unit_type = "Bed"
								bed.bed = n
								bed.bed_length = length
								bed.bed_width = width
								bed.variety = variety
								# both spellings: upande_core says bed_area,
								# upande_scp says bed__area; the extra one is
								# ignored on whichever site lacks it.
								bed.bed_area = length * width
								bed.bed__area = length * width
								bed.insert(ignore_permissions=True)
								existing[n] = 1
								created_beds = created_beds + 1
							n = n + 1

				from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import (
					prepare_bed_range,
				)

				names = []
				replaced = []
				for variety in order:
					g = groups[variety]
					parts = []
					for rng in sorted(g["ranges"]):
						if rng[0] == rng[1]:
							parts.append(str(rng[0]))
						else:
							parts.append(str(rng[0]) + "-" + str(rng[1]))
					# Planting over beds another cycle still holds replaces it
					# there: same as the desk's preflight, the old cycle gets an
					# uproot logged on those beds (and ends if nothing's left).
					prep = prepare_bed_range(
						greenhouse,
						", ".join(parts),
						variety,
						planting_date=g["planting_date"] or None,
						bed_length=g["bed_length"] or None,
						bed_width=g["bed_width"] or None,
					)
					for u in prep.get("uprooted") or []:
						replaced.append(u["cycle"] + " (" + (u["variety"] or "") + ") beds " + u["beds"])
					doc = frappe.new_doc("Crop Cycle")
					doc.greenhouse = greenhouse
					if farm and frappe.db.exists("Farm", farm):
						doc.farm = farm
					if company:
						doc.company = company
					doc.variety = variety
					protocol = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "name")
					if protocol:
						doc.crop_protocol = protocol
					doc.planting_date = g["planting_date"] or frappe.utils.nowdate()
					doc.bed_range = ", ".join(parts)
					doc.status = "Active"
					dens = plants_per_sqm
					if not dens and protocol:
						dens = frappe.db.get_value("Crop Protocol", protocol, "plants_per_sqm")
					if dens:
						doc.plants_per_sqm = float(dens)
					doc.insert(ignore_permissions=True)
					names.append(doc.name)

				if gross_area and frappe.db.exists("Greenhouse", greenhouse):
					frappe.db.set_value("Greenhouse", greenhouse, "gross_area", float(gross_area))

				frappe.db.commit()
				frappe.response["data"] = {
					"status": "success",
					"name": names[0],
					"names": names,
					"message": str(len(names))
					+ " crop cycle(s) created: "
					+ ", ".join(names)
					+ (". Replaced " + "; ".join(replaced) if replaced else ""),
					"replaced": replaced,
					"beds": created_beds,
					"varieties": len(names),
				}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionCreateCropCycle error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionCreateSeedlingRequest():
	data = frappe.request.get_json() or {}
	required_by_date = (data.get("required_by_date") or "").strip()
	priority = (data.get("priority") or "Medium").strip()
	farm = (data.get("farm") or "").strip()
	target_farm_block = (data.get("target_farm_block") or "").strip()
	reason = (data.get("reason") or "").strip()
	items = data.get("items") or []

	# Resolve a default Company. `frappe.defaults.get_user_default` isn't safe in
	# the sandbox on every site, so read Global Defaults or pick the first Company.
	company = (data.get("company") or "").strip()
	if not company:
		try:
			company = frappe.db.get_single_value("Global Defaults", "default_company") or ""
		except Exception:
			company = ""
	if not company:
		rows = frappe.get_all("Company", fields=["name"], limit=1)
		if rows:
			company = rows[0].get("name") or ""

	if not required_by_date:
		frappe.response["data"] = {"error": "required_by_date is required."}
	elif not items or not isinstance(items, list):
		frappe.response["data"] = {"error": "items is required (non-empty list)."}
	elif not company:
		frappe.response["data"] = {"error": "Cannot resolve a default Company for the request."}
	else:
		try:
			doc = frappe.new_doc("Seedling Request")
			doc.requested_by = frappe.session.user
			doc.request_date = frappe.utils.today()
			doc.required_by_date = required_by_date
			if priority:
				doc.priority = priority
			if farm:
				doc.farm = farm
			if target_farm_block:
				doc.target_farm_block = target_farm_block
			if reason:
				doc.reason = reason
			doc.company = company

			for it in items:
				variety = (it.get("variety") or "").strip()
				qty = it.get("qty")
				if not variety or qty is None:
					continue
				row = doc.append("items", {})
				row.variety = variety
				row.qty = int(qty)
				stem_length = (it.get("stem_length") or "").strip()
				if stem_length:
					row.stem_length = stem_length

			if not doc.get("items"):
				frappe.response["data"] = {
					"error": "No valid items provided (variety + qty required per row)."
				}
			else:
				doc.insert(ignore_permissions=True)
				frappe.db.commit()
				frappe.response["data"] = {
					"status": "success",
					"name": doc.name,
					"message": "Seedling Request " + doc.name + " created.",
				}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionCreateSeedlingRequest error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionGetGreenhouseSpec():
	data = frappe.request.get_json() or {}
	greenhouse = (data.get("greenhouse") or "").strip()
	try:
		if not greenhouse:
			frappe.response["data"] = {"error": "greenhouse is required."}
		else:
			wh = frappe.db.get_value("Warehouse", greenhouse, ["warehouse_name", "custom_farm"], as_dict=True)
			farm = ""
			label = greenhouse
			if wh:
				label = wh.get("warehouse_name") or greenhouse
				farm = wh.get("custom_farm") or ""
			spec = {
				"name": greenhouse,
				"label": label,
				"farm": farm,
				"has_cycle": 0,
				"total_beds": 0,
				"total_planted_area": 0.0,
				"number_of_plants": 0,
				"variety_count": 0,
				"varieties": [],
				"bed_summary": [],
				"bays": [],
				"growers": [],
			}
			# The Greenhouse doctype's ledger is the source of truth now: its
			# rollups and varieties_grown reflect exactly what is standing.
			gh = frappe.db.get_value(
				"Greenhouse",
				greenhouse,
				["number_of_beds", "area_planted", "number_of_plants", "varieties", "farm"],
				as_dict=True,
			)
			if gh:
				spec["total_beds"] = gh.get("number_of_beds") or 0
				spec["total_planted_area"] = gh.get("area_planted") or 0.0
				spec["number_of_plants"] = gh.get("number_of_plants") or 0
				spec["variety_count"] = gh.get("varieties") or 0
				if not spec["farm"]:
					spec["farm"] = gh.get("farm") or ""
				spec["varieties"] = frappe.get_all(
					"Greenhouse Variety",
					filters={"parent": greenhouse, "parenttype": "Greenhouse"},
					fields=["variety", "beds", "area_m2", "plants"],
					order_by="beds desc",
				)
				# Per-status breakdown of the bed ledger: what's standing,
				# what's out of the ground, what's empty.
				summary = {}
				for b in frappe.get_all(
					"Greenhouse Bed",
					filters={"parent": greenhouse, "parenttype": "Greenhouse"},
					fields=["status", "plant_count", "area_m2"],
					limit=0,
				):
					st = b.get("status") or "Empty"
					row = summary.setdefault(st, {"status": st, "beds": 0, "plants": 0, "area_m2": 0.0})
					row["beds"] = row["beds"] + 1
					row["plants"] = row["plants"] + int(b.get("plant_count") or 0)
					row["area_m2"] = row["area_m2"] + float(b.get("area_m2") or 0)
				spec["bed_summary"] = sorted(summary.values(), key=lambda r: r["beds"], reverse=True)
			active_cycles = frappe.get_all(
				"Crop Cycle", filters={"greenhouse": greenhouse, "status": ["!=", "Ended"]}, pluck="name"
			)
			if active_cycles or (gh and (gh.get("varieties") or 0) > 0):
				spec["has_cycle"] = 1
			growers = []
			try:
				glinks = frappe.get_all(
					"Employee Greenhouse", filters={"greenhouse": greenhouse}, fields=["parent"]
				)
				for gl in glinks:
					emp = gl.get("parent")
					ed = frappe.db.get_value("Employee", emp, ["employee_name", "designation"], as_dict=True)
					nm = emp
					dg = ""
					if ed:
						nm = ed.get("employee_name") or emp
						dg = ed.get("designation") or ""
					growers.append({"employee": emp, "employee_name": nm, "designation": dg})
			except Exception:
				growers = []
			spec["growers"] = growers
			frappe.response["data"] = spec
	except Exception as e:
		frappe.log_error("productionGetGreenhouseSpec error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionGetPlanForm():
	data = frappe.request.get_json() or {}
	name = (data.get("name") or "").strip()
	try:
		if not name:
			frappe.response["data"] = {"error": "name is required."}
		else:
			doc = frappe.get_doc("Production Plan Form", name)
			empnames = {}
			tasks = []
			for t in doc.tasks or []:
				emp = t.assigned_to or ""
				if emp and emp not in empnames:
					empnames[emp] = frappe.db.get_value("Employee", emp, "employee_name") or emp
				tasks.append(
					{
						"name": t.name,
						"task_name": t.task_name or "",
						"assigned_task": t.assigned_task or "",
						"day": t.day or "",
						"purpose": t.purpose or "",
						"actual": t.actual or 0,
						"achievement": t.achievement or 0,
						"supervisors_remarks": t.supervisors_remarks or "",
						"operation": t.operation or "",
						"greenhouse": t.greenhouse or "",
						"variety": t.variety or "",
						"target": t.target or 0,
						"beds": t.beds or "",
						"due_date": str(t.due_date or ""),
						"assigned_to": emp,
						"assigned_to_name": empnames.get(emp, ""),
						"status": t.status or "Open",
						"harvested": t.harvested or 0,
						"completion_note": t.completion_note or "",
					}
				)
			varieties = []
			for v in doc.varieties or []:
				varieties.append(
					{
						"name": v.name,
						"variety": v.variety or "",
						"planned_stems": v.planned_stems or 0,
						"forecast_stems": v.forecast_stems or 0,
						"budget_stems": v.budget_stems or 0,
						"notes": v.notes or "",
					}
				)
			farm = doc.farm or (
				frappe.db.get_value("Warehouse", doc.greenhouse, "custom_farm") or ""
				if doc.greenhouse
				else ""
			)
			supervisor_name = (
				frappe.db.get_value("Employee", doc.supervisor, "employee_name") or doc.supervisor
				if doc.supervisor
				else ""
			)
			frappe.response["data"] = {
				"name": doc.name,
				"docstatus": doc.docstatus or 0,
				"company": doc.company or "",
				"supervisor": doc.supervisor or "",
				"supervisor_name": supervisor_name or "",
				"greenhouse": doc.greenhouse or "",
				"farm": farm,
				"plan_year": doc.plan_year or 0,
				"plan_week": doc.plan_week or 0,
				"plan_period": doc.plan_period or "",
				"tasks": tasks,
				"varieties": varieties,
				**plan_approval_info(doc),
			}
	except Exception as e:
		frappe.log_error("productionGetPlanForm error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


def plan_approval_info(doc):
	"""Workflow state, the actions this user can take now, and the manager's review."""
	from frappe.model.workflow import get_transitions

	state = doc.get("workflow_state") or ""
	actions = []
	if state:
		try:
			for t in get_transitions(doc):
				if t.get("action") not in actions:
					actions.append(t.get("action"))
		except Exception:
			actions = []

	def user_name(user):
		return (frappe.db.get_value("User", user, "full_name") or user) if user else ""

	return {
		"workflow_state": state,
		"actions": actions,
		"can_edit_plan": doc.docstatus == 0 and can_edit_plan(doc),
		"can_enter_actuals": doc.docstatus == 0 and can_enter_actuals(doc),
		"approved_by": user_name(doc.get("approved_by")),
		"approved_on": str(doc.get("approved_on") or ""),
		"verified_by": user_name(doc.get("verified_by")),
		"verified_on": str(doc.get("verified_on") or ""),
		"quality_rating": doc.get("quality_rating") or 0,
		"manager_comments": doc.get("manager_comments") or "",
	}


@frappe.whitelist()
def productionPlanAction():
	"""Moves a plan through its approval workflow: Submit for Approval,
	Approve, Reject, Submit for Verification or Verify. A rejection needs a
	comment; the manager can rate the quality of the work (0-5 stars)."""
	from frappe.model.workflow import apply_workflow

	data = frappe.request.get_json() or {}
	name = (data.get("name") or "").strip()
	action = (data.get("action") or "").strip()
	comment = (data.get("comment") or "").strip()
	rating = data.get("rating")
	try:
		if not name or not action:
			frappe.throw(_("name and action are required."))
		if action == "Reject" and not comment:
			frappe.throw(_("Add a comment on why this plan is rejected."))
		updates = {}
		if comment:
			updates["manager_comments"] = comment
		if rating not in (None, ""):
			stars = max(0, min(5, int(rating)))
			updates["quality_rating"] = stars / 5.0
		if updates:
			frappe.db.set_value("Production Plan Form", name, updates, update_modified=False)
		doc = apply_workflow(frappe.get_doc("Production Plan Form", name), action)
		frappe.db.commit()
		doc = frappe.get_doc("Production Plan Form", name)
		frappe.response["data"] = {
			"status": "success",
			"name": name,
			"message": _("Plan {0} is now {1}.").format(name, doc.workflow_state),
			**plan_approval_info(doc),
		}
	except Exception as e:
		frappe.db.rollback()
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionHarvestReport():
	data = frappe.request.get_json() or {}
	date = (data.get("date") or "").strip()
	# A range (from_date..to_date, inclusive) or the single `date`.
	from_date = (data.get("from_date") or date).strip()
	to_date = (data.get("to_date") or from_date).strip()
	if from_date and to_date and to_date < from_date:
		from_date, to_date = to_date, from_date
	farm_filter = (data.get("farm") or "").strip()
	if not from_date:
		frappe.response["data"] = {"error": "date is required."}
	else:
		try:
			# Aggregate a given stock entry type (Harvesting / Receiving) for the
			# date into group -> variety -> stem_length totals, once each for
			# farm and greenhouse grouping. Both flows carry the same
			# harvest-detail fields (farm, custom_greenhouse, custom_stem_length),
			# so one helper serves both sections of the report.
			def build_groups(agg, group_key):
				out = []
				for group in sorted(agg.keys()):
					varieties_out = []
					group_stems = 0
					for variety in sorted(agg[group].keys()):
						sl_map = agg[group][variety]["stem_lengths"]
						hs_map = agg[group][variety]["head_sizes"]
						sl_out = []
						variety_stems = 0
						for sl in sorted(sl_map.keys()):
							sl_out.append({"stem_length": sl, "stems": sl_map[sl]})
							variety_stems = variety_stems + sl_map[sl]
						hs_out = [{"head_size": hs, "stems": hs_map[hs]} for hs in sorted(hs_map.keys())]
						varieties_out.append(
							{
								"variety": variety,
								"stems": variety_stems,
								"stem_lengths": sl_out,
								"head_sizes": hs_out,
							}
						)
						group_stems = group_stems + variety_stems
					out.append({group_key: group, "stems": group_stems, "varieties": varieties_out})
				return out

			def aggregate(entry_type, by_head_size=False):
				se_filters = {
					"stock_entry_type": entry_type,
					"docstatus": 1,
					"posting_date": ["between", [from_date, to_date]],
				}
				if farm_filter:
					se_filters["farm"] = farm_filter
				entries = frappe.get_all(
					"Stock Entry",
					filters=se_filters,
					fields=["name", "farm", "custom_greenhouse", "custom_stem_length", "custom_head_size"],
				)
				meta = {}
				for e in entries:
					meta[e.get("name")] = {
						# "farm" is the real accounting-dimension field the
						# harvest/grading/receiving flows actually set.
						# custom_farm is a legacy mirror that nothing in the
						# current mobile API populates -- filtering or
						# grouping on it silently finds nothing.
						"farm": e.get("farm") or "Unspecified",
						"greenhouse": e.get("custom_greenhouse") or "Unspecified",
						"stem_length": e.get("custom_stem_length") or "Unspecified",
						"head_size": e.get("custom_head_size") or "Unspecified",
					}
				rows = []
				if meta:
					rows = frappe.get_all(
						"Stock Entry Detail",
						filters={"parent": ["in", list(meta.keys())]},
						fields=["parent", "item_code", "qty", "custom_stem_length"],
					)
				agg_farm = {}
				agg_greenhouse = {}
				total = 0
				for r in rows:
					info = meta.get(r.get("parent")) or {}
					farm = info.get("farm") or "Unspecified"
					greenhouse = info.get("greenhouse") or "Unspecified"
					variety = r.get("item_code") or "Unspecified"
					# Prefer the child row's own length: Receiving entries
					# covering several lengths in one bucket-cycle carry the
					# real per-item length there (see createReceivingStockEntry),
					# with only the FIRST length on the parent as a legacy
					# summary. Harvesting entries never set it at child level,
					# so they fall back to the parent's.
					sl = r.get("custom_stem_length") or info.get("stem_length") or "Unspecified"
					qty = int(r.get("qty") or 0)
					total = total + qty
					# Head size is captured on the Harvesting entry only; Receiving
					# entries don't carry it, so their breakdown is left empty.
					hs = info.get("head_size") or "Unspecified"
					for agg, group in ((agg_farm, farm), (agg_greenhouse, greenhouse)):
						vbucket = agg.setdefault(group, {}).setdefault(
							variety, {"stem_lengths": {}, "head_sizes": {}}
						)
						vbucket["stem_lengths"][sl] = vbucket["stem_lengths"].get(sl, 0) + qty
						if by_head_size:
							vbucket["head_sizes"][hs] = vbucket["head_sizes"].get(hs, 0) + qty
				return {
					"total": total,
					"farms": build_groups(agg_farm, "farm"),
					"greenhouses": build_groups(agg_greenhouse, "greenhouse"),
				}

			harvested = aggregate("Harvesting", by_head_size=True)
			received = aggregate("Receiving")
			frappe.response["data"] = {
				"date": from_date,
				"from_date": from_date,
				"to_date": to_date,
				"total_stems": harvested["total"],
				"farms": harvested["farms"],
				"greenhouses": harvested["greenhouses"],
				"received_total_stems": received["total"],
				"received_farms": received["farms"],
				"received_greenhouses": received["greenhouses"],
			}
		except Exception as e:
			frappe.log_error("productionHarvestReport error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionGreenhouseVarietyBreakdown():
	# Per-variety harvested/received for one greenhouse on one date, plus
	# bucket-level "missing" detail (harvested but not yet received) for the
	# mobile dashboard's expand-to-drill-down + hold-to-find-missing-buckets
	# feature. Mirrors the variety/bucket logic already used for the
	# dashboard's unreceived-buckets rollup, scoped down to a single
	# greenhouse instead of aggregated across the whole farm.
	data = frappe.request.get_json() or {}
	greenhouse = (data.get("greenhouse") or "").strip()
	date = (data.get("date") or "").strip() or frappe.utils.today()
	if not greenhouse:
		frappe.response["data"] = {"error": "greenhouse is required."}
		return
	try:
		entries = frappe.get_all(
			"Stock Entry",
			filters={
				"custom_greenhouse": greenhouse,
				"posting_date": date,
				"docstatus": 1,
				"stock_entry_type": ["in", ["Harvesting", "Receiving"]],
			},
			fields=["name", "stock_entry_type", "custom_bucket_id", "creation"],
		)

		if not entries:
			frappe.response["data"] = {"date": date, "greenhouse": greenhouse, "varieties": []}
			return

		meta = {e.name: e for e in entries}
		rows = frappe.get_all(
			"Stock Entry Detail",
			filters={"parent": ["in", list(meta.keys())]},
			fields=["parent", "item_code", "qty"],
		)

		variety_totals = {}
		bucket_totals = {}
		for r in rows:
			info = meta.get(r.parent)
			if not info:
				continue
			variety = r.item_code or "Unspecified"
			qty = int(r.qty or 0)
			is_harvest = info.stock_entry_type == "Harvesting"

			v = variety_totals.setdefault(variety, {"variety": variety, "harvested": 0, "received": 0})
			v["harvested" if is_harvest else "received"] += qty

			bucket = (info.custom_bucket_id or "").strip().upper()
			if bucket:
				bkey = (variety, bucket)
				b = bucket_totals.setdefault(
					bkey,
					{
						"variety": variety,
						"bucket_id": bucket,
						"harvested_qty": 0,
						"received_qty": 0,
						"last_harvest_creation": None,
					},
				)
				if is_harvest:
					b["harvested_qty"] += qty
					if not b["last_harvest_creation"] or info.creation > b["last_harvest_creation"]:
						b["last_harvest_creation"] = info.creation
				else:
					b["received_qty"] += qty

		now = frappe.utils.now_datetime()
		missing_by_variety = {}
		for (variety, bucket), b in bucket_totals.items():
			variance = b["harvested_qty"] - b["received_qty"]
			if variance <= 0:
				continue
			aging_minutes = 0
			if b["last_harvest_creation"]:
				aging_minutes = int(
					(now - frappe.utils.get_datetime(b["last_harvest_creation"])).total_seconds() / 60
				)
			missing_by_variety.setdefault(variety, []).append(
				{
					"bucket_id": bucket,
					"harvested_qty": b["harvested_qty"],
					"received_qty": b["received_qty"],
					"variance": variance,
					"aging_minutes": aging_minutes,
				}
			)

		varieties_out = []
		for v in sorted(variety_totals.values(), key=lambda x: x["harvested"], reverse=True):
			missing = sorted(
				missing_by_variety.get(v["variety"], []), key=lambda b: b["aging_minutes"], reverse=True
			)
			varieties_out.append(
				{
					"variety": v["variety"],
					"harvested": v["harvested"],
					"received": v["received"],
					"variance": v["harvested"] - v["received"],
					"missing_buckets": missing,
				}
			)

		frappe.response["data"] = {"date": date, "greenhouse": greenhouse, "varieties": varieties_out}
	except Exception as e:
		frappe.log_error("productionGreenhouseVarietyBreakdown error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListCropCycles():
	data = frappe.request.get_json() or {}
	greenhouse = (data.get("greenhouse") or "").strip()
	farm = (data.get("farm") or "").strip()
	variety = (data.get("variety") or "").strip()
	limit = int(data.get("limit") or 200)
	if limit > 500:
		limit = 500
	try:
		# New model: one Crop Cycle = one variety on a set of beds. Ended
		# cycles are history and stay off the management list.
		filters = {"status": ["!=", "Ended"]}
		if greenhouse:
			filters["greenhouse"] = greenhouse
		if farm:
			filters["farm"] = farm
		if variety:
			filters["variety"] = variety
		rows = frappe.get_all(
			"Crop Cycle",
			filters=filters,
			fields=[
				"name",
				"greenhouse",
				"farm",
				"variety",
				"status",
				"planting_date",
				"planted_area",
				"qty_planted",
				"plants_standing",
				"bed_range",
			],
			order_by="modified desc",
			limit=limit,
		)
		bedcounts = {}
		names = []
		for r in rows:
			names.append(r.get("name"))
		if names:
			counted = frappe.get_all(
				"Crop Cycle Bed", filters={"parent": ["in", names]}, fields=["parent"], limit=0
			)
			for c in counted:
				p = c.get("parent")
				bedcounts[p] = bedcounts.get(p, 0) + 1
		out = []
		for r in rows:
			numbers = []
			spec = (r.get("bed_range") or "").replace("–", "-").replace("—", "-")
			for chunk in spec.split(","):
				part = chunk.strip()
				if not part:
					continue
				if "-" in part:
					bits = part.split("-")
					try:
						numbers.append(int(float(bits[0])))
						numbers.append(int(float(bits[1])))
					except Exception:
						pass
				else:
					try:
						numbers.append(int(float(part)))
					except Exception:
						pass
			out.append(
				{
					"name": r.get("name") or "",
					"greenhouse": r.get("greenhouse") or "",
					"farm": r.get("farm") or "",
					"variety": r.get("variety") or "",
					"status": r.get("status") or "",
					"planting_date": str(r.get("planting_date") or ""),
					"beds": bedcounts.get(r.get("name"), 0),
					"bays": 0,
					"varieties": 1,
					"area": r.get("planted_area") or 0.0,
					"plants": r.get("plants_standing") or r.get("qty_planted") or 0,
					"start_bed": min(numbers) if numbers else 0,
					"end_bed": max(numbers) if numbers else 0,
				}
			)
		frappe.response["data"] = {"count": len(out), "cycles": out}
	except Exception as e:
		frappe.log_error("productionListCropCycles error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListFarms():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 100)
	if limit > 500:
		limit = 500

	try:
		filters = {}
		if search:
			filters["name"] = ["like", "%" + search + "%"]
		rows = frappe.get_all(
			"Farm",
			filters=filters,
			fields=["name", "company"],
			order_by="name asc",
			limit=limit,
		)
		farms = []
		for r in rows:
			nm = r.get("name") or ""
			farms.append({"name": nm, "label": nm, "company": r.get("company") or ""})
		frappe.response["data"] = {"count": len(farms), "farms": farms}
	except Exception as e:
		frappe.log_error("productionListFarms error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListGreenhouseSpecs():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	farm = (data.get("farm") or "").strip()
	limit = int(data.get("limit") or 300)
	if limit > 1000:
		limit = 1000
	try:
		filters = {"disabled": 0, "is_group": 0, "warehouse_type": "Greenhouse"}
		if search:
			filters["warehouse_name"] = ["like", "%" + search + "%"]
		if farm:
			filters["custom_farm"] = farm
		ghs = frappe.get_all(
			"Warehouse",
			filters=filters,
			fields=["name", "warehouse_name", "custom_farm"],
			order_by="name asc",
			limit=limit,
		)
		# Rollups come from the Greenhouse doctype ledger; has_cycle from the
		# per-variety Crop Cycles that are still running.
		ledgers = frappe.get_all(
			"Greenhouse", fields=["name", "greenhouse", "farm", "number_of_beds", "area_planted", "varieties"]
		)
		ledgermap = {}
		for c in ledgers:
			key = c.get("greenhouse") or c.get("name")
			if key and key not in ledgermap:
				ledgermap[key] = c
		active = frappe.get_all("Crop Cycle", filters={"status": ["!=", "Ended"]}, fields=["greenhouse"])
		activeset = []
		for a in active:
			g = a.get("greenhouse")
			if g and g not in activeset:
				activeset.append(g)
		out = []
		for g in ghs:
			led = ledgermap.get(g.get("name"))
			item = {
				"name": g.get("name") or "",
				"label": g.get("warehouse_name") or g.get("name") or "",
				"farm": g.get("custom_farm") or "",
				"has_cycle": 0,
				"total_beds": 0,
				"total_area": 0.0,
				"variety_count": 0,
			}
			if led:
				item["total_beds"] = led.get("number_of_beds") or 0
				item["total_area"] = led.get("area_planted") or 0.0
				item["variety_count"] = led.get("varieties") or 0
				if not item["farm"]:
					item["farm"] = led.get("farm") or ""
			if g.get("name") in activeset or (led and (led.get("varieties") or 0) > 0):
				item["has_cycle"] = 1
			out.append(item)
		frappe.response["data"] = {"count": len(out), "greenhouses": out}
	except Exception as e:
		frappe.log_error("productionListGreenhouseSpecs error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListGreenhouses():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	farm = (data.get("farm") or "").strip()
	limit = int(data.get("limit") or 300)
	if limit > 1000:
		limit = 1000

	try:
		filters = {"disabled": 0, "is_group": 0, "name": ["like", "%GH%"]}
		if search:
			filters["name"] = ["like", "%" + search + "%"]
		if farm:
			filters["custom_farm"] = farm

		rows = frappe.get_all(
			"Warehouse",
			filters=filters,
			fields=["name", "warehouse_name", "custom_farm"],
			order_by="name asc",
			limit=limit,
		)

		out = []
		for r in rows:
			out.append(
				{
					"name": r.get("name") or "",
					"label": r.get("warehouse_name") or r.get("name") or "",
					"farm": r.get("custom_farm") or "",
				}
			)

		frappe.response["data"] = {"count": len(out), "greenhouses": out}
	except Exception as e:
		frappe.log_error("productionListGreenhouses error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListGrowerCandidates():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 50)
	if limit > 200:
		limit = 200
	try:
		filters = {"status": "Active", "designation": "Grower"}
		if search:
			filters["employee_name"] = ["like", "%" + search + "%"]
		rows = frappe.get_all(
			"Employee",
			filters=filters,
			fields=["name", "employee_name"],
			order_by="employee_name asc",
			limit=limit,
		)
		out = []
		for r in rows:
			out.append(
				{
					"employee": r.get("name") or "",
					"employee_name": r.get("employee_name") or r.get("name") or "",
				}
			)
		frappe.response["data"] = {"count": len(out), "candidates": out}
	except Exception as e:
		frappe.log_error("productionListGrowerCandidates error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListPlanForms():
	data = frappe.request.get_json() or {}
	greenhouse = (data.get("greenhouse") or "").strip()
	greenhouses = [g for g in (data.get("greenhouses") or []) if g]
	farm = (data.get("farm") or "").strip()
	plan_year = data.get("plan_year")
	plan_week = data.get("plan_week")
	limit = int(data.get("limit") or 200)
	if limit > 500:
		limit = 500
	try:
		filters = {"docstatus": ["<", 2]}
		workflow_states = [w for w in (data.get("workflow_states") or []) if w]
		has_workflow = frappe.get_meta("Production Plan Form").has_field("workflow_state")
		if workflow_states and has_workflow:
			filters["workflow_state"] = ["in", workflow_states]
		if greenhouse:
			greenhouses.append(greenhouse)
		if greenhouses:
			filters["greenhouse"] = ["in", greenhouses]
		or_filters = None
		if farm:
			# Older plans have no farm of their own; match them by greenhouse.
			farm_houses = frappe.get_all("Warehouse", filters={"custom_farm": farm}, pluck="name")
			or_filters = {"farm": farm, "greenhouse": ["in", farm_houses or [""]]}
		if plan_year:
			filters["plan_year"] = int(plan_year)
		if plan_week:
			filters["plan_week"] = int(plan_week)
		rows = frappe.get_all(
			"Production Plan Form",
			filters=filters,
			or_filters=or_filters,
			fields=[
				"name",
				"docstatus",
				"greenhouse",
				"farm",
				"company",
				"supervisor",
				"plan_year",
				"plan_week",
				"plan_period",
			]
			+ (["workflow_state"] if has_workflow else []),
			order_by="plan_year desc, plan_week desc, modified desc",
			limit=limit,
		)
		names = []
		for r in rows:
			names.append(r.get("name"))
		taskstats = {}
		stems = {}
		if names:
			for t in frappe.get_all(
				"Production Plan Task",
				filters={"parent": ["in", names]},
				fields=["parent", "status"],
				limit=0,
			):
				p = t.get("parent")
				st = taskstats.setdefault(p, {"total": 0, "open": 0, "done": 0})
				st["total"] = st["total"] + 1
				if (t.get("status") or "Open") in ["Open", "In Progress"]:
					st["open"] = st["open"] + 1
				if t.get("status") == "Done":
					st["done"] = st["done"] + 1
			for v in frappe.get_all(
				"Production Plan Variety",
				filters={"parent": ["in", names]},
				fields=["parent", "planned_stems"],
				limit=0,
			):
				p = v.get("parent")
				stems[p] = stems.get(p, 0) + int(v.get("planned_stems") or 0)
		farms = {}
		out = []
		for r in rows:
			gh = r.get("greenhouse") or ""
			if gh and gh not in farms:
				farms[gh] = frappe.db.get_value("Warehouse", gh, "custom_farm") or ""
			st = taskstats.get(r.get("name"), {"total": 0, "open": 0, "done": 0})
			out.append(
				{
					"name": r.get("name") or "",
					"greenhouse": gh,
					"farm": r.get("farm") or farms.get(gh, ""),
					"company": r.get("company") or "",
					"supervisor": r.get("supervisor") or "",
					"docstatus": r.get("docstatus") or 0,
					"plan_year": r.get("plan_year") or 0,
					"plan_week": r.get("plan_week") or 0,
					"plan_period": r.get("plan_period") or "",
					"tasks_total": st["total"],
					"tasks_open": st["open"],
					"tasks_done": st["done"],
					"planned_stems": stems.get(r.get("name"), 0),
					"workflow_state": r.get("workflow_state") or "",
				}
			)
		frappe.response["data"] = {"count": len(out), "plans": out}
	except Exception as e:
		frappe.log_error("productionListPlanForms error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListPropagationBatches():
	data = frappe.request.get_json() or {}
	stage = (data.get("stage") or "").strip()
	variety = (data.get("variety") or "").strip()
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 100)
	if limit > 500:
		limit = 500

	try:
		filters = {}
		if stage:
			filters["current_stage"] = stage
		if variety:
			filters["variety"] = variety
		if search:
			filters["name"] = ["like", "%" + search + "%"]

		rows = frappe.get_all(
			"Propagation Batch",
			filters=filters,
			fields=[
				"name",
				"variety",
				"variety_name",
				"propagation_method",
				"current_stage",
				"iso_week",
				"iso_year",
				"start_date",
				"total_cuttings_planned",
				"total_cuttings_actual",
				"total_mortality",
				"total_survived",
				"available_to_dispatch",
				"total_dispatched",
				"success_rate",
				"propagation_unit",
				"company",
			],
			order_by="creation desc",
			limit=limit,
		)

		batches = []
		for r in rows:
			batches.append(
				{
					"name": r.get("name") or "",
					"variety": r.get("variety") or "",
					"variety_name": r.get("variety_name") or "",
					"propagation_method": r.get("propagation_method") or "",
					"current_stage": r.get("current_stage") or "",
					"iso_week": r.get("iso_week") or 0,
					"iso_year": r.get("iso_year") or 0,
					"start_date": str(r.get("start_date") or ""),
					"total_cuttings_planned": int(r.get("total_cuttings_planned") or 0),
					"total_cuttings_actual": int(r.get("total_cuttings_actual") or 0),
					"total_mortality": int(r.get("total_mortality") or 0),
					"total_survived": int(r.get("total_survived") or 0),
					"available_to_dispatch": int(r.get("available_to_dispatch") or 0),
					"total_dispatched": int(r.get("total_dispatched") or 0),
					"success_rate": float(r.get("success_rate") or 0),
					"propagation_unit": r.get("propagation_unit") or "",
					"company": r.get("company") or "",
				}
			)

		frappe.response["data"] = {"count": len(batches), "batches": batches}
	except Exception as e:
		frappe.log_error("productionListPropagationBatches error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListRejectionReasons():
	data = frappe.request.get_json() or {}
	limit = int(data.get("limit") or 100)
	if limit > 500:
		limit = 500

	try:
		rows = frappe.get_all(
			"QC Parameters",
			fields=["name", "parameter"],
			order_by="parameter asc",
			limit=limit,
		)
		reasons = []
		for r in rows:
			nm = r.get("name") or ""
			reasons.append({"name": nm, "label": r.get("parameter") or nm})
		frappe.response["data"] = {"count": len(reasons), "reasons": reasons}
	except Exception as e:
		frappe.log_error("productionListRejectionReasons error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListFieldRejects():
	"""Submitted Field Rejects entries, newest first, one row per item."""
	data = frappe.request.get_json() or {}
	farm = (data.get("farm") or "").strip()
	greenhouse = (data.get("greenhouse") or "").strip()
	start = max(frappe.utils.cint(data.get("start")), 0)
	page_length = min(max(frappe.utils.cint(data.get("page_length")) or 30, 1), 200)

	conds = ["se.docstatus = 1", "se.stock_entry_type = 'Field Rejects'"]
	values = {"start": start, "page_length": page_length}
	if farm:
		conds.append("COALESCE(NULLIF(se.farm, ''), w.custom_farm) = %(farm)s")
		values["farm"] = farm
	if greenhouse:
		conds.append("sed.s_warehouse = %(greenhouse)s")
		values["greenhouse"] = greenhouse
	where = " AND ".join(conds)

	try:
		total = frappe.db.sql(
			"""
            SELECT COUNT(*), COALESCE(SUM(sed.qty), 0)
            FROM `tabStock Entry` se
            JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
            LEFT JOIN `tabWarehouse` w ON w.name = sed.s_warehouse
            WHERE """
			+ where,
			values,
		)[0]
		rows = frappe.db.sql(
			"""
            SELECT se.name, se.posting_date, se.posting_time,
                   COALESCE(NULLIF(se.farm, ''), w.custom_farm) AS farm,
                   sed.s_warehouse AS greenhouse, sed.item_code AS variety, sed.qty,
                   sed.custom_rejection_reason AS reason, u.full_name AS recorded_by
            FROM `tabStock Entry` se
            JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
            LEFT JOIN `tabWarehouse` w ON w.name = sed.s_warehouse
            LEFT JOIN `tabUser` u ON u.name = se.owner
            WHERE """
			+ where
			+ """
            ORDER BY se.posting_date DESC, se.posting_time DESC, se.name DESC
            LIMIT %(start)s, %(page_length)s
        """,
			values,
			as_dict=True,
		)
		entries = []
		for r in rows:
			entries.append(
				{
					"name": r.name,
					"date": str(r.posting_date or ""),
					"time": frappe.utils.get_time(r.posting_time).strftime("%H:%M") if r.posting_time else "",
					"farm": r.farm or "",
					"greenhouse": r.greenhouse or "",
					"variety": r.variety or "",
					"stems": frappe.utils.flt(r.qty),
					"reason": r.reason or "",
					"recorded_by": r.recorded_by or "",
				}
			)
		frappe.response["data"] = {
			"total": frappe.utils.cint(total[0]),
			"total_stems": frappe.utils.flt(total[1]),
			"entries": entries,
		}
	except Exception as e:
		frappe.log_error("productionListFieldRejects error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListSeedlingRequests():
	data = frappe.request.get_json() or {}
	status = (data.get("status") or "").strip()
	limit = int(data.get("limit") or 100)
	if limit > 500:
		limit = 500

	try:
		filters = {}
		if status:
			filters["status"] = status

		rows = frappe.get_all(
			"Seedling Request",
			filters=filters,
			fields=[
				"name",
				"status",
				"request_date",
				"required_by_date",
				"priority",
				"farm",
				"target_farm_block",
				"total_qty_requested",
				"total_qty_dispatched",
				"fulfilment_percentage",
				"days_to_fulfil",
				"reason",
				"requested_by",
			],
			order_by="creation desc",
			limit=limit,
		)

		requests = []
		for r in rows:
			requests.append(
				{
					"name": r.get("name") or "",
					"status": r.get("status") or "",
					"request_date": str(r.get("request_date") or ""),
					"required_by_date": str(r.get("required_by_date") or ""),
					"priority": r.get("priority") or "",
					"farm": r.get("farm") or "",
					"target_farm_block": r.get("target_farm_block") or "",
					"total_qty_requested": int(r.get("total_qty_requested") or 0),
					"total_qty_dispatched": int(r.get("total_qty_dispatched") or 0),
					"fulfilment_percentage": float(r.get("fulfilment_percentage") or 0),
					"days_to_fulfil": int(r.get("days_to_fulfil") or 0),
					"reason": r.get("reason") or "",
					"requested_by": r.get("requested_by") or "",
				}
			)

		frappe.response["data"] = {"count": len(requests), "requests": requests}
	except Exception as e:
		frappe.log_error("productionListSeedlingRequests error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListVarieties():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 200)
	if limit > 500:
		limit = 500
	try:
		root_groups = frappe.get_all(
			"Harvest Item Group Config",
			filters={"parent": "Production Settings", "parenttype": "Production Settings"},
			pluck="item_group",
		)
		# Same mistake item_group_is_under() exists to avoid: real items never
		# sit directly in a bare parent group like "Standard Roses" or "Spray
		# Roses" -- they're in leaf groups underneath it ("Standard Roses -
		# Intermediate", etc). A flat item_group IN (root_groups) filter here
		# matched almost nothing, so the crop-cycle "New" screen's variety
		# picker showed next to no varieties. Resolve each root to its actual
		# descendant groups (nested-set lft/rgt) before filtering Items.
		groups = []
		for root in root_groups:
			bounds = frappe.db.get_value("Item Group", root, ["lft", "rgt"], as_dict=True)
			if not bounds:
				continue
			descendants = frappe.get_all(
				"Item Group", filters={"lft": [">=", bounds.lft], "rgt": ["<=", bounds.rgt]}, pluck="name"
			)
			groups.extend(descendants)
		filters = {"disabled": 0}
		if groups:
			filters["item_group"] = ["in", groups]
		if search:
			filters["item_name"] = ["like", "%" + search + "%"]
		rows = frappe.get_all(
			"Item",
			filters=filters,
			fields=["name", "item_name", "item_group"],
			order_by="item_name asc",
			limit=limit,
		)
		out = []
		for r in rows:
			out.append(
				{
					"name": r.get("name") or "",
					"label": r.get("item_name") or r.get("name") or "",
					"item_group": r.get("item_group") or "",
				}
			)
		frappe.response["data"] = {"count": len(out), "varieties": out}
	except Exception as e:
		frappe.log_error("productionListVarieties error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionRecordPropagationLog():
	data = frappe.request.get_json() or {}
	batch = (data.get("propagation_batch") or "").strip()
	log_date = (data.get("log_date") or "").strip()
	activity_type = (data.get("activity_type") or "").strip()
	qty_done = data.get("qty_done_today")
	mortality = data.get("mortality_today")
	surviving = data.get("surviving_qty")
	weather = (data.get("weather_conditions") or "").strip()
	observations = (data.get("observations") or "").strip()

	if not batch:
		frappe.response["data"] = {"error": "propagation_batch is required."}
	elif not log_date:
		frappe.response["data"] = {"error": "log_date is required."}
	elif not activity_type:
		frappe.response["data"] = {"error": "activity_type is required."}
	elif qty_done is None:
		frappe.response["data"] = {"error": "qty_done_today is required."}
	else:
		try:
			# Read the batch to enrich the log
			b = frappe.get_doc("Propagation Batch", batch)
			doc = frappe.new_doc("Daily Propagation Log")
			doc.log_date = log_date
			doc.propagation_batch = batch
			doc.activity_type = activity_type
			doc.qty_done_today = int(qty_done)
			if mortality is not None:
				doc.mortality_today = int(mortality)
			if surviving is not None:
				doc.surviving_qty = int(surviving)
			if weather:
				doc.weather_conditions = weather
			if observations:
				doc.observations = observations
			doc.logged_by = frappe.session.user
			# Propagate variety + greenhouse from the batch for downstream reports
			if b.get("variety"):
				doc.rose_variety = b.get("variety")
			if b.get("propagation_unit"):
				doc.greenhouse = b.get("propagation_unit")
			doc.insert(ignore_permissions=True)
			frappe.db.commit()

			frappe.response["data"] = {
				"status": "success",
				"name": doc.name,
				"message": "Logged " + activity_type + " for batch " + batch + ".",
			}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionRecordPropagationLog error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionRecordReplant():
	data = frappe.request.get_json() or {}
	crop_cycle = (data.get("crop_cycle") or "").strip()
	replanting_date = (data.get("replanting_date") or "").strip()
	from_bed = data.get("from_bed")
	to_bed = data.get("to_bed")
	new_variety = (data.get("new_variety") or "").strip()
	qty_uprooted = data.get("qty_uprooted")
	qty_replanted = data.get("qty_replanted")
	cost_per_plant = data.get("cost_per_plant")
	propagation_batch = (data.get("propagation_batch") or "").strip()
	reason = (data.get("reason") or "").strip()
	remarks = (data.get("remarks") or "").strip()

	if not crop_cycle:
		frappe.response["data"] = {"error": "crop_cycle is required."}
	elif not replanting_date:
		frappe.response["data"] = {"error": "replanting_date is required."}
	elif from_bed is None or to_bed is None:
		frappe.response["data"] = {"error": "from_bed and to_bed are required."}
	elif not new_variety:
		frappe.response["data"] = {"error": "new_variety is required."}
	elif qty_uprooted is None or qty_replanted is None:
		frappe.response["data"] = {"error": "qty_uprooted and qty_replanted are required."}
	elif not reason:
		frappe.response["data"] = {"error": "reason is required."}
	else:
		try:
			cycle = frappe.get_doc("Crop Cycle", crop_cycle)
			house = cycle.greenhouse
			if not frappe.db.exists("Greenhouse", house):
				frappe.response["data"] = {
					"error": "No greenhouse ledger exists for "
					+ house
					+ ". Save the crop cycle once to create it."
				}
			else:
				gh = frappe.get_doc("Greenhouse", house)
				lo = int(from_bed)
				hi = int(to_bed)
				if lo > hi:
					lo, hi = hi, lo

				# Uproot first (same date sorts before the replant), but only if
				# something is actually still standing on those beds.
				standing = 0
				for b in gh.individual_beds or []:
					if b.bed_number and lo <= int(b.bed_number) <= hi:
						if b.status in ["Planted", "Producing", "Harvesting", "Transplanted"]:
							standing = standing + int(b.plant_count or 0)
				if standing > 0:
					reasonmap = {"End of Life": "Age (End of Life)", "Soil Issue": "Other"}
					ghreason = reasonmap.get(reason, reason)
					if ghreason not in [
						"Low Yield",
						"Disease",
						"Variety Change",
						"Age (End of Life)",
						"Storm Damage",
						"Other",
					]:
						ghreason = "Other"
					qty_out = int(qty_uprooted)
					if qty_out > standing:
						qty_out = standing
					urow = gh.append("uprooting_logs", {})
					urow.uproot_date = replanting_date
					urow.from_bed = lo
					urow.to_bed = hi
					urow.qty_uprooted = qty_out
					urow.reason = ghreason

				rrow = gh.append("replanting_logs", {})
				rrow.replant_date = replanting_date
				rrow.from_bed = lo
				rrow.to_bed = hi
				rrow.new_variety = new_variety
				rrow.qty_replanted = int(qty_replanted)
				if cost_per_plant is not None:
					rrow.cost_of_replanting = float(cost_per_plant) * int(qty_replanted)
				if propagation_batch:
					rrow.propagation_batch = propagation_batch
				note = reason
				if remarks:
					note = note + ": " + remarks
				rrow.remarks = note

				# Saving replays the logs and the reverse sync creates the NEW
				# Crop Cycle for the replanted beds automatically.
				gh.save(ignore_permissions=True)
				frappe.db.commit()

				newcycles = frappe.get_all(
					"Crop Cycle",
					filters={"greenhouse": house, "variety": new_variety, "status": ["!=", "Ended"]},
					order_by="creation desc",
					limit=1,
					pluck="name",
				)
				newname = newcycles[0] if newcycles else crop_cycle
				frappe.response["data"] = {
					"status": "success",
					"name": newname,
					"message": "Replant recorded on beds "
					+ str(lo)
					+ "-"
					+ str(hi)
					+ ". New cycle: "
					+ newname
					+ ".",
				}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionRecordReplant error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionRecordUproot():
	data = frappe.request.get_json() or {}
	crop_cycle = (data.get("crop_cycle") or "").strip()
	uproot_date = (data.get("uproot_date") or "").strip()
	from_bed = data.get("from_bed")
	to_bed = data.get("to_bed")
	qty_uprooted = data.get("qty_uprooted")
	reason = (data.get("reason") or "").strip()
	notes = (data.get("notes") or "").strip()

	if not crop_cycle:
		frappe.response["data"] = {"error": "crop_cycle is required."}
	elif not uproot_date:
		frappe.response["data"] = {"error": "uproot_date is required."}
	elif from_bed is None or to_bed is None:
		frappe.response["data"] = {"error": "from_bed and to_bed are required."}
	elif qty_uprooted is None:
		frappe.response["data"] = {"error": "qty_uprooted is required."}
	elif not reason:
		frappe.response["data"] = {"error": "reason is required."}
	else:
		try:
			cycle = frappe.get_doc("Crop Cycle", crop_cycle)
			house = cycle.greenhouse
			# App reasons -> Greenhouse Uprooting Log select options.
			reasonmap = {"End of Life": "Age (End of Life)", "Soil Issue": "Other"}
			ghreason = reasonmap.get(reason, reason)
			if ghreason not in [
				"Low Yield",
				"Disease",
				"Variety Change",
				"Age (End of Life)",
				"Storm Damage",
				"Other",
			]:
				ghreason = "Other"

			if frappe.db.exists("Greenhouse", house):
				# Log on the Greenhouse: its reverse sync writes the uproot back
				# onto the owning Crop Cycle, updates the bed ledger and the Bed
				# master statuses, and ends the cycle if nothing is left.
				gh = frappe.get_doc("Greenhouse", house)
				row = gh.append("uprooting_logs", {})
				row.uproot_date = uproot_date
				row.from_bed = int(from_bed)
				row.to_bed = int(to_bed)
				row.qty_uprooted = int(qty_uprooted)
				row.reason = ghreason
				if notes:
					row.remarks = notes
				gh.save(ignore_permissions=True)
			else:
				bedspec = str(int(from_bed))
				if int(to_bed) != int(from_bed):
					bedspec = str(int(from_bed)) + "-" + str(int(to_bed))
				row = cycle.append("uproot_log", {})
				row.uproot_date = uproot_date
				row.bed_range = bedspec
				row.plants = int(qty_uprooted)
				cycle.save(ignore_permissions=True)
			frappe.db.commit()

			cyclestatus = frappe.db.get_value("Crop Cycle", crop_cycle, "status")
			msg = "Uproot recorded for cycle " + crop_cycle + "."
			if cyclestatus == "Ended":
				msg = msg + " Nothing left standing - the cycle is now Ended."
			frappe.response["data"] = {"status": "success", "name": crop_cycle, "message": msg}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionRecordUproot error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionSavePlanForm():
	data = frappe.request.get_json() or {}
	name = (data.get("name") or "").strip()
	greenhouse = (data.get("greenhouse") or "").strip()
	plan_year = data.get("plan_year")
	plan_week = data.get("plan_week")
	company = (data.get("company") or "").strip()
	farm = (data.get("farm") or "").strip()
	supervisor = (data.get("supervisor") or "").strip()
	tasks = data.get("tasks") or []
	varieties = data.get("varieties") or []

	try:
		if name:
			doc = frappe.get_doc("Production Plan Form", name)
			created = 0
		else:
			if not greenhouse:
				frappe.response["data"] = {"error": "greenhouse is required."}
				greenhouse = None
			elif not plan_year or not plan_week:
				frappe.response["data"] = {"error": "plan_year and plan_week are required."}
				greenhouse = None
			if greenhouse:
				existing = frappe.get_all(
					"Production Plan Form",
					filters={
						"greenhouse": greenhouse,
						"plan_year": int(plan_year),
						"plan_week": int(plan_week),
						"docstatus": ["<", 2],
					},
					limit=1,
					pluck="name",
				)
				if existing:
					doc = frappe.get_doc("Production Plan Form", existing[0])
					created = 0
				else:
					doc = frappe.new_doc("Production Plan Form")
					doc.greenhouse = greenhouse
					doc.plan_year = int(plan_year)
					doc.plan_week = int(plan_week)
					doc.plan_period = "W" + str(int(plan_week)) + " " + str(int(plan_year))
					doc.company = company or frappe.db.get_single_value("Global Defaults", "default_company")
					created = 1
		if greenhouse is not None or name:
			ensure_plan_editable(doc)
			if not doc.is_new() and not can_edit_plan(doc):
				frappe.throw(
					_("Plan {0} is {1}. Tasks can only change before it is approved.").format(
						doc.name, doc.workflow_state
					)
				)
			if company:
				doc.company = company
			if farm:
				doc.farm = farm
			if supervisor:
				doc.supervisor = supervisor
			# Rows with a 'name' update that row; rows without append. Nothing
			# is ever deleted from here — removing planned work is a desk job.
			taskmap = {}
			for t in doc.tasks or []:
				taskmap[t.name] = t
			for entry in tasks:
				row = taskmap.get(entry.get("name"))
				if not row:
					row = doc.append("tasks", {})
				if entry.get("day") is None and entry.get("day_of_week") is not None:
					entry["day"] = entry.get("day_of_week")
				for f in [
					"task_name",
					"assigned_task",
					"day",
					"purpose",
					"supervisors_remarks",
					"operation",
					"variety",
					"beds",
					"due_date",
					"assigned_to",
					"status",
					"completion_note",
				]:
					if entry.get(f) is not None:
						row.set(f, entry.get(f))
				if entry.get("target") is not None:
					row.target = int(entry.get("target") or 0)
				if entry.get("actual") is not None:
					row.actual = int(entry.get("actual") or 0)
				if not row.greenhouse:
					row.greenhouse = doc.greenhouse
				if not row.status:
					row.status = "Open"
			varmap = {}
			for v in doc.varieties or []:
				varmap[v.name] = v
				varmap[v.variety] = v
			for entry in varieties:
				row = varmap.get(entry.get("name")) or varmap.get(entry.get("variety"))
				if not row:
					row = doc.append("varieties", {})
					row.variety = entry.get("variety")
				if entry.get("planned_stems") is not None:
					row.planned_stems = int(entry.get("planned_stems") or 0)
				if entry.get("notes") is not None:
					row.notes = entry.get("notes")
			doc.save(ignore_permissions=True)
			frappe.db.commit()
			todos = len(
				frappe.get_all(
					"ToDo",
					filters={"reference_type": "Production Plan Form", "reference_name": doc.name},
					pluck="name",
				)
			)
			verb = "created" if created else "updated"
			frappe.response["data"] = {
				"status": "success",
				"name": doc.name,
				"message": "Plan "
				+ doc.name
				+ " "
				+ verb
				+ " with "
				+ str(len(doc.tasks or []))
				+ " task(s).",
				"tasks": len(doc.tasks or []),
				"todos": todos,
			}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error("productionSavePlanForm error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionSetGreenhouseGrower():
	data = frappe.request.get_json() or {}
	employee = (data.get("employee") or "").strip()
	greenhouse = (data.get("greenhouse") or "").strip()
	action = (data.get("action") or "").strip()
	user = frappe.session.user
	is_admin = user == "Administrator"
	role_rows = frappe.get_all(
		"Has Role", filters={"parent": user, "role": "Production Section Head"}, fields=["name"], limit=1
	)
	try:
		if not (role_rows or is_admin):
			frappe.response["data"] = {"error": "Role 'Production Section Head' required to manage growers."}
		elif not employee or not greenhouse:
			frappe.response["data"] = {"error": "employee and greenhouse are required."}
		elif action not in ("add", "remove"):
			frappe.response["data"] = {"error": "action must be 'add' or 'remove'."}
		elif not frappe.db.exists("Employee", employee):
			frappe.response["data"] = {"error": "Employee not found: " + employee}
		elif not frappe.db.exists("Warehouse", greenhouse):
			frappe.response["data"] = {"error": "Greenhouse not found: " + greenhouse}
		else:
			existing = frappe.get_all(
				"Employee Greenhouse",
				filters={"parent": employee, "parenttype": "Employee", "greenhouse": greenhouse},
				fields=["name"],
				limit=1,
			)
			if action == "add":
				if existing:
					frappe.response["data"] = {
						"status": "exists",
						"message": "Grower already linked to this greenhouse.",
					}
				else:
					row = frappe.get_doc(
						{
							"doctype": "Employee Greenhouse",
							"parenttype": "Employee",
							"parentfield": "custom_greenhouses",
							"parent": employee,
							"greenhouse": greenhouse,
						}
					)
					row.insert(ignore_permissions=True)
					frappe.db.commit()
					frappe.response["data"] = {"status": "added"}
			else:
				if existing:
					frappe.delete_doc("Employee Greenhouse", existing[0].get("name"), ignore_permissions=True)
					frappe.db.commit()
					frappe.response["data"] = {"status": "removed"}
				else:
					frappe.response["data"] = {
						"status": "missing",
						"message": "Grower was not linked to this greenhouse.",
					}
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error("productionSetGreenhouseGrower error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionUpdatePlanTask():
	data = frappe.request.get_json() or {}
	plan = (data.get("plan") or "").strip()
	task = (data.get("task") or "").strip()
	status = (data.get("status") or "").strip()
	completion_note = (data.get("completion_note") or "").strip()
	supervisors_remarks = data.get("supervisors_remarks")
	actual = data.get("actual")

	if not plan:
		frappe.response["data"] = {"error": "plan is required."}
	elif not task:
		frappe.response["data"] = {"error": "task is required."}
	elif status not in ["Open", "In Progress", "Done", "Skipped"]:
		frappe.response["data"] = {"error": "status must be one of Open, In Progress, Done, Skipped."}
	else:
		try:
			doc = frappe.get_doc("Production Plan Form", plan)
			row = None
			for t in doc.tasks or []:
				if t.name == task:
					row = t
			if not row:
				frappe.response["data"] = {"error": "Task " + task + " not found on " + plan + "."}
			else:
				ensure_plan_editable(doc)
				if not can_enter_actuals(doc):
					frappe.throw(
						_("Plan {0} is {1}. Actuals can be keyed in once it is approved.").format(
							plan, doc.workflow_state
						)
					)
				row.status = status
				now = frappe.utils.now()
				if status == "In Progress" and not row.started_at:
					row.started_at = now
				if status in ["Done", "Skipped"]:
					row.completed_at = now
				if status == "Open":
					row.completed_at = None
				if completion_note:
					row.completion_note = completion_note
				if supervisors_remarks is not None:
					row.supervisors_remarks = supervisors_remarks
				if actual is not None and actual != "":
					row.actual = int(actual or 0)
				# Finishing tasks never submits the plan: actuals go to the
				# manager through "Submit for Verification". Older app builds
				# still send submit=true with "Mark done"; it is ignored.
				doc.save(ignore_permissions=True)
				frappe.db.commit()
				frappe.response["data"] = {
					"status": "success",
					"name": plan,
					"task": task,
					"docstatus": doc.docstatus,
					"message": (row.task_name or "Task") + " marked " + status + ".",
				}
		except Exception as e:
			frappe.db.rollback()
			frappe.log_error("productionUpdatePlanTask error: " + str(e))
			frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def reportDeviceTelemetry():
	# Server Script (API), api_method = reportDeviceTelemetry
	# Inserts one Device Telemetry record from the posted payload. Fail-safe, no submit.
	# Payload: { "data": { device_id, model, os, app_version, ... , captured_at, is_connected } }
	frappe.response["message"] = {"status": "error", "message": "Script failed"}
	try:
		data = frappe.request.get_json()
		if isinstance(data, dict) and "data" in data:
			data = data.get("data")
		data = data or {}

		fields = [
			"device_id",
			"device_name",
			"model",
			"brand",
			"os",
			"os_version",
			"app_name",
			"app_version",
			"build",
			"ota_update_id",
			"ota_channel",
			"battery_level",
			"battery_state",
			"network_type",
			"cellular_generation",
			"storage_free",
			"storage_total",
			"user",
			"user_full_name",
			"captured_at",
		]
		doc = {"doctype": "Device Telemetry"}
		i = 0
		while i < len(fields):
			f = fields[i]
			doc[f] = data.get(f)
			i = i + 1
		doc["is_connected"] = 1 if data.get("is_connected") else 0
		# Device sends ISO-8601 with a 'Z'/'T' which MySQL Datetime rejects — normalise
		# to 'YYYY-MM-DD HH:MM:SS.ffffff'.
		ts = data.get("captured_at")
		doc["captured_at"] = str(ts).replace("T", " ").replace("Z", "")[:26] if ts else None
		doc["raw_json"] = json.dumps(data)

		d = frappe.get_doc(doc)
		d.insert(ignore_permissions=True)
		frappe.db.commit()
		frappe.response["message"] = {"status": "success", "name": d.name}
	except Exception as e:
		frappe.response["message"] = {"status": "error", "message": str(e)}


@frappe.whitelist()
def submitFieldRejects():
	variety = frappe.form_dict.get("variety")
	no_of_stems = frappe.form_dict.get("no_of_stems")
	rejection_reason = frappe.form_dict.get("rejection_reason")
	farm = frappe.form_dict.get("farm")
	greenhouse = frappe.form_dict.get("greenhouse")

	if not variety or not no_of_stems or not rejection_reason or not farm or not greenhouse:
		frappe.throw("Missing required fields: variety, no_of_stems, rejection_reason, farm, greenhouse")

	qty = frappe.utils.cint(no_of_stems)
	if qty <= 0:
		frappe.throw("Number of stems must be greater than 0")

	cost_center = frappe.db.get_value("Warehouse", greenhouse, "custom_cost_center") or frappe.db.get_value(
		"Company", "Karen Roses", "cost_center"
	)
	if not cost_center:
		frappe.throw(_("Set a Cost Center on warehouse {0}.").format(greenhouse))

	doc = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Field Rejects",
			"posting_date": frappe.utils.today(),
			"posting_time": frappe.utils.nowtime(),
			"company": "Karen Roses",
			"farm": farm,
			"items": [
				{
					"s_warehouse": greenhouse,
					"t_warehouse": "Rejects - KR",
					"farm": farm,
					"item_code": variety,
					"qty": qty,
					"custom_rejection_reason": rejection_reason,
					"cost_center": cost_center,
				}
			],
		}
	)

	doc.insert()
	doc.submit()

	frappe.response["message"] = {
		"status": "success",
		"name": doc.name,
		"message": "Field rejection recorded successfully",
	}


@frappe.whitelist()
def getWeekForecast():
	"""One page of greenhouse x variety rows for a single week: budget, actual,
	revised and note. Rows that need a comment (last week, off budget by 10% or
	more) come first; a page's missing Active forecasts are created on the way out."""
	data = frappe.request.get_json() or {}
	greenhouses = [g for g in (data.get("greenhouses") or []) if g]
	year = frappe.utils.cint(data.get("year"))
	week = frappe.utils.cint(data.get("week"))
	current_week = frappe.utils.cint(data.get("current_week"))
	search = (data.get("search") or "").strip().lower()
	off_budget = bool(data.get("off_budget"))
	start = max(frappe.utils.cint(data.get("start")), 0)
	page_length = min(max(frappe.utils.cint(data.get("page_length")) or 50, 1), 200)

	if not year or not week:
		frappe.throw(_("Missing required fields: year, week"))

	empty = {"status": "success", "total": 0, "start": start, "filled": 0, "rows": []}
	if not greenhouses:
		frappe.response["message"] = empty
		return

	pairs = []
	seen = set()
	for r in frappe.get_all(
		"Greenhouse Variety",
		filters={"parent": ["in", greenhouses], "parenttype": "Greenhouse", "parentfield": "varieties_grown"},
		fields=["parent", "variety"],
		order_by="area_m2 desc",
	):
		key = (r.parent, r.variety)
		if r.variety and key not in seen:
			seen.add(key)
			pairs.append(key)
	order = {g: i for i, g in enumerate(greenhouses)}
	pairs.sort(key=lambda p: order.get(p[0], len(order)))
	if search:
		pairs = [p for p in pairs if search in p[0].lower() or search in p[1].lower()]
	if not pairs:
		frappe.response["message"] = empty
		return

	gh_list = sorted({p[0] for p in pairs})
	var_list = sorted({p[1] for p in pairs})

	budget = {}
	for r in frappe.db.sql(
		"""
        SELECT pp.greenhouse, pp.variety, pw.projected_stems
        FROM `tabProduction Projection` pp
        JOIN `tabProjection Week` pw ON pw.parent = pp.name
        WHERE pp.projection_year = %(year)s AND pw.week = %(week)s
          AND pp.greenhouse IN %(ghs)s AND pp.variety IN %(vars)s
    """,
		{"year": year, "week": week, "ghs": gh_list, "vars": var_list},
		as_dict=True,
	):
		budget[(r.greenhouse, r.variety)] = frappe.utils.cint(r.projected_stems)

	forecasts = {}
	for r in frappe.get_all(
		"Production Forecast",
		filters={
			"greenhouse": ["in", gh_list],
			"variety": ["in", var_list],
			"forecast_year": year,
			"status": "Active",
		},
		fields=["name", "greenhouse", "variety"],
		order_by="revision asc",
	):
		forecasts[(r.greenhouse, r.variety)] = r.name

	fcrows = {}
	if forecasts:
		for r in frappe.get_all(
			"Production Forecast Week",
			filters={"parent": ["in", list(forecasts.values())], "grade": "all", "week_number": week},
			fields=[
				"parent",
				"name",
				"budget_stems",
				"manual_budget_stems",
				"revised_forecast_stems",
				"actual_stems",
				"note",
			],
		):
			fcrows[r.parent] = r

	monday = datetime.date.fromisocalendar(year, week, 1)
	actual = {}
	for r in frappe.db.sql(
		"""
        SELECT se.custom_greenhouse, sed.t_warehouse, sed.item_code, SUM(sed.qty) AS stems
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.stock_entry_type = 'Harvesting'
          AND (se.custom_greenhouse IN %(ghs)s OR sed.t_warehouse IN %(ghs)s)
          AND sed.item_code IN %(vars)s
          AND se.posting_date BETWEEN %(monday)s AND %(sunday)s
        GROUP BY se.custom_greenhouse, sed.t_warehouse, sed.item_code
    """,
		{"monday": monday, "sunday": monday + datetime.timedelta(days=6), "ghs": gh_list, "vars": var_list},
		as_dict=True,
	):
		for gh in {r.custom_greenhouse, r.t_warehouse}:
			if gh:
				key = (gh, r.item_code)
				actual[key] = actual.get(key, 0) + frappe.utils.flt(r.stems)

	startweek, lastweek = _forecast_window(year)
	editable = forecast_week_editable(year, week)
	comment_week = week == current_week - 1
	rows = []
	for key in pairs:
		fcname = forecasts.get(key)
		fr = fcrows.get(fcname) if fcname else None
		if key not in budget and not fr and (fcname or not startweek <= week <= lastweek):
			continue
		projected = (fr and frappe.utils.cint(fr.manual_budget_stems)) or budget.get(
			key, frappe.utils.cint(fr.budget_stems) if fr else 0
		)
		harvested = int(actual[key]) if key in actual else (frappe.utils.cint(fr.actual_stems) if fr else 0)
		if off_budget and not (week <= current_week and harvested != projected):
			continue
		flagged = comment_week and projected > 0 and abs(harvested - projected) * 10 >= projected
		rows.append(
			{
				"greenhouse": key[0],
				"variety": key[1],
				"projection_name": fcname,
				"flagged": flagged,
				"week": {
					"name": fr.name if fr else None,
					"week_no": week,
					"projected_qty": projected,
					"actual_harvest": harvested,
					"revised_forecast": frappe.utils.cint(fr.revised_forecast_stems) if fr else 0,
					"custom_comment": (fr.note or "") if fr else "",
					"editable": editable,
				},
			}
		)
	rows.sort(key=lambda r: 0 if r["flagged"] else 1)

	page = rows[start : start + page_length]
	for row in page:
		if row["projection_name"]:
			if not row["week"]["name"]:
				row["week"]["name"] = "week-" + str(week)
			continue
		row["projection_name"] = _new_production_forecast(row["greenhouse"], row["variety"], year)
		fr = frappe.db.get_value(
			"Production Forecast Week",
			{"parent": row["projection_name"], "grade": "all", "week_number": week},
			["name", "revised_forecast_stems", "note"],
			as_dict=True,
		)
		row["week"]["name"] = fr.name if fr else "week-" + str(week)

	frappe.response["message"] = {
		"status": "success",
		"total": len(rows),
		"start": start,
		"filled": sum(1 for r in rows if r["week"]["revised_forecast"] > 0),
		"editable": editable,
		"rows": page,
	}


@frappe.whitelist()
def updateRevisedForecast():
	projection_name = frappe.form_dict.get("projection_name")
	weeks = frappe.form_dict.get("weeks")

	if not projection_name or not weeks:
		frappe.throw("Missing required fields: projection_name, weeks")

	if isinstance(weeks, str):
		weeks = frappe.parse_json(weeks)

	# Revisions live on Production Forecast now; projection_name is the
	# Production Forecast docname handed out by getProductionProjection.
	doc = frappe.get_doc("Production Forecast", projection_name)

	week_map = {}
	for w in doc.weeks:
		week_map[w.name] = w

	lock_weeks = forecast_lock_weeks()
	updated = 0
	skipped = 0
	locked = 0
	for entry in weeks:
		row_name = entry.get("name")
		row = week_map.get(row_name)
		if not row:
			skipped = skipped + 1
			continue
		changed = 0
		if "revised_forecast" in entry:
			if forecast_week_editable(doc.forecast_year, row.week_number, lock_weeks):
				row.revised_forecast_stems = frappe.utils.cint(entry.get("revised_forecast"))
				changed = 1
			elif frappe.utils.cint(entry.get("revised_forecast")) != frappe.utils.cint(
				row.revised_forecast_stems
			):
				locked = locked + 1
		if "custom_comment" in entry:
			row.note = entry.get("custom_comment")
			changed = 1
		if changed:
			updated = updated + 1

	# Works on drafts and submitted forecasts alike: the revised column and
	# note are allow_on_submit, and saving recomputes every variance.
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	msg = str(updated) + " week(s) updated successfully"
	if skipped:
		msg = msg + " (" + str(skipped) + " outside the forecast window were skipped)"
	if locked:
		msg = (
			msg
			+ ". "
			+ str(locked)
			+ " week(s) within the next "
			+ str(lock_weeks)
			+ " weeks are locked and were not changed"
		)
	frappe.response["message"] = {"status": "success", "updated": updated, "locked": locked, "message": msg}


@frappe.whitelist()
def productionListCompanies():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 200)
	if limit > 500:
		limit = 500
	try:
		filters = {}
		if search:
			filters["name"] = ["like", "%" + search + "%"]
		rows = frappe.get_all("Company", filters=filters, fields=["name"], order_by="name asc", limit=limit)
		out = []
		for r in rows:
			out.append({"name": r.get("name"), "label": r.get("name")})
		frappe.response["data"] = {"count": len(out), "companies": out}
	except Exception as e:
		frappe.log_error("productionListCompanies error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListEmployees():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	company = (data.get("company") or "").strip()
	limit = int(data.get("limit") or 500)
	if limit > 5000:
		limit = 5000
	try:
		filters = {"status": "Active"}
		if company:
			filters["company"] = company
		or_filters = None
		if search:
			or_filters = {"employee_name": ["like", "%" + search + "%"], "name": ["like", "%" + search + "%"]}
		rows = frappe.get_all(
			"Employee",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "employee_name", "designation"],
			order_by="employee_name asc",
			limit=limit,
		)
		out = []
		for r in rows:
			out.append(
				{
					"name": r.get("name"),
					"label": r.get("employee_name") or r.get("name"),
					"designation": r.get("designation") or "",
				}
			)
		me = (
			frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")
			or ""
		)
		my_company = (
			(frappe.db.get_value("Employee", me, "company") if me else None)
			or frappe.defaults.get_user_default("Company")
			or frappe.db.get_single_value("Global Defaults", "default_company")
			or ""
		)
		frappe.response["data"] = {"count": len(out), "employees": out, "me": me, "my_company": my_company}
	except Exception as e:
		frappe.log_error("productionListEmployees error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListTasks():
	data = frappe.request.get_json() or {}
	search = (data.get("search") or "").strip()
	limit = int(data.get("limit") or 500)
	if limit > 5000:
		limit = 5000
	try:
		filters = {"status": ["not in", ["Cancelled", "Template"]]}
		or_filters = None
		if search:
			or_filters = {"subject": ["like", "%" + search + "%"], "name": ["like", "%" + search + "%"]}
		rows = frappe.get_all(
			"Task",
			filters=filters,
			or_filters=or_filters,
			fields=["name", "subject", "project"],
			order_by="subject asc",
			limit=limit,
		)
		out = []
		for r in rows:
			out.append(
				{
					"name": r.get("name"),
					"label": r.get("subject") or r.get("name"),
					"project": r.get("project") or "",
				}
			)
		frappe.response["data"] = {"count": len(out), "tasks": out}
	except Exception as e:
		frappe.log_error("productionListTasks error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


# --- Greenhouse 5S / DMS audits ------------------------------------------
# audit_type is "5S" or "DMS"; each maps to its own doctype with one
# checklist table per section (see greenhouse_audit_templates.AUDIT_TYPES).

AUDIT_HEADER_FIELDS = ["audit_date", "farm", "greenhouse", "zone", "zone_leader", "auditor", "auditee"]


def audit_doctype_sections(audit_type):
	from upande_agriculture.greenhouse_audit_templates import AUDIT_TYPES

	if audit_type not in AUDIT_TYPES:
		frappe.throw(_("audit_type must be one of: {0}").format(", ".join(AUDIT_TYPES.keys())))
	return AUDIT_TYPES[audit_type]


def audit_payload(doc, sections):
	out_sections = []
	for section, table in sections.items():
		rows = []
		for r in doc.get(table) or []:
			rows.append(
				{
					"name": r.name or "",
					"look_at": r.look_at or "",
					"look_for": r.look_for or "",
					"observation": r.observation or "",
					"max_score": r.max_score or 0,
					"score": "" if r.score in (None, "") else str(r.score),
					"comments": r.comments or "",
				}
			)
		out_sections.append({"section": section, "table": table, "rows": rows})
	out = {
		"name": doc.name if not doc.is_new() else "",
		"docstatus": doc.docstatus or 0,
		"audit_template": doc.audit_template or "",
		"total_score": doc.total_score or 0,
		"max_score": doc.max_score or 0,
		"percentage": doc.percentage or 0,
		"sections": out_sections,
	}
	for f in AUDIT_HEADER_FIELDS:
		out[f] = str(doc.get(f) or "")
	for f in ["zone_leader", "auditor", "auditee"]:
		out[f + "_name"] = doc.get(f + "_name") or ""
	out["scores"] = frappe.get_all("Greenhouse Audit Score", pluck="name", order_by="score asc")
	return out


@frappe.whitelist()
def productionListAudits():
	data = frappe.request.get_json() or {}
	audit_type = (data.get("audit_type") or "").strip()
	greenhouse = (data.get("greenhouse") or "").strip()
	limit = int(data.get("limit") or 100)
	if limit > 500:
		limit = 500
	try:
		doctype = audit_doctype_sections(audit_type)[0]
		filters = {"docstatus": ["<", 2]}
		if greenhouse:
			filters["greenhouse"] = greenhouse
		rows = frappe.get_all(
			doctype,
			filters=filters,
			fields=[
				"name",
				"audit_date",
				"farm",
				"greenhouse",
				"zone",
				"auditor_name",
				"total_score",
				"max_score",
				"percentage",
				"docstatus",
			],
			order_by="audit_date desc, creation desc",
			limit=limit,
		)
		out = []
		for r in rows:
			out.append(
				{
					"name": r.get("name"),
					"audit_date": str(r.get("audit_date") or ""),
					"farm": r.get("farm") or "",
					"greenhouse": r.get("greenhouse") or "",
					"zone": r.get("zone") or "",
					"auditor_name": r.get("auditor_name") or "",
					"total_score": r.get("total_score") or 0,
					"max_score": r.get("max_score") or 0,
					"percentage": r.get("percentage") or 0,
					"docstatus": r.get("docstatus") or 0,
				}
			)
		frappe.response["data"] = {"count": len(out), "audits": out}
	except Exception as e:
		frappe.log_error("productionListAudits error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionGetAudit():
	# With a name: that audit. Without: a blank audit with the checklist
	# already loaded from the type's default template, for a new entry.
	data = frappe.request.get_json() or {}
	audit_type = (data.get("audit_type") or "").strip()
	name = (data.get("name") or "").strip()
	try:
		doctype, sections = audit_doctype_sections(audit_type)
		if name:
			doc = frappe.get_doc(doctype, name)
		else:
			from upande_agriculture.greenhouse_audit import default_template

			doc = frappe.new_doc(doctype)
			doc.audit_template = default_template(audit_type)
			if not doc.audit_template:
				frappe.throw(_("No enabled {0} Greenhouse Audit Template.").format(audit_type))
			doc.audit_date = frappe.utils.today()
			doc.auditor = frappe.db.get_value(
				"Employee", {"user_id": frappe.session.user, "status": "Active"}, "name"
			)
			if doc.auditor:
				doc.auditor_name = frappe.db.get_value("Employee", doc.auditor, "employee_name")
			doc.load_checklist()
			doc.compute_scores()
		frappe.response["data"] = audit_payload(doc, sections)
	except Exception as e:
		frappe.log_error("productionGetAudit error: " + str(e))
		frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionSaveAudit():
	# sections: {table fieldname: [{score, comments}, ...]} in checklist order.
	data = frappe.request.get_json() or {}
	audit_type = (data.get("audit_type") or "").strip()
	name = (data.get("name") or "").strip()
	sections_in = data.get("sections") or {}
	submit = bool(data.get("submit"))
	try:
		doctype, sections = audit_doctype_sections(audit_type)
		if name:
			doc = frappe.get_doc(doctype, name)
			if doc.docstatus != 0:
				frappe.throw(name + " is already submitted.")
		else:
			doc = frappe.new_doc(doctype)
		for f in AUDIT_HEADER_FIELDS:
			if data.get(f) is not None:
				doc.set(f, data.get(f) or None)
		if doc.is_new():
			doc.run_method("before_validate")
		for table in sections.values():
			rows = doc.get(table) or []
			entries = sections_in.get(table) or []
			for i in range(min(len(rows), len(entries))):
				entry = entries[i] or {}
				if entry.get("score") is not None:
					rows[i].score = str(entry.get("score")) if entry.get("score") != "" else None
				if entry.get("comments") is not None:
					rows[i].comments = entry.get("comments")
		doc.save(ignore_permissions=True)
		if submit:
			doc.submit()
		frappe.db.commit()
		out = audit_payload(doc, sections)
		out["message"] = (
			doc.name
			+ (" submitted" if submit else " saved")
			+ " - "
			+ str(doc.total_score)
			+ "/"
			+ str(doc.max_score)
			+ " ("
			+ str(doc.percentage)
			+ "%)."
		)
		frappe.response["data"] = out
	except Exception as e:
		frappe.db.rollback()
		frappe.log_error("productionSaveAudit error: " + str(e))
		frappe.response["data"] = {"error": str(e)}
