"""One-off maintenance helpers, run manually via `bench execute` -- not
scheduled, not called from the app. Kept here (rather than deleted after use)
so the fix is documented and repeatable on staging/production.
"""

import frappe


def add_receiving_entry_index():
    """Fixes the Connections tab showing "?" for Stock Entry on Harvesting/
    Grading entries.

    Root cause: custom_receiving_entry (the Link field the Connections tab
    filters Stock Entry by -- see stock_entry_connections.py) was never
    marked as indexed. Stock Entry is the single largest table in this
    instance (3.3M+ rows, shared with every other stock-moving module), so
    the dashboard's `SELECT ... WHERE custom_receiving_entry = %s LIMIT 100`
    full-scans it on every page load and hits Frappe's per-request statement
    timeout -- which is exactly the "?" (see frappe.desk.notifications.
    get_doc_count: it returns "?" only on a caught statement timeout).

    custom_bucket_id (used by getBucketStatus) was already marked indexed,
    which is why that lookup was never affected by this.
    """
    cf_name = frappe.db.get_value(
        "Custom Field",
        {"dt": "Stock Entry", "fieldname": "custom_receiving_entry"},
        "name",
    )
    if not cf_name:
        print("custom_receiving_entry Custom Field not found -- nothing to do")
        return

    # Raw db.set_value rather than doc.save(): Stock Entry has an unrelated,
    # pre-existing broken Link field elsewhere on the doctype (eTIMS Queue
    # Entry, options pointing at a doctype that doesn't validate) that makes
    # the full Custom Field controller validation blow up on save(). That's
    # a separate, pre-existing data-quality issue -- not touching it here.
    already = frappe.db.get_value("Custom Field", cf_name, "search_index")
    if already:
        print("already indexed")
    else:
        frappe.db.set_value("Custom Field", cf_name, "search_index", 1, update_modified=False)
        frappe.clear_cache(doctype="Stock Entry")
        print("Custom Field updated, search_index=1")

    # updatedb() only diffs/alters the DB schema (columns + indexes) -- it
    # doesn't run the DocType controller's link-target validation, so the
    # unrelated eTIMS issue above doesn't block it.
    frappe.db.updatedb("Stock Entry")
    frappe.db.commit()

    rows = frappe.db.sql(
        "SHOW INDEX FROM `tabStock Entry` WHERE Column_name='custom_receiving_entry'"
    )
    print("index present:" if rows else "index STILL MISSING:", rows)


def remove_duplicate_stock_entry_link():
    """Removes the stale static "Production" group Connections entry for
    Stock Entry -> Stock Entry via custom_receiving_entry.

    This was a leftover from before the Harvesting/Grading<->Receiving
    relationship moved to the dynamic upande_agriculture.
    stock_entry_connections.get_dashboard_data override (see hooks.py /
    override_doctype_dashboards, and the comment left in upande_packhouse's
    hooks.py noting the move). The static DocType Link entry was never
    deleted, so the Connections tab has been showing "Stock Entry" twice --
    once as "Production" (this stale entry) and once as "Roses Production"
    (the dynamic hook) -- for the exact same relationship.
    """
    rows = frappe.get_all(
        "DocType Link",
        filters={
            "parent": "Stock Entry",
            "parenttype": "Customize Form",
            "link_doctype": "Stock Entry",
            "link_fieldname": "custom_receiving_entry",
        },
        fields=["name", "group"],
    )
    print("found:", rows)
    for row in rows:
        frappe.delete_doc("DocType Link", row["name"], ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="Stock Entry")
    frappe.db.commit()
    print("removed", len(rows), "duplicate link(s)")


