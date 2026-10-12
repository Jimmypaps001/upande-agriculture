"""One-off maintenance helpers, run manually via `bench execute` -- not
scheduled, not called from the app. Kept here (rather than deleted after use)
so the fix is documented and repeatable on staging/production.
"""

import frappe




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


