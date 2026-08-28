# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Mobile API — server scripts called by the Upande mobile apps, ported
# verbatim from DB Server Scripts (bench + kaitet-group v15 live) into
# version-controlled whitelisted methods. Bodies keep frappe.form_dict /
# frappe.response exactly as the live scripts used them.

import frappe
from frappe import _
import json


@frappe.whitelist()
def createGradingStockEntry():
    try:
        data = frappe.request.get_json()
        bunch_qr_doc = frappe.get_doc("Bunch QR Code", data.get("bunch_id"))
        farm = bunch_qr_doc.farm
        stock_entry_type = data.get("stock_entry_type")
        graded_by = data.get("graded_by")
        stem_length = bunch_qr_doc.stem_length
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
            spray_item_group = frappe.db.get_value("Item", variety, "item_group") if variety else None
            max_spray_limit = 0
            if spray_item_group:
                max_spray_limit = frappe.db.get_value(
                    "Harvest Item Group Config",
                    {"parent": "Production Settings", "parenttype": "Production Settings", "item_group": spray_item_group},
                    "max_stems_per_bucket",
                ) or 0
            size_digits = "".join([c for c in str(raw_bunch_size) if c.isdigit()])
            bunch_size_stems = int(size_digits) if size_digits else 0
            if max_spray_limit and bunch_size_stems:
                new_stems = float(qty or 0) * bunch_size_stems
                todays_harvest_entries = frappe.get_all(
                    "Stock Entry",
                    filters={
                        "custom_bucket_id": bucket_id,
                        "stock_entry_type": "Harvesting",
                        "posting_date": frappe.utils.today(),
                        "docstatus": ["<", 2],
                    },
                    fields=["name"],
                )
                existing_stems = 0
                if todays_harvest_entries:
                    detail_rows = frappe.get_all(
                        "Stock Entry Detail",
                        filters={"parent": ["in", [e.name for e in todays_harvest_entries]]},
                        fields=["qty"],
                    )
                    existing_stems = sum((row.qty or 0) for row in detail_rows)
                if existing_stems + new_stems > float(max_spray_limit):
                    remaining_stems = int(max(float(max_spray_limit) - existing_stems, 0))
                    limit_msg = (
                        f"Bucket limit exceeded: this bucket already holds {int(existing_stems)} stems "
                        f"today and this scan adds {int(new_stems)}, over the maximum of "
                        f"{int(max_spray_limit)} spray stems per bucket. Only {remaining_stems} more "
                        f"stems can go in this bucket."
                    )
                    frappe.response["http_status_code"] = 400
                    frappe.response["error"] = limit_msg
                    frappe.response["message"] = limit_msg
                    frappe.throw(limit_msg)
        # --- end spray bucket stem limit validation ---
         # ======================================
        # VALIDATION: Check if item is planted in the source warehouse/greenhouse
        # ======================================
        if source_warehouse and variety:
            try:
                # Varieties standing in this greenhouse come from the Greenhouse
                # doctype's varieties_grown ledger; active Crop Cycles (one per
                # variety) are the fallback for houses without a ledger yet.
                vg_rows = frappe.get_all(
                    "Greenhouse Variety",
                    filters={"parent": source_warehouse, "parenttype": "Greenhouse", "parentfield": "varieties_grown"},
                    fields=["variety"],
                )
                varieties_planted = [r.variety for r in vg_rows]
                if not varieties_planted:
                    varieties_planted = frappe.get_all(
                        "Crop Cycle",
                        filters={"greenhouse": source_warehouse, "status": ["!=", "Ended"]},
                        pluck="variety",
                    )

                # Check if the item being graded is planted in this warehouse
                if variety not in varieties_planted:
                    frappe.response["http_status_code"] = 400
                    frappe.response["error"] = f"Item '{variety}' is not planted in warehouse '{source_warehouse}'. Please verify the correct location."
                    frappe.response["message"] = f"Item '{variety}' is not planted in warehouse '{source_warehouse}'. Please verify the correct location."
                else:
                    timestamp = frappe.utils.now_datetime()
                    harvest_batch_id = f"{bucket_id}-{farm}-{variety}-{timestamp}"
                    digits = "".join([c for c in str(raw_bunch_size) if c.isdigit()])
                    if digits:
                        bunch_size = int(digits)
                    else:
                        frappe.throw(f"Invalid bunch size: '{raw_bunch_size}'")
                        frappe.log_error("Invalid bunch size",raw_bunch_size)

                    farm_doc = frappe.get_doc("Farm", farm)
                    company = farm_doc.company
                    if rose_type == "Spray Roses":
                        if not bucket_id:
                            frappe.throw("Bucket ID is required for Spray Roses")

                        if not frappe.db.exists("Bucket QR Code", bucket_id):
                            new_bucket = frappe.new_doc("Bucket QR Code")
                            new_bucket.id = bucket_id
                            new_bucket.insert()
                            frappe.log_error("Grading Debug", f"Submitted new Bucket QR Code: {new_bucket.name}")


                        harvest_doc = frappe.new_doc("Stock Entry")
                        harvest_doc.stock_entry_type = "Harvesting"
                        harvest_doc.custom_bucket_id = bucket_id
                        harvest_doc.custom_greenhouse = source_warehouse
                        harvest_doc.farm = farm   # Farm accounting dimension (mirrored to custom_farm by hook)
                        harvest_doc.custom_business_unit = "Roses"
                        harvest_doc.company = company
                        harvest_doc.custom_harvester = graded_by
                        harvest_doc.custom_stem_length = stem_length
                        harvest_doc.custom_harvest_batch_no = harvest_batch_id
                        harvest_doc.append("items", {
                            "t_warehouse": source_warehouse,
                            "item_code": variety,
                            "qty": qty * bunch_size,
                            "uom": "Stems",
                            "allow_zero_valuation_rate": 1,
                        })
                        harvest_doc.insert(ignore_permissions=True)
                        harvest_doc.submit()
                        frappe.log_error("Grading Debug", f"Harvest Stock Entry created: {harvest_doc.name}")

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
                            frappe.throw(f"No mapping row found for stock_entry_type '{stock_entry_type}' in {mapping_name}.")
                        mapped_source_wh = row.get("source_warehouse")
                        mapped_target_wh = source_warehouse

                    frappe.log_error("Grading Debug - Stock Entry", json.dumps({
                        "mapped_source_wh": mapped_source_wh,
                        "mapped_target_wh": mapped_target_wh,
                        "qty": qty
                    }, default=str))

                    ref_harvest = None
                    if bucket_id:
                        ref_harvest = frappe.db.get_value("Stock Entry", {"custom_bucket_id": bucket_id, "stock_entry_type": "Harvesting", "docstatus": 1}, ["custom_greenhouse", "custom_harvester", "custom_cut_stage", "posting_date"], as_dict=True, order_by="creation desc")
                    se = frappe.new_doc("Stock Entry")
                    se.stock_entry_type = stock_entry_type
                    se.farm = farm   # Farm accounting dimension (mirrored to custom_farm by hook)
                    se.company = company
                    se.custom_business_unit = "Roses"
                    se.custom_scanned_grading = 1
                    se.custom_harvest_batch_no = harvest_batch_id
                    se.custom_graded_by = graded_by
                    se.custom_stem_length = stem_length
                    se.custom_bunched_by = raw_bunch_size
                    se.custom_bunch_id = bunch_id
                    se.custom_greenhouse = source_warehouse or (ref_harvest.custom_greenhouse if ref_harvest else None)
                    se.custom_bucket_id = bucket_id
                    se.custom_harvester = (ref_harvest.custom_harvester if ref_harvest else None) or graded_by
                    if ref_harvest and ref_harvest.get("custom_cut_stage"):
                        se.custom_cut_stage = ref_harvest.custom_cut_stage
                    se.custom_harvest_date = (ref_harvest.posting_date if ref_harvest else None)
                    se.custom_grading_date = frappe.utils.nowdate()
                    se.from_warehouse = mapped_source_wh
                    se.to_warehouse = mapped_target_wh
                    se.append("items", {
                        "s_warehouse": mapped_source_wh,
                        "t_warehouse": mapped_target_wh,
                        "item_code": variety,
                        "qty": qty,
                        "uom": raw_bunch_size,
                        "conversion_factor": bunch_size,
                        "allow_zero_valuation_rate": 1
                    })
                    se.insert(ignore_permissions=True)
                    se.submit()
                    frappe.log_error("Grading Debug", f"Final Stock Entry created: {se.name}")

                    frappe.response["message"] = "Stock Entry submitted successfully"
                    frappe.response["stock_entry"] = se.name
            except frappe.DoesNotExistError:
                # If warehouse doesn't exist or doesn't have the custom field, log and continue
                frappe.log_error("Grading Validation Warning", f"Could not validate varieties for warehouse '{source_warehouse}'")


    except Exception as e:
        frappe.log_error("Grading Error", str(e))
        frappe.response["http_status_code"] = 500
        frappe.response["message"] = "An error occurred while submitting the stock entry"
        frappe.response["error"] = str(e)


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

        # Resolve harvester to an Employee id. custom_harvester is a Link to
        # Employee, so it needs the id; accept an employee_name too (older app
        # builds send the display name).
        if harvester and not frappe.db.exists("Employee", harvester):
            resolved_harvester = frappe.db.get_value("Employee", {"employee_name": harvester}, "name")
            if resolved_harvester:
                harvester = resolved_harvester

        # Enforce the configurable per-bucket limit (Production Settings). The
        # config is keyed by item group, but it is usually set on a PARENT group
        # (e.g. "Standard Roses"), while an item lives in a leaf group (e.g.
        # "Standard Roses - Intermediate"). So resolve the cap against the item's
        # whole ancestor chain and use the NEAREST configured ancestor.
        # 0 / unset means no cap.
        limit_item_group = frappe.db.get_value("Item", item_code, "item_group") if item_code else None
        max_standard_limit = 0
        matched_group = limit_item_group
        if limit_item_group:
            configs = frappe.get_all(
                "Harvest Item Group Config",
                filters={"parent": "Production Settings", "parenttype": "Production Settings"},
                fields=["item_group", "max_stems_per_bucket"],
            )
            config_map = {c["item_group"]: c["max_stems_per_bucket"] for c in configs}
            if config_map:
                # Ancestors of the item's group (self + parents), nearest first.
                bounds = frappe.db.get_value("Item Group", limit_item_group, ["lft", "rgt"], as_dict=True)
                if bounds:
                    ancestors = frappe.get_all(
                        "Item Group",
                        filters={"lft": ["<=", bounds.lft], "rgt": [">=", bounds.rgt]},
                        fields=["name"],
                        order_by="lft desc",   # deepest (most specific) first
                    )
                    for a in ancestors:
                        if a["name"] in config_map:
                            max_standard_limit = config_map[a["name"]] or 0
                            matched_group = a["name"]
                            break
        if max_standard_limit and float(quantity or 0) > float(max_standard_limit):
            frappe.log_error("Bucket Rate Error", data)
            frappe.throw(_(f"The maximum stems per bucket for {matched_group} is {int(max_standard_limit)}"))

        # Check if the item is a Spray Rose
        if item_code:
            item_group = frappe.db.get_value("Item", item_code, "item_group")

            # Block Spray Roses
            if item_group == "Spray Roses":
                frappe.log_error("Attempt to harvest sprays from the harvest form", data)
                frappe.throw(_("Spray Roses can only be harvested on the grading page"))


        # Handle both string and JSON dict formats for bucket_id
        if isinstance(bucket_data, dict):
            bucket_id = list(bucket_data.keys())[0]
        else:
            bucket_id = str(bucket_data).strip()


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

        # create bucket if it does not exist
        if not frappe.db.exists("Bucket QR Code", bucket_id):
            frappe.get_doc({
                "doctype": "Bucket QR Code",
                "id": bucket_id,
                "status": "Available"
            }).insert(ignore_permissions=True)

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
            frappe.response["error"] = f"Bucket {bucket_id} is already in use. Please receive it first before harvesting again."
            frappe.throw(f"Bucket {bucket_id} is already in use. Please receive it first before harvesting again.")

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
        #                 "custom_received_bucket_id": bucket_id,
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

        stock_entry.append("items", {
            "item_code": item_code,
            "qty": quantity,
            "allow_zero_valuation_rate": 1
        })

        se = stock_entry.insert()
        se_submit = stock_entry.submit()

        if se and se_submit:
            # ======================================
            # UPDATE BUCKET STATUS TO "In Use"
            # ======================================
            # Mark bucket as In Use and link this stock entry so subsequent
            # harvest attempts on the same bucket are blocked until receiving.
            bucket_qr_doc.status = "In Use"
            bucket_qr_doc.last_stock_entry = stock_entry.name
            bucket_qr_doc.save(ignore_permissions=True)
            frappe.db.commit()

            frappe.response["message"] = "Harvesting Stock Entry submitted successfully"
            frappe.response["stock_entry"] = stock_entry.name

    except frappe.PermissionError as e:
        # frappe.log_error("Permission Error - Stock Entry Creation", f"User {frappe.session.user} does not have permission to create Stock Entry")
        frappe.response["http_status_code"] = 200
        frappe.response["error"] = "You do not have sufficient permissions to create Stock Entry. Please contact your IT Administrator"

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
                varieties.append({"variety": v, "area": r.get("area_m2"), "item_group": item_group})

        # Fetch employees (harvesters) linked to this greenhouse via the
        # custom_greenhouses child table. A harvester can belong to several
        # greenhouses, so match any Employee Greenhouse row for this greenhouse.
        greenhouse_links = frappe.get_all(
            "Employee Greenhouse",
            filters={
                "greenhouse": greenhouse_name,
                "parenttype": "Employee"
            },
            fields=["parent"]
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
                filters={
                    "name": ["in", employee_ids]
                },
                fields=["employee_name"]
            )

        # Cut stages come from the Cut Stage master (named by the cutstage value).
        cut_stage_rows = frappe.get_all("Cut Stage", fields=["cutstage"], order_by="cutstage asc")
        cut_stages = []
        for cs in cut_stage_rows:
            v = cs.get("cutstage")
            if v and v not in cut_stages:
                cut_stages.append(v)

        notice = None
        if not varieties:
            notice = "Nothing is standing in this greenhouse yet. Create a crop cycle to load its varieties."

        frappe.response["data"] = {
            "varieties": varieties,
            "employees": employees,
            "cut_stages": cut_stages,
            "notice": notice
        }
    except Exception as e:
        frappe.log_error(message=e, title="Get greenhouse data error")
        frappe.throw(_("Error fetching greenhouse data: ") + str(e))