def fix_generate_bucket_codes_script():
    """Guards the legacy "Generate Bucket Codes" Client Script's after_save
    so it only fires for the action types it actually builds args for.
    Previously it ran unconditionally on every Label Print save, including
    action="Bunch Label Table", where it called generate_id() with no
    matching branch -- a harmless but confusing no-op reporting fake
    success while doing nothing.
    """
    script = 'frappe.ui.form.on(\'Label Print\', {\n\tafter_save(frm) {\n\t    // Guard: this legacy single-shot flow only builds args for these three\n\t    // action types (see below) -- calling it for "Bunch Label Table" (handled\n\t    // instead by the "Bunch Label Table Autofill" script\'s own after_save via\n\t    // generate_batch_table_labels) or "Shelf Label" hit generate_id() with no\n\t    // matching branch: a harmless but confusing no-op that still reported\n\t    // "Label created Successfully" despite doing nothing.\n\t    const handled_actions = [\'Harvesting Label\', \'Bunch Label\', \'Grader Label\'];\n\t    if (!handled_actions.includes(frm.doc.action)) {\n\t        return;\n\t    }\n\n\t    let variety;\n\t    let number_of_labels;\n\t    let doc_name;\n\t    let action;\n\t    let farm;\n\t    let stem_length;\n\t    let bunch_size, grader, day_code;\n\t    \n\t    let args = {\n\t        action : frm.doc.action,\n\t        label_doc_name: frm.doc.name,\n\t        \n\t    };\n\t    \n\t    if (frm.doc.action === \'Harvesting Label\') {\n\t        args.variety = frm.doc.item_code;\n    \t\targs.no_of_labels = frm.doc.number_of_labels;\n\n\t    } else if (frm.doc.action === \'Bunch Label\') {\n\t        args.variety = frm.doc.variety;\n\t        args.no_of_labels = frm.doc.no_of_labels;\n\t        args.farm = frm.doc.farm;\n\t        args.stem_length = frm.doc.stem_length;\n\t        args.bunch_size = frm.doc.bunch_size;\n\t        args.farm_code = frm.doc.farm_code;\n\t        \n\t    } else if (frm.doc.action === \'Grader Label\') {\n\t        args.grader = frm.doc.grader;\n\t        args.no_of_labels = frm.doc.qty_of_labels;\n\t        args.day_code = frm.doc.day_code;\n\t    }\n\t    \n\t    \n\t\t\n\t\tlet bucket_id;\n\t\t\n\t\tfrappe.call({\n\t\t    method: "upande_packhouse.server_scripts.gen_label_id.generate_id",\n\t\t    args: args,\n\t\t    callback:(response) => {\n\t\t        console.log(response);\n\t\t    }\n\t\t});\n\t\t\n\t}\n})'
    frappe.db.set_value("Client Script", "Generate Bucket Codes", "script", script)
    frappe.db.commit()
    print("Client Script updated")


def check_harvest_item_group_config():
    """Read-only check: does any Harvest Item Group Config data already
    exist for Production Settings, and does the Production Settings
    doctype currently have a field to edit it through the Desk UI?
    """
    import frappe

    rows = frappe.get_all(
        "Harvest Item Group Config",
        filters={"parenttype": "Production Settings"},
        fields=["name", "parent", "item_group", "max_stems_per_bucket"],
    )
    print("existing rows:", rows)

    meta = frappe.get_meta("Production Settings")
    fieldnames = [df.fieldname for df in meta.fields]
    print("Production Settings fields:", fieldnames)
    print("has harvest_item_group_config field:",
          any(df.fieldname == "harvest_item_group_config" for df in meta.fields))


def reload_production_settings():
    """Syncs the newly-added harvest_item_group_config Table field (see
    production_settings.json) onto the live schema."""
    import frappe

    frappe.reload_doctype("Production Settings", force=True)
    frappe.clear_cache(doctype="Production Settings")
    frappe.db.commit()
    meta = frappe.get_meta("Production Settings")
    print("has field now:", any(df.fieldname == "harvest_item_group_config" for df in meta.fields))


def seed_test_harvest_limits():
    """Adds a low, obviously-test-only cap for Standard Roses and Spray
    Roses so the mobile validation can be exercised end-to-end."""
    import frappe

    doc = frappe.get_single("Production Settings")
    doc.set("harvest_item_group_config", [])
    doc.append("harvest_item_group_config", {"item_group": "Standard Roses", "max_stems_per_bucket": 5})
    doc.append("harvest_item_group_config", {"item_group": "Spray Roses", "max_stems_per_bucket": 5})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    print("seeded")
    for r in doc.harvest_item_group_config:
        print(" -", r.item_group, r.max_stems_per_bucket)


def cleanup_stem_limit_test_data():
    """Removes every artifact created while testing the restored
    Harvest Item Group Config field, and clears the test-only 5-stem caps
    so Production Settings is left empty and ready for real values."""
    import frappe

    buckets = [
        "TEST-LIMIT-STD-01", "TEST-LIMIT-STD-02", "TEST-LIMIT-STD-03",
        "TEST-LIMIT-SPRAY-01", "TEST-LIMIT-SPRAY-02", "TEST-LIMIT-SPRAY-03", "TEST-LIMIT-SPRAY-04",
    ]
    ses = frappe.get_all(
        "Stock Entry",
        filters={"custom_bucket_id": ["in", buckets]},
        fields=["name", "docstatus"],
    )
    for se in ses:
        if se.docstatus == 1:
            frappe.get_doc("Stock Entry", se.name).cancel()
        frappe.delete_doc("Stock Entry", se.name, ignore_permissions=True, force=True)
    print("removed", len(ses), "test Stock Entries")

    for b in buckets:
        if frappe.db.exists("Bucket QR Code", b):
            frappe.delete_doc("Bucket QR Code", b, ignore_permissions=True, force=True)
    print("removed test buckets")

    doc = frappe.get_single("Production Settings")
    doc.set("harvest_item_group_config", [])
    doc.save(ignore_permissions=True)
    print("cleared test caps -- harvest_item_group_config is now empty")

    frappe.db.commit()


def cleanup_amend_test_data():
    """Removes every Stock Entry / Bucket QR Code created while testing
    amendHarvestEntry (standards + sprays, including cancel/amend chains
    and the one Receiving entry created along the way). Idempotent -- safe
    to re-run against a partially-cleaned state."""
    import frappe

    buckets = ["TEST-AMEND-STD-01", "TEST-AMEND-SPRAY-01", "TEST-AMEND-SPRAY-02"]
    receiving_entries = ["MAT-STE-2026-100006902"]

    names = set(frappe.get_all(
        "Stock Entry", filters={"custom_bucket_id": ["in", buckets]}, pluck="name"
    ))
    names.update(n for n in receiving_entries if frappe.db.exists("Stock Entry", n))

    # Clear the custom_receiving_entry link on every Harvesting/Grading entry
    # first -- a Receiving entry can't be cancelled/deleted while something
    # still links to it (LinkExistsError), and that link is exactly what
    # this test data is riddled with by design.
    frappe.db.set_value(
        "Stock Entry",
        {"name": ["in", list(names)], "custom_receiving_entry": ["in", receiving_entries]},
        "custom_receiving_entry",
        None,
        update_modified=False,
    )
    frappe.db.commit()

    docs = frappe.get_all("Stock Entry", filters={"name": ["in", list(names)]}, fields=["name", "docstatus"])
    for d in docs:
        if d.docstatus == 1:
            frappe.get_doc("Stock Entry", d.name).cancel()
    for d in docs:
        if frappe.db.exists("Stock Entry", d.name):
            frappe.delete_doc("Stock Entry", d.name, ignore_permissions=True, force=True)
    print("removed", len(docs), "test Stock Entries")

    for b in buckets:
        if frappe.db.exists("Bucket QR Code", b):
            frappe.delete_doc("Bucket QR Code", b, ignore_permissions=True, force=True)
    print("removed test buckets")

    frappe.db.commit()