@frappe.whitelist()
def getProductionProjection():
    greenhouse = frappe.form_dict.get("greenhouse")
    variety = frappe.form_dict.get("variety")
    year = frappe.form_dict.get("year")

    if not greenhouse or not variety or not year:
        frappe.throw("Missing required fields: greenhouse, variety, year")

    year = int(year)

    # Budget: the system-generated Production Projection for this house x variety.
    proj = frappe.db.get_value("Production Projection",
        {"greenhouse": greenhouse, "variety": variety, "projection_year": year}, "name")
    budget = {}
    if proj:
        for r in frappe.get_all("Projection Week", filters={"parent": proj},
                                fields=["week", "projected_stems"]):
            budget[int(r.get("week") or 0)] = int(r.get("projected_stems") or 0)

    # Revisions live on Production Forecast now. Make sure an Active one exists
    # so the app's Revised column has editable rows to write into.
    fc = frappe.get_all("Production Forecast",
        filters={"greenhouse": greenhouse, "variety": variety,
                 "forecast_year": year, "status": "Active"},
        order_by="revision desc", limit=1, pluck="name")
    fcname = fc[0] if fc else None
    if not fcname:
        today = frappe.utils.getdate(frappe.utils.nowdate())
        iso = today.isocalendar()
        lastweek = frappe.utils.getdate(str(year) + "-12-28").isocalendar()[1]
        startweek = 1
        if iso[0] == year:
            startweek = iso[1]
        window = lastweek - startweek + 1
        if window > 26:
            window = 26
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
        fcname = fdoc.name

    fcrows = {}
    for r in frappe.get_all("Production Forecast Week",
            filters={"parent": fcname, "grade": "all"},
            fields=["name", "week_number", "budget_stems", "forecasted_stems",
                    "revised_forecast_stems", "actual_stems", "note"]):
        fcrows[int(r.get("week_number") or 0)] = r

    # Live actuals straight from Harvesting entries, per ISO week (WEEK mode 3).
    actuals = frappe.db.sql("""
        SELECT WEEK(se.posting_date, 3) AS week_no, SUM(sed.qty) AS total_stems
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.stock_entry_type = 'Harvesting'
          AND (se.custom_greenhouse = %(greenhouse)s OR sed.t_warehouse = %(greenhouse)s)
          AND sed.item_code = %(variety)s
          AND YEAR(se.posting_date) = %(year)s
        GROUP BY WEEK(se.posting_date, 3)
    """, {"greenhouse": greenhouse, "variety": variety, "year": year}, as_dict=True)
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

    weeks = []
    for wk in weeknums:
        fr = fcrows.get(wk)
        weeks.append({
            # Rows inside the forecast window carry the real child-row name and
            # are editable; rows outside it are budget-only and read back as
            # skipped if the app tries to save them.
            "name": fr.get("name") if fr else "week-" + str(wk),
            "week_no": wk,
            "projected_qty": budget.get(wk, int(fr.get("budget_stems") or 0) if fr else 0),
            "actual_harvest": actuals_map.get(wk, int(fr.get("actual_stems") or 0) if fr else 0),
            "revised_forecast": int(fr.get("revised_forecast_stems") or 0) if fr else 0,
            "custom_comment": (fr.get("note") or "") if fr else "",
        })

    area = 0.0
    for c in frappe.get_all("Crop Cycle",
            filters={"greenhouse": greenhouse, "variety": variety, "status": ["!=", "Ended"]},
            fields=["planted_area"]):
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
                frappe.response["data"] = {"error": "At least one valid bed range (variety + bed numbers) is required."}
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

                names = []
                for variety in order:
                    g = groups[variety]
                    parts = []
                    for rng in sorted(g["ranges"]):
                        if rng[0] == rng[1]:
                            parts.append(str(rng[0]))
                        else:
                            parts.append(str(rng[0]) + "-" + str(rng[1]))
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
                    "message": str(len(names)) + " crop cycle(s) created: " + ", ".join(names),
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
                frappe.response["data"] = {"error": "No valid items provided (variety + qty required per row)."}
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
    greenhouse = (data.get('greenhouse') or '').strip()
    try:
        if not greenhouse:
            frappe.response['data'] = {'error': 'greenhouse is required.'}
        else:
            wh = frappe.db.get_value('Warehouse', greenhouse, ['warehouse_name', 'custom_farm'], as_dict=True)
            farm = ''
            label = greenhouse
            if wh:
                label = wh.get('warehouse_name') or greenhouse
                farm = wh.get('custom_farm') or ''
            spec = {'name': greenhouse, 'label': label, 'farm': farm, 'has_cycle': 0, 'total_beds': 0, 'total_planted_area': 0.0, 'number_of_plants': 0, 'variety_count': 0, 'varieties': [], 'bed_summary': [], 'bays': [], 'growers': []}
            # The Greenhouse doctype's ledger is the source of truth now: its
            # rollups and varieties_grown reflect exactly what is standing.
            gh = frappe.db.get_value('Greenhouse', greenhouse, ['number_of_beds', 'area_planted', 'number_of_plants', 'varieties', 'farm'], as_dict=True)
            if gh:
                spec['total_beds'] = gh.get('number_of_beds') or 0
                spec['total_planted_area'] = gh.get('area_planted') or 0.0
                spec['number_of_plants'] = gh.get('number_of_plants') or 0
                spec['variety_count'] = gh.get('varieties') or 0
                if not spec['farm']:
                    spec['farm'] = gh.get('farm') or ''
                spec['varieties'] = frappe.get_all('Greenhouse Variety', filters={'parent': greenhouse, 'parenttype': 'Greenhouse'}, fields=['variety', 'beds', 'area_m2', 'plants'], order_by='beds desc')
                # Per-status breakdown of the bed ledger: what's standing,
                # what's out of the ground, what's empty.
                summary = {}
                for b in frappe.get_all('Greenhouse Bed', filters={'parent': greenhouse, 'parenttype': 'Greenhouse'}, fields=['status', 'plant_count', 'area_m2'], limit=0):
                    st = b.get('status') or 'Empty'
                    row = summary.setdefault(st, {'status': st, 'beds': 0, 'plants': 0, 'area_m2': 0.0})
                    row['beds'] = row['beds'] + 1
                    row['plants'] = row['plants'] + int(b.get('plant_count') or 0)
                    row['area_m2'] = row['area_m2'] + float(b.get('area_m2') or 0)
                spec['bed_summary'] = sorted(summary.values(), key=lambda r: r['beds'], reverse=True)
            active_cycles = frappe.get_all('Crop Cycle', filters={'greenhouse': greenhouse, 'status': ['!=', 'Ended']}, pluck='name')
            if active_cycles or (gh and (gh.get('varieties') or 0) > 0):
                spec['has_cycle'] = 1
            growers = []
            try:
                glinks = frappe.get_all('Employee Greenhouse', filters={'greenhouse': greenhouse}, fields=['parent'])
                for gl in glinks:
                    emp = gl.get('parent')
                    ed = frappe.db.get_value('Employee', emp, ['employee_name', 'designation'], as_dict=True)
                    nm = emp
                    dg = ''
                    if ed:
                        nm = ed.get('employee_name') or emp
                        dg = ed.get('designation') or ''
                    growers.append({'employee': emp, 'employee_name': nm, 'designation': dg})
            except Exception:
                growers = []
            spec['growers'] = growers
            frappe.response['data'] = spec
    except Exception as e:
        frappe.log_error('productionGetGreenhouseSpec error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionGetPlanForm():
    data = frappe.request.get_json() or {}
    name = (data.get('name') or '').strip()
    try:
        if not name:
            frappe.response['data'] = {'error': 'name is required.'}
        else:
            doc = frappe.get_doc('Production Plan Form', name)
            empnames = {}
            tasks = []
            for t in (doc.tasks or []):
                emp = t.assigned_to or ''
                if emp and emp not in empnames:
                    empnames[emp] = frappe.db.get_value('Employee', emp, 'employee_name') or emp
                tasks.append({
                    'name': t.name,
                    'task_name': t.task_name or '',
                    'operation': t.operation or '',
                    'greenhouse': t.greenhouse or '',
                    'variety': t.variety or '',
                    'target': t.target or 0,
                    'beds': t.beds or '',
                    'due_date': str(t.due_date or ''),
                    'assigned_to': emp,
                    'assigned_to_name': empnames.get(emp, ''),
                    'status': t.status or 'Open',
                    'harvested': t.harvested or 0,
                    'completion_note': t.completion_note or '',
                })
            varieties = []
            for v in (doc.varieties or []):
                varieties.append({
                    'name': v.name,
                    'variety': v.variety or '',
                    'planned_stems': v.planned_stems or 0,
                    'forecast_stems': v.forecast_stems or 0,
                    'budget_stems': v.budget_stems or 0,
                    'notes': v.notes or '',
                })
            farm = frappe.db.get_value('Warehouse', doc.greenhouse, 'custom_farm') or '' if doc.greenhouse else ''
            frappe.response['data'] = {
                'name': doc.name,
                'greenhouse': doc.greenhouse or '',
                'farm': farm,
                'plan_year': doc.plan_year or 0,
                'plan_week': doc.plan_week or 0,
                'plan_period': doc.plan_period or '',
                'tasks': tasks,
                'varieties': varieties,
            }
    except Exception as e:
        frappe.log_error('productionGetPlanForm error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionHarvestReport():
    data = frappe.request.get_json() or {}
    date = (data.get("date") or "").strip()
    farm_filter = (data.get("farm") or "").strip()
    if not date:
        frappe.response["data"] = {"error": "date is required."}
    else:
        try:
            # Aggregate a given stock entry type (Harvesting / Receiving) for the
            # date into farm -> variety -> stem_length totals. Both flows carry the
            # same harvest-detail fields (custom_farm, custom_stem_length), so one
            # helper serves both sections of the report.
            def aggregate(entry_type):
                se_filters = {"stock_entry_type": entry_type, "docstatus": 1, "posting_date": date}
                if farm_filter:
                    se_filters["custom_farm"] = farm_filter
                entries = frappe.get_all("Stock Entry", filters=se_filters,
                    fields=["name", "custom_farm", "custom_stem_length"])
                meta = {}
                for e in entries:
                    meta[e.get("name")] = {
                        "farm": e.get("custom_farm") or "Unspecified",
                        "stem_length": e.get("custom_stem_length") or "Unspecified",
                    }
                rows = []
                if meta:
                    rows = frappe.get_all("Stock Entry Detail",
                        filters={"parent": ["in", list(meta.keys())]},
                        fields=["parent", "item_code", "qty"])
                agg = {}
                total = 0
                for r in rows:
                    info = meta.get(r.get("parent")) or {}
                    farm = info.get("farm") or "Unspecified"
                    variety = r.get("item_code") or "Unspecified"
                    sl = info.get("stem_length") or "Unspecified"
                    qty = int(r.get("qty") or 0)
                    total = total + qty
                    fbucket = agg.setdefault(farm, {})
                    vbucket = fbucket.setdefault(variety, {})
                    vbucket[sl] = vbucket.get(sl, 0) + qty
                farms_out = []
                for farm in sorted(agg.keys()):
                    varieties_out = []
                    farm_stems = 0
                    for variety in sorted(agg[farm].keys()):
                        sl_map = agg[farm][variety]
                        sl_out = []
                        variety_stems = 0
                        for sl in sorted(sl_map.keys()):
                            sl_out.append({"stem_length": sl, "stems": sl_map[sl]})
                            variety_stems = variety_stems + sl_map[sl]
                        varieties_out.append({"variety": variety, "stems": variety_stems, "stem_lengths": sl_out})
                        farm_stems = farm_stems + variety_stems
                    farms_out.append({"farm": farm, "stems": farm_stems, "varieties": varieties_out})
                return {"total": total, "farms": farms_out}

            harvested = aggregate("Harvesting")
            received = aggregate("Receiving")
            frappe.response["data"] = {
                "date": date,
                "total_stems": harvested["total"],
                "farms": harvested["farms"],
                "received_total_stems": received["total"],
                "received_farms": received["farms"],
            }
        except Exception as e:
            frappe.log_error("productionHarvestReport error: " + str(e))
            frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListCropCycles():
    data = frappe.request.get_json() or {}
    greenhouse = (data.get('greenhouse') or '').strip()
    farm = (data.get('farm') or '').strip()
    variety = (data.get('variety') or '').strip()
    limit = int(data.get('limit') or 200)
    if limit > 500:
        limit = 500
    try:
        # New model: one Crop Cycle = one variety on a set of beds. Ended
        # cycles are history and stay off the management list.
        filters = {'status': ['!=', 'Ended']}
        if greenhouse:
            filters['greenhouse'] = greenhouse
        if farm:
            filters['farm'] = farm
        if variety:
            filters['variety'] = variety
        rows = frappe.get_all('Crop Cycle', filters=filters,
            fields=['name', 'greenhouse', 'farm', 'variety', 'status', 'planting_date',
                    'planted_area', 'qty_planted', 'plants_standing', 'bed_range'],
            order_by='modified desc', limit=limit)
        bedcounts = {}
        names = []
        for r in rows:
            names.append(r.get('name'))
        if names:
            counted = frappe.get_all('Crop Cycle Bed', filters={'parent': ['in', names]}, fields=['parent'], limit=0)
            for c in counted:
                p = c.get('parent')
                bedcounts[p] = bedcounts.get(p, 0) + 1
        out = []
        for r in rows:
            numbers = []
            spec = (r.get('bed_range') or '').replace('–', '-').replace('—', '-')
            for chunk in spec.split(','):
                part = chunk.strip()
                if not part:
                    continue
                if '-' in part:
                    bits = part.split('-')
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
            out.append({
                'name': r.get('name') or '',
                'greenhouse': r.get('greenhouse') or '',
                'farm': r.get('farm') or '',
                'variety': r.get('variety') or '',
                'status': r.get('status') or '',
                'planting_date': str(r.get('planting_date') or ''),
                'beds': bedcounts.get(r.get('name'), 0),
                'bays': 0,
                'varieties': 1,
                'area': r.get('planted_area') or 0.0,
                'plants': r.get('plants_standing') or r.get('qty_planted') or 0,
                'start_bed': min(numbers) if numbers else 0,
                'end_bed': max(numbers) if numbers else 0,
            })
        frappe.response['data'] = {'count': len(out), 'cycles': out}
    except Exception as e:
        frappe.log_error('productionListCropCycles error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionListFarms():
    data = frappe.request.get_json() or {}
    search = (data.get("search") or "").strip()
    limit = int(data.get("limit") or 100)
    if limit > 500: limit = 500

    try:
        filters = {}
        if search:
            filters["name"] = ["like", "%" + search + "%"]
        rows = frappe.get_all(
            "Farm",
            filters=filters,
            fields=["name"],
            order_by="name asc",
            limit=limit,
        )
        farms = []
        for r in rows:
            nm = r.get("name") or ""
            farms.append({"name": nm, "label": nm})
        frappe.response["data"] = {"count": len(farms), "farms": farms}
    except Exception as e:
        frappe.log_error("productionListFarms error: " + str(e))
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListGreenhouseSpecs():
    data = frappe.request.get_json() or {}
    search = (data.get('search') or '').strip()
    farm = (data.get('farm') or '').strip()
    limit = int(data.get('limit') or 300)
    if limit > 1000:
        limit = 1000
    try:
        filters = {'disabled': 0, 'is_group': 0, 'warehouse_type': 'Greenhouse'}
        if search:
            filters['warehouse_name'] = ['like', '%' + search + '%']
        if farm:
            filters['custom_farm'] = farm
        ghs = frappe.get_all('Warehouse', filters=filters, fields=['name', 'warehouse_name', 'custom_farm'], order_by='name asc', limit=limit)
        # Rollups come from the Greenhouse doctype ledger; has_cycle from the
        # per-variety Crop Cycles that are still running.
        ledgers = frappe.get_all('Greenhouse', fields=['name', 'greenhouse', 'farm', 'number_of_beds', 'area_planted', 'varieties'])
        ledgermap = {}
        for c in ledgers:
            key = c.get('greenhouse') or c.get('name')
            if key and key not in ledgermap:
                ledgermap[key] = c
        active = frappe.get_all('Crop Cycle', filters={'status': ['!=', 'Ended']}, fields=['greenhouse'])
        activeset = []
        for a in active:
            g = a.get('greenhouse')
            if g and g not in activeset:
                activeset.append(g)
        out = []
        for g in ghs:
            led = ledgermap.get(g.get('name'))
            item = {'name': g.get('name') or '', 'label': g.get('warehouse_name') or g.get('name') or '', 'farm': g.get('custom_farm') or '', 'has_cycle': 0, 'total_beds': 0, 'total_area': 0.0, 'variety_count': 0}
            if led:
                item['total_beds'] = led.get('number_of_beds') or 0
                item['total_area'] = led.get('area_planted') or 0.0
                item['variety_count'] = led.get('varieties') or 0
                if not item['farm']:
                    item['farm'] = led.get('farm') or ''
            if g.get('name') in activeset or (led and (led.get('varieties') or 0) > 0):
                item['has_cycle'] = 1
            out.append(item)
        frappe.response['data'] = {'count': len(out), 'greenhouses': out}
    except Exception as e:
        frappe.log_error('productionListGreenhouseSpecs error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionListGreenhouses():
    data = frappe.request.get_json() or {}
    search = (data.get("search") or "").strip()
    farm = (data.get("farm") or "").strip()
    limit = int(data.get("limit") or 300)
    if limit > 1000: limit = 1000

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
            out.append({
                "name": r.get("name") or "",
                "label": r.get("warehouse_name") or r.get("name") or "",
                "farm": r.get("custom_farm") or "",
            })

        frappe.response["data"] = {"count": len(out), "greenhouses": out}
    except Exception as e:
        frappe.log_error("productionListGreenhouses error: " + str(e))
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListGrowerCandidates():
    data = frappe.request.get_json() or {}
    search = (data.get('search') or '').strip()
    limit = int(data.get('limit') or 50)
    if limit > 200:
        limit = 200
    try:
        filters = {'status': 'Active', 'designation': 'Grower'}
        if search:
            filters['employee_name'] = ['like', '%' + search + '%']
        rows = frappe.get_all('Employee', filters=filters, fields=['name', 'employee_name'], order_by='employee_name asc', limit=limit)
        out = []
        for r in rows:
            out.append({'employee': r.get('name') or '', 'employee_name': r.get('employee_name') or r.get('name') or ''})
        frappe.response['data'] = {'count': len(out), 'candidates': out}
    except Exception as e:
        frappe.log_error('productionListGrowerCandidates error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionListPlanForms():
    data = frappe.request.get_json() or {}
    greenhouse = (data.get('greenhouse') or '').strip()
    plan_year = data.get('plan_year')
    plan_week = data.get('plan_week')
    limit = int(data.get('limit') or 200)
    if limit > 500:
        limit = 500
    try:
        filters = {}
        if greenhouse:
            filters['greenhouse'] = greenhouse
        if plan_year:
            filters['plan_year'] = int(plan_year)
        if plan_week:
            filters['plan_week'] = int(plan_week)
        rows = frappe.get_all('Production Plan Form', filters=filters,
            fields=['name', 'greenhouse', 'plan_year', 'plan_week', 'plan_period'],
            order_by='plan_year desc, plan_week desc, modified desc', limit=limit)
        names = []
        for r in rows:
            names.append(r.get('name'))
        taskstats = {}
        stems = {}
        if names:
            for t in frappe.get_all('Production Plan Task', filters={'parent': ['in', names]}, fields=['parent', 'status'], limit=0):
                p = t.get('parent')
                st = taskstats.setdefault(p, {'total': 0, 'open': 0, 'done': 0})
                st['total'] = st['total'] + 1
                if (t.get('status') or 'Open') in ['Open', 'In Progress']:
                    st['open'] = st['open'] + 1
                if t.get('status') == 'Done':
                    st['done'] = st['done'] + 1
            for v in frappe.get_all('Production Plan Variety', filters={'parent': ['in', names]}, fields=['parent', 'planned_stems'], limit=0):
                p = v.get('parent')
                stems[p] = stems.get(p, 0) + int(v.get('planned_stems') or 0)
        farms = {}
        out = []
        for r in rows:
            gh = r.get('greenhouse') or ''
            if gh and gh not in farms:
                farms[gh] = frappe.db.get_value('Warehouse', gh, 'custom_farm') or ''
            st = taskstats.get(r.get('name'), {'total': 0, 'open': 0, 'done': 0})
            out.append({
                'name': r.get('name') or '',
                'greenhouse': gh,
                'farm': farms.get(gh, ''),
                'plan_year': r.get('plan_year') or 0,
                'plan_week': r.get('plan_week') or 0,
                'plan_period': r.get('plan_period') or '',
                'tasks_total': st['total'],
                'tasks_open': st['open'],
                'tasks_done': st['done'],
                'planned_stems': stems.get(r.get('name'), 0),
            })
        frappe.response['data'] = {'count': len(out), 'plans': out}
    except Exception as e:
        frappe.log_error('productionListPlanForms error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionListPropagationBatches():
    data = frappe.request.get_json() or {}
    stage = (data.get("stage") or "").strip()
    variety = (data.get("variety") or "").strip()
    search = (data.get("search") or "").strip()
    limit = int(data.get("limit") or 100)
    if limit > 500: limit = 500

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
            fields=["name", "variety", "variety_name", "propagation_method",
                    "current_stage", "iso_week", "iso_year", "start_date",
                    "total_cuttings_planned", "total_cuttings_actual",
                    "total_mortality", "total_survived",
                    "available_to_dispatch", "total_dispatched",
                    "success_rate", "propagation_unit", "company"],
            order_by="creation desc",
            limit=limit,
        )

        batches = []
        for r in rows:
            batches.append({
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
            })

        frappe.response["data"] = {"count": len(batches), "batches": batches}
    except Exception as e:
        frappe.log_error("productionListPropagationBatches error: " + str(e))
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListRejectionReasons():
    data = frappe.request.get_json() or {}
    limit = int(data.get("limit") or 100)
    if limit > 500: limit = 500

    try:
        rows = frappe.get_all(
            "Field Rejection Reason",
            fields=["name"],
            order_by="name asc",
            limit=limit,
        )
        reasons = []
        for r in rows:
            nm = r.get("name") or ""
            reasons.append({"name": nm, "label": nm})
        frappe.response["data"] = {"count": len(reasons), "reasons": reasons}
    except Exception as e:
        frappe.log_error("productionListRejectionReasons error: " + str(e))
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListSeedlingRequests():
    data = frappe.request.get_json() or {}
    status = (data.get("status") or "").strip()
    limit = int(data.get("limit") or 100)
    if limit > 500: limit = 500

    try:
        filters = {}
        if status:
            filters["status"] = status

        rows = frappe.get_all(
            "Seedling Request",
            filters=filters,
            fields=["name", "status", "request_date", "required_by_date",
                    "priority", "farm", "target_farm_block",
                    "total_qty_requested", "total_qty_dispatched",
                    "fulfilment_percentage", "days_to_fulfil", "reason",
                    "requested_by"],
            order_by="creation desc",
            limit=limit,
        )

        requests = []
        for r in rows:
            requests.append({
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
            })

        frappe.response["data"] = {"count": len(requests), "requests": requests}
    except Exception as e:
        frappe.log_error("productionListSeedlingRequests error: " + str(e))
        frappe.response["data"] = {"error": str(e)}


@frappe.whitelist()
def productionListVarieties():
    data = frappe.request.get_json() or {}
    search = (data.get("search") or "").strip()
    limit = int(data.get("limit") or 200)
    if limit > 500: limit = 500
    try:
        groups = frappe.get_all("Harvest Item Group Config",
            filters={"parent": "Production Settings", "parenttype": "Production Settings"},
            pluck="item_group")
        filters = {"disabled": 0}
        if groups:
            filters["item_group"] = ["in", groups]
        if search:
            filters["item_name"] = ["like", "%" + search + "%"]
        rows = frappe.get_all("Item", filters=filters,
            fields=["name", "item_name", "item_group"], order_by="item_name asc", limit=limit)
        out = []
        for r in rows:
            out.append({"name": r.get("name") or "", "label": r.get("item_name") or r.get("name") or "", "item_group": r.get("item_group") or ""})
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
                frappe.response["data"] = {"error": "No greenhouse ledger exists for " + house + ". Save the crop cycle once to create it."}
            else:
                gh = frappe.get_doc("Greenhouse", house)
                lo = int(from_bed)
                hi = int(to_bed)
                if lo > hi:
                    lo, hi = hi, lo

                # Uproot first (same date sorts before the replant), but only if
                # something is actually still standing on those beds.
                standing = 0
                for b in (gh.individual_beds or []):
                    if b.bed_number and lo <= int(b.bed_number) <= hi:
                        if b.status in ["Planted", "Producing", "Harvesting", "Transplanted"]:
                            standing = standing + int(b.plant_count or 0)
                if standing > 0:
                    reasonmap = {"End of Life": "Age (End of Life)", "Soil Issue": "Other"}
                    ghreason = reasonmap.get(reason, reason)
                    if ghreason not in ["Low Yield", "Disease", "Variety Change", "Age (End of Life)", "Storm Damage", "Other"]:
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

                newcycles = frappe.get_all("Crop Cycle",
                    filters={"greenhouse": house, "variety": new_variety, "status": ["!=", "Ended"]},
                    order_by="creation desc", limit=1, pluck="name")
                newname = newcycles[0] if newcycles else crop_cycle
                frappe.response["data"] = {
                    "status": "success",
                    "name": newname,
                    "message": "Replant recorded on beds " + str(lo) + "-" + str(hi) + ". New cycle: " + newname + ".",
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
            if ghreason not in ["Low Yield", "Disease", "Variety Change", "Age (End of Life)", "Storm Damage", "Other"]:
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
    name = (data.get('name') or '').strip()
    greenhouse = (data.get('greenhouse') or '').strip()
    plan_year = data.get('plan_year')
    plan_week = data.get('plan_week')
    tasks = data.get('tasks') or []
    varieties = data.get('varieties') or []

    try:
        if name:
            doc = frappe.get_doc('Production Plan Form', name)
            created = 0
        else:
            if not greenhouse:
                frappe.response['data'] = {'error': 'greenhouse is required.'}
                greenhouse = None
            elif not plan_year or not plan_week:
                frappe.response['data'] = {'error': 'plan_year and plan_week are required.'}
                greenhouse = None
            if greenhouse:
                existing = frappe.get_all('Production Plan Form',
                    filters={'greenhouse': greenhouse, 'plan_year': int(plan_year), 'plan_week': int(plan_week)},
                    limit=1, pluck='name')
                if existing:
                    doc = frappe.get_doc('Production Plan Form', existing[0])
                    created = 0
                else:
                    doc = frappe.new_doc('Production Plan Form')
                    doc.greenhouse = greenhouse
                    doc.plan_year = int(plan_year)
                    doc.plan_week = int(plan_week)
                    doc.plan_period = 'W' + str(int(plan_week)) + ' ' + str(int(plan_year))
                    doc.company = frappe.db.get_single_value('Global Defaults', 'default_company')
                    created = 1
        if greenhouse is not None or name:
            # Rows with a 'name' update that row; rows without append. Nothing
            # is ever deleted from here — removing planned work is a desk job.
            taskmap = {}
            for t in (doc.tasks or []):
                taskmap[t.name] = t
            for entry in tasks:
                row = taskmap.get(entry.get('name'))
                if not row:
                    row = doc.append('tasks', {})
                for f in ['task_name', 'operation', 'variety', 'beds', 'due_date', 'assigned_to', 'status', 'completion_note']:
                    if entry.get(f) is not None:
                        row.set(f, entry.get(f))
                if entry.get('target') is not None:
                    row.target = int(entry.get('target') or 0)
                if not row.greenhouse:
                    row.greenhouse = doc.greenhouse
                if not row.status:
                    row.status = 'Open'
            varmap = {}
            for v in (doc.varieties or []):
                varmap[v.name] = v
                varmap[v.variety] = v
            for entry in varieties:
                row = varmap.get(entry.get('name')) or varmap.get(entry.get('variety'))
                if not row:
                    row = doc.append('varieties', {})
                    row.variety = entry.get('variety')
                if entry.get('planned_stems') is not None:
                    row.planned_stems = int(entry.get('planned_stems') or 0)
                if entry.get('notes') is not None:
                    row.notes = entry.get('notes')
            doc.save(ignore_permissions=True)
            frappe.db.commit()
            todos = len(frappe.get_all('ToDo',
                filters={'reference_type': 'Production Plan Form', 'reference_name': doc.name}, pluck='name'))
            verb = 'created' if created else 'updated'
            frappe.response['data'] = {
                'status': 'success',
                'name': doc.name,
                'message': 'Plan ' + doc.name + ' ' + verb + ' with ' + str(len(doc.tasks or [])) + ' task(s).',
                'tasks': len(doc.tasks or []),
                'todos': todos,
            }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error('productionSavePlanForm error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionSetGreenhouseGrower():
    data = frappe.request.get_json() or {}
    employee = (data.get('employee') or '').strip()
    greenhouse = (data.get('greenhouse') or '').strip()
    action = (data.get('action') or '').strip()
    user = frappe.session.user
    is_admin = user == 'Administrator'
    role_rows = frappe.get_all('Has Role', filters={'parent': user, 'role': 'Production Section Head'}, fields=['name'], limit=1)
    try:
        if not (role_rows or is_admin):
            frappe.response['data'] = {'error': "Role 'Production Section Head' required to manage growers."}
        elif not employee or not greenhouse:
            frappe.response['data'] = {'error': 'employee and greenhouse are required.'}
        elif action not in ('add', 'remove'):
            frappe.response['data'] = {'error': "action must be 'add' or 'remove'."}
        elif not frappe.db.exists('Employee', employee):
            frappe.response['data'] = {'error': 'Employee not found: ' + employee}
        elif not frappe.db.exists('Warehouse', greenhouse):
            frappe.response['data'] = {'error': 'Greenhouse not found: ' + greenhouse}
        else:
            existing = frappe.get_all('Employee Greenhouse', filters={'parent': employee, 'parenttype': 'Employee', 'greenhouse': greenhouse}, fields=['name'], limit=1)
            if action == 'add':
                if existing:
                    frappe.response['data'] = {'status': 'exists', 'message': 'Grower already linked to this greenhouse.'}
                else:
                    row = frappe.get_doc({'doctype': 'Employee Greenhouse', 'parenttype': 'Employee', 'parentfield': 'custom_greenhouses', 'parent': employee, 'greenhouse': greenhouse})
                    row.insert(ignore_permissions=True)
                    frappe.db.commit()
                    frappe.response['data'] = {'status': 'added'}
            else:
                if existing:
                    frappe.delete_doc('Employee Greenhouse', existing[0].get('name'), ignore_permissions=True)
                    frappe.db.commit()
                    frappe.response['data'] = {'status': 'removed'}
                else:
                    frappe.response['data'] = {'status': 'missing', 'message': 'Grower was not linked to this greenhouse.'}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error('productionSetGreenhouseGrower error: ' + str(e))
        frappe.response['data'] = {'error': str(e)}


@frappe.whitelist()
def productionUpdatePlanTask():
    data = frappe.request.get_json() or {}
    plan = (data.get('plan') or '').strip()
    task = (data.get('task') or '').strip()
    status = (data.get('status') or '').strip()
    completion_note = (data.get('completion_note') or '').strip()

    if not plan:
        frappe.response['data'] = {'error': 'plan is required.'}
    elif not task:
        frappe.response['data'] = {'error': 'task is required.'}
    elif status not in ['Open', 'In Progress', 'Done', 'Skipped']:
        frappe.response['data'] = {'error': 'status must be one of Open, In Progress, Done, Skipped.'}
    else:
        try:
            doc = frappe.get_doc('Production Plan Form', plan)
            row = None
            for t in (doc.tasks or []):
                if t.name == task:
                    row = t
            if not row:
                frappe.response['data'] = {'error': 'Task ' + task + ' not found on ' + plan + '.'}
            else:
                row.status = status
                now = frappe.utils.now()
                if status == 'In Progress' and not row.started_at:
                    row.started_at = now
                if status in ['Done', 'Skipped']:
                    row.completed_at = now
                if status == 'Open':
                    row.completed_at = None
                if completion_note:
                    row.completion_note = completion_note
                doc.save(ignore_permissions=True)
                frappe.db.commit()
                frappe.response['data'] = {
                    'status': 'success',
                    'name': plan,
                    'task': task,
                    'message': (row.task_name or 'Task') + ' marked ' + status + '.',
                }
        except Exception as e:
            frappe.db.rollback()
            frappe.log_error('productionUpdatePlanTask error: ' + str(e))
            frappe.response['data'] = {'error': str(e)}


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
            "device_id", "device_name", "model", "brand", "os", "os_version",
            "app_name", "app_version", "build", "ota_update_id", "ota_channel", "battery_level",
            "battery_state", "network_type", "cellular_generation",
            "storage_free", "storage_total", "user", "user_full_name", "captured_at",
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

    doc = frappe.get_doc({
        "doctype": "Stock Entry",
        "stock_entry_type": "Field Rejects",
        "posting_date": frappe.utils.today(),
        "posting_time": frappe.utils.nowtime(),
        "company": "Karen Roses",
        "custom_farm": farm,
        "items": [
            {
                "s_warehouse": greenhouse,
                "t_warehouse": "Rejects - KR",
                "item_code": variety,
                "qty": qty,
                "custom_rejection_reason": rejection_reason,
            }
        ]
    })

    doc.insert()
    doc.submit()

    frappe.response["message"] = {
        "status": "success",
        "name": doc.name,
        "message": "Field rejection recorded successfully"
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

    updated = 0
    skipped = 0
    for entry in weeks:
        row_name = entry.get("name")
        row = week_map.get(row_name)
        if not row:
            skipped = skipped + 1
            continue
        changed = 0
        if "revised_forecast" in entry:
            row.revised_forecast_stems = frappe.utils.cint(entry.get("revised_forecast"))
            changed = 1
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
    frappe.response["message"] = {"status": "success", "updated": updated, "message": msg}