def add_harvest_grading_link_fields():
    """Adds the two real link fields connecting a spray's Harvesting entry
    to its Grading entry (and back) -- custom_harvest_batch_no was meant to
    do this but is not a real persisted column (confirmed earlier), so no
    stored link between the two has ever existed. amendHarvestEntry needs
    custom_grading_entry to find and correct the Grading side too.

    ignore_links=True on insert(): Stock Entry has an unrelated, pre-existing
    broken Link field elsewhere (eTIMS Queue Entry) that makes full link
    validation blow up for ANY Custom Field insert on this doctype -- same
    issue worked around in add_receiving_entry_index.
    """
    import frappe

    specs = [
        {
            "fieldname": "custom_grading_entry",
            "label": "Grading Entry",
            "description": "Spray Roses only: the Grading entry this Harvesting entry fed. Set automatically at grading time; not user-editable.",
            "insert_after": "custom_cut_stage",
        },
        {
            "fieldname": "custom_harvest_entry",
            "label": "Harvest Entry",
            "description": "Spray Roses only: the Harvesting entry this Grading entry came from. Set automatically at grading time; not user-editable.",
            "insert_after": "custom_grading_entry",
        },
    ]

    for spec in specs:
        if frappe.db.exists("Custom Field", "Stock Entry-" + spec["fieldname"]):
            print(spec["fieldname"], "already exists")
            continue
        doc = frappe.get_doc({
            "doctype": "Custom Field",
            "dt": "Stock Entry",
            "fieldname": spec["fieldname"],
            "label": spec["label"],
            "fieldtype": "Link",
            "options": "Stock Entry",
            "insert_after": spec["insert_after"],
            "read_only": 1,
            "search_index": 1,
            "description": spec["description"],
        })
        # Full insert() re-validates the WHOLE Stock Entry doctype schema
        # (every Link field's options, not just this one), and Stock Entry
        # has an unrelated pre-existing broken Link field elsewhere (eTIMS
        # Queue Entry) that blows that up -- same issue worked around in
        # add_receiving_entry_index. db_insert() writes the row directly,
        # skipping validate() entirely.
        doc.db_insert()
        print("created", spec["fieldname"])

    frappe.clear_cache(doctype="Stock Entry")
    frappe.db.updatedb("Stock Entry")
    frappe.db.commit()

    rows = frappe.db.sql(
        "SHOW INDEX FROM `tabStock Entry` WHERE Column_name IN ('custom_grading_entry', 'custom_harvest_entry')"
    )
    print("indexes present:", rows)


def add_forecast_actuals_indexes():
    """Speeds up actual_week_totals()'s fallback query (production_forecast.py)
    -- the "Actual Harvest" doctype it prefers doesn't exist anywhere in this
    codebase, so every Harvesting Stock Entry submit/cancel always takes the
    fallback path: a JOIN across Stock Entry (3.3M+ rows) x Stock Entry Detail
    filtered by stock_entry_type/docstatus/posting_date (parent) and
    item_code/t_warehouse (child) -- none of which had a matching index,
    so this JOIN forced a broad scan on every single Harvesting submit/cancel
    (confirmed via cProfile against a real cancel(): ~5.5s of the ~20s it took).

    Composite indexes (not a single search_index=1 flag) -- add_index()
    only persists a Property Setter for single-field indexes, so unlike the
    earlier single-field fixes these two need to be re-applied on any other
    site (staging/production) rather than surviving a plain `bench migrate`.
    """
    import frappe

    frappe.db.add_index("Stock Entry", ["stock_entry_type", "docstatus", "posting_date"])
    frappe.db.add_index("Stock Entry Detail", ["item_code", "t_warehouse"])
    frappe.db.commit()

    print(frappe.db.sql("SHOW INDEX FROM `tabStock Entry` WHERE Key_name LIKE '%stock_entry_type%'"))
    print(frappe.db.sql("SHOW INDEX FROM `tabStock Entry Detail` WHERE Key_name LIKE '%item_code%'"))


def find_unindexed_stock_entry_links():
    """Lists every Link/Dynamic Link field across the whole install pointing
    at Stock Entry, with its search_index flag and owning table's row count
    -- to find which of the 33 checks in check_no_back_links_exist (~442ms
    avg, ~14.6s total per cancel/delete of a Stock Entry) are the expensive
    ones: an unindexed field on a big table.
    """
    import frappe
    from frappe.model.rename_doc import get_link_fields

    link_fields = get_link_fields("Stock Entry")
    print(f"{len(link_fields)} link fields found")

    rows = []
    for lf in link_fields:
        dt, fieldname, issingle = lf["parent"], lf["fieldname"], lf["issingle"]
        if issingle:
            rows.append((dt, fieldname, "single", "-", "-"))
            continue
        try:
            meta = frappe.get_meta(dt)
        except Exception:
            rows.append((dt, fieldname, "?", "no meta", "-"))
            continue
        df = meta.get_field(fieldname)
        indexed = bool(df.search_index) if df else "?"
        try:
            count = frappe.db.count(dt)
        except Exception:
            count = "?"
        rows.append((dt, fieldname, indexed, count, meta.istable))

    rows.sort(key=lambda r: (r[3] if isinstance(r[3], int) else -1), reverse=True)
    for r in rows:
        print(f"dt={r[0]!r:40s} field={r[1]!r:30s} indexed={r[2]!s:6s} rows={r[3]!s:10s} child_table={r[4]}")


def add_stock_entry_backlink_indexes():
    """Indexes 3 core ERPNext fields on Stock Entry itself, found via
    find_unindexed_stock_entry_links: source_stock_entry, amended_from, and
    outgoing_stock_entry -- all unindexed against 3.3M+ rows. Every single
    Stock Entry cancel (or delete) runs check_no_back_links_exist, which
    checks EVERY doctype+field that can link to Stock Entry for existing
    references -- these three, each an unindexed equality lookup against
    the whole table, accounted for the bulk of the ~14.6s that step took
    (confirmed via cProfile against a real cancel()).

    Pure schema addition -- no ERPNext app code is touched, just a DB index.
    Single-field indexes via add_index() persist their own Property Setter
    (search_index=1), so unlike the composite indexes in
    add_forecast_actuals_indexes, these survive a plain `bench migrate`.
    """
    import frappe
    from frappe.custom.doctype.property_setter.property_setter import make_property_setter

    for field in ["source_stock_entry", "amended_from", "outgoing_stock_entry"]:
        index_name = frappe.db.get_index_name([field])
        if not frappe.db.has_index("tabStock Entry", index_name):
            frappe.db.commit()
            frappe.db.sql(f"ALTER TABLE `tabStock Entry` ADD INDEX IF NOT EXISTS `{index_name}` (`{field}`)")

        # add_index()'s own make_property_setter() call defaults
        # validate_fields_for_doctype=True, which re-validates the WHOLE
        # doctype's Link fields -- same eTIMS Queue Entry issue worked
        # around elsewhere in this file. Skip that re-validation explicitly;
        # it has nothing to do with the field being indexed here.
        already = frappe.db.exists(
            "Property Setter", {"doc_type": "Stock Entry", "field_name": field, "property": "search_index"}
        )
        if not already:
            make_property_setter(
                "Stock Entry", field, property="search_index", value="1",
                property_type="Check", for_doctype=False,
                validate_fields_for_doctype=False,
            )
    frappe.clear_cache(doctype="Stock Entry")
    frappe.db.commit()

    rows = frappe.db.sql(
        "SHOW INDEX FROM `tabStock Entry` WHERE Column_name IN "
        "('source_stock_entry', 'amended_from', 'outgoing_stock_entry')"
    )
    print(rows)


def cleanup_cascade_test_data():
    import frappe

    buckets = ["TEST-CASCADE-SPRAY-01", "TEST-CASCADE-SPRAY-02", "TEST-REGRESS-STD-01"]
    names = set(frappe.get_all("Stock Entry", filters={"custom_bucket_id": ["in", buckets]}, pluck="name"))
    # Pull in any Receiving entry these harvests were claimed by too, and
    # anything else in that Receiving entry's own claim group -- all of it
    # needs its cross-links cleared before any cancel/delete can proceed.
    receiving = set(frappe.get_all("Stock Entry", filters={"name": ["in", list(names)], "custom_receiving_entry": ["is", "set"]}, pluck="custom_receiving_entry"))
    names.update(receiving)

    # Clear reciprocal links first so nothing blocks cancel/delete.
    frappe.db.set_value("Stock Entry", {"name": ["in", list(names)]}, "custom_grading_entry", None, update_modified=False)
    frappe.db.set_value("Stock Entry", {"name": ["in", list(names)]}, "custom_harvest_entry", None, update_modified=False)
    frappe.db.set_value("Stock Entry", {"name": ["in", list(names)]}, "custom_receiving_entry", None, update_modified=False)
    frappe.db.commit()

    docs = frappe.get_all("Stock Entry", filters={"name": ["in", list(names)]}, fields=["name", "docstatus"])
    for d in docs:
        if d.docstatus == 1:
            frappe.get_doc("Stock Entry", d.name).cancel()
    for d in docs:
        if frappe.db.exists("Stock Entry", d.name):
            frappe.delete_doc("Stock Entry", d.name, ignore_permissions=True, force=True)
    print("removed", len(docs), "test Stock Entries")

    for b in buckets:
        if frappe.db.exists("Bucket QR Code", b):
            frappe.delete_doc("Bucket QR Code", b, ignore_permissions=True, force=True)
    print("removed test buckets")
    frappe.db.commit()
