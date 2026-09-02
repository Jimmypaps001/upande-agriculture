// Crop Cycle — bed-range feedback before saving, and a way to push the
// cycle into a budget without leaving the form.

frappe.ui.form.on("Crop Cycle", {
    setup(frm) {
        // Only submitted invoices carry a price you can trust.
        frm.set_query("purchase_invoice", () => ({ filters: { docstatus: 1 } }));
        // A crop cycle only ever lives in a greenhouse, never any other warehouse type.
        frm.set_query("greenhouse", () => ({ filters: { warehouse_type: "Greenhouse" } }));
        // A protocol for a different variety would silently mis-model this planting.
        frm.set_query("crop_protocol", () => ({ filters: { variety_item: frm.doc.variety } }));
    },

    refresh(frm) {
        bed_range_hint(frm);
        cost_hint(frm);
        if (frm.is_new()) return;

        if (frm.doc.status === "Ended") {
            frm.add_custom_button(__("Replant"), () => replant(frm), __("Actions"));
        } else {
            frm.add_custom_button(__("Uproot"), () => uproot(frm), __("Actions"));
            frm.add_custom_button(__("Uproot Beds…"), () => uproot_beds(frm), __("Actions"));
        }
        frm.add_custom_button(__("Generate Budget"), () => ask_year(frm), __("Actions"));
        frm.add_custom_button(__("View Budgets"), () => {
            frappe.set_route("List", "Production Projection", {
                greenhouse: frm.doc.greenhouse, variety: frm.doc.variety,
            });
        }, __("Actions"));
    },

    validate: preflight_gate,

    bed_range: bed_range_hint,
    plants_per_sqm(frm) { bed_range_hint(frm); show_density(frm); },
    qty_planted(frm) { show_density(frm); cost_hint(frm); },
    purchase_invoice(frm) {
        if (!frm.doc.purchase_invoice) {
            // Unlinking hands the fields back rather than stranding stale values.
            frm.set_value({ invoiced_qty: 0, breeder: null, cost_per_plant: 0 });
        }
        cost_hint(frm);
    },
    cost_per_plant: cost_hint,
    beds_add: show_density,
    beds_remove: show_density,
});

// Saving over occupied or missing beds shouldn't be a dead end: preflight
// asks the server what's in the way, lays it out in one dialog, and on
// Proceed does the uproots / bed creation and re-runs the save. Validation
// server-side stays exactly as strict — this only clears the path first.
function preflight_gate(frm) {
    if (frm.__bed_prep_done) {
        frm.__bed_prep_done = false;
        return;
    }
    if (!frm.doc.bed_range || !frm.doc.greenhouse) return;

    return frappe
        .call({
            method: "upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle.bed_range_preflight",
            args: {
                greenhouse: frm.doc.greenhouse,
                bed_range: frm.doc.bed_range,
                new_variety: frm.doc.variety,
                exclude_cycle: frm.doc.__islocal ? null : frm.doc.name,
            },
        })
        .then(({ message: r }) => {
            if (!r) return;
            const anything = r.missing.length || r.conflicts.length || r.stale.length;
            if (!anything) return;
            frappe.validated = false;
            show_preflight_dialog(frm, r);
        });
}

function show_preflight_dialog(frm, r) {
    const lines = [];
    for (const c of r.conflicts) {
        lines.push(__("Beds <b>{0}</b> carry <b>{1}</b> ({2}) — <b>{3}</b> standing plants will be uprooted.",
            [c.beds_label, c.variety, c.cycle, cint(c.plants).toLocaleString()]));
    }
    for (const s of r.stale) {
        lines.push(__("Bed <b>{0}</b> still shows <b>{1}</b> on the greenhouse ledger with no active crop cycle — it will be cleared.",
            [s.bed, s.variety]));
    }
    if (r.missing.length) {
        lines.push(__("Beds <b>{0}</b> don't exist in {1} yet — they will be created with the dimensions below.",
            [r.missing_label, frm.doc.greenhouse]));
    }

    const fields = [{
        fieldtype: "HTML", fieldname: "summary",
        options: "<ul style='padding-left:1.2em'><li>" + lines.join("</li><li>") + "</li></ul>",
    }];
    if (r.missing.length) {
        fields.push(
            { fieldtype: "Float", fieldname: "bed_length", label: __("Bed Length (m)"),
              default: r.default_length, reqd: 1 },
            { fieldtype: "Column Break", fieldname: "cb1" },
            { fieldtype: "Float", fieldname: "bed_width", label: __("Bed Width (m)"),
              default: r.default_width, reqd: 1 },
        );
    }

    const d = new frappe.ui.Dialog({
        title: __("This planting needs the ground prepared"),
        fields: fields,
        primary_action_label: __("Proceed"),
        primary_action(values) {
            d.hide();
            frappe.call({
                method: "upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle.prepare_bed_range",
                args: {
                    greenhouse: frm.doc.greenhouse,
                    bed_range: frm.doc.bed_range,
                    new_variety: frm.doc.variety,
                    exclude_cycle: frm.doc.__islocal ? null : frm.doc.name,
                    planting_date: frm.doc.planting_date,
                    bed_length: values.bed_length,
                    bed_width: values.bed_width,
                },
                freeze: true,
                freeze_message: __("Preparing beds…"),
                callback: ({ message: res }) => {
                    if (!res) return;
                    const bits = [];
                    if (res.beds_created) bits.push(__("{0} bed(s) created", [res.beds_created]));
                    for (const u of res.uprooted) {
                        bits.push(__("{0} uprooted on {1}{2}",
                            [u.cycle, u.beds, u.ended ? __(" (cycle ended)") : ""]));
                    }
                    if (res.stale_cleared) bits.push(__("{0} stale ledger bed(s) cleared", [res.stale_cleared]));
                    if (bits.length) frappe.show_alert({ message: bits.join("<br>"), indicator: "orange" }, 8);
                    frm.__bed_prep_done = true;
                    frm.save();
                },
            });
        },
        secondary_action_label: __("Cancel"),
    });
    d.show();
}

frappe.ui.form.on("Crop Cycle Bed", {
    bed: show_density,
    beds_remove: show_density,
});

// Area and density are computed server-side on save, but a grower typing a
// plant count needs to see the number now, not after a round trip.
function show_density(frm) {
    const area = (frm.doc.beds || []).reduce((t, b) => t + (b.bed_area || 0), 0);
    if (area) frm.set_value("planted_area", area);

    const qty = frm.doc.qty_planted;
    if (!area || !qty) {
        frm.set_df_property("plants_per_sqm", "description", "");
        return;
    }
    // Blank density means "work it out from what I planted".
    const density = frm.doc.plants_per_sqm || qty / area;
    if (!frm.doc.plants_per_sqm) frm.set_value("plants_per_sqm", Math.round(density * 100) / 100);
    frm.set_value("implied_plants", Math.round(area * density));

    const bad = density < 0.5 || density > 30;
    const colour = bad ? "var(--red-500)" : "var(--text-muted)";
    const note = bad
        ? " &mdash; cut flowers run about 6-8. This will save with a warning."
        : "";
    frm.set_df_property("plants_per_sqm", "description",
        `<span style="color:${colour}">${qty.toLocaleString()} plants over `
        + `${area.toLocaleString(undefined, {maximumFractionDigits: 0})} m² `
        + `= <b>${density.toFixed(1)} plants/m²</b>${note}</span>`);
}

// Counting the beds client-side means the grower sees "1-50 -> 50 beds"
// while typing, instead of finding out on save.
function bed_range_hint(frm) {
    const spec = (frm.doc.bed_range || "").replace(/[–—]/g, "-");
    if (!spec.trim()) {
        frm.set_df_property("bed_range", "description",
            "Type the beds: <b>1-50</b>, or <b>1-20, 31-40</b> for split blocks. "
            + "The table below fills itself on save.");
        return;
    }
    const numbers = new Set();
    let bad = false;
    for (const chunk of spec.split(",")) {
        const part = chunk.trim();
        if (!part) continue;
        const m = part.match(/^(\d+)\s*-\s*(\d+)$/);
        if (m) {
            let [lo, hi] = [parseInt(m[1]), parseInt(m[2])];
            if (lo > hi) [lo, hi] = [hi, lo];
            for (let i = lo; i <= hi; i++) numbers.add(i);
        } else if (/^\d+$/.test(part)) {
            numbers.add(parseInt(part));
        } else {
            bad = true;
        }
    }
    if (bad) {
        frm.set_df_property("bed_range", "description",
            "<span style='color:var(--red-500)'>Can't read that. Use <b>1-50</b> "
            + "or <b>1-20, 31-40</b>.</span>");
        return;
    }
    const n = numbers.size;
    const density = frm.doc.plants_per_sqm;
    let msg = `<b>${n}</b> bed${n === 1 ? "" : "s"} — filled on save.`;
    if (density) {
        msg += ` Plants implied by the beds appear once saved (area × ${density}/m²).`;
    }
    frm.set_df_property("bed_range", "description", msg);
}

// Cost per plant only means something next to how many were planted, and the
// invoiced count is the honest cross-check on qty_planted.
function cost_hint(frm) {
    const rate = frm.doc.cost_per_plant;
    const qty = frm.doc.qty_planted;
    if (!rate || !qty) {
        frm.set_df_property("cost_per_plant", "description", "");
        return;
    }
    const total = rate * qty;
    const cur = frappe.defaultCurrency || "";
    let msg = `${qty.toLocaleString()} plants x ${rate} = <b>${cur} `
        + `${total.toLocaleString(undefined, { maximumFractionDigits: 0 })}</b>`;

    const billed = frm.doc.invoiced_qty;
    if (billed && Math.abs(billed - qty) / billed > 0.05) {
        msg += `<br><span style="color:var(--orange-500)">Invoice bills `
            + `${billed.toLocaleString()} plants but ${qty.toLocaleString()} were `
            + `planted — check which is right.</span>`;
    }
    frm.set_df_property("cost_per_plant", "description", msg);
}

// Uprooting is two fields that must move together: without the date the
// projection keeps cutting stems off a block that is out of the ground.
function uproot(frm) {
    frappe.prompt(
        [{
            fieldname: "date", fieldtype: "Date", label: __("Uprooted On"),
            default: frm.doc.planned_uprooting_date || frappe.datetime.get_today(),
            reqd: 1,
            description: __("Production is budgeted right up to this date."),
        }],
        ({ date }) => {
            frm.set_value({ cycle_end_date: date, status: "Ended" });
            frm.save().then(() => frappe.show_alert({
                message: __("Uprooted. Regenerate the budget to stop production here."),
                indicator: "orange",
            }, 7));
        },
        __("Uproot Block"), __("Uproot"),
    );
}

// A partial uproot is one bed range coming out, not the whole block — logged
// as a row rather than ending the cycle. The server validates it against
// what's actually still standing on those beds; this dialog just collects
// the same three things the "Uproot Block" one does.
function uproot_beds(frm) {
    frappe.prompt(
        [{
            fieldname: "date", fieldtype: "Date", label: __("Uprooted On"),
            default: frappe.datetime.get_today(), reqd: 1,
        }, {
            fieldname: "bed_range", fieldtype: "Data", label: __("Beds"),
            reqd: 1,
            description: __("e.g. 12-18, or 12-18.5 for a half bed."),
        }, {
            fieldname: "plants", fieldtype: "Int", label: __("Plants Removed"), reqd: 1,
        }],
        (v) => {
            frm.add_child("uproot_log", {
                uproot_date: v.date, bed_range: v.bed_range, plants: v.plants,
            });
            frm.save().then(() => frappe.show_alert({
                message: __("Logged. {0} plants removed from {1}.", [v.plants, v.bed_range]),
                indicator: "orange",
            }, 7));
        },
        __("Uproot Beds"), __("Uproot"),
    );
}

// The replacement is the same beds at the same density — everything except
// when it went in. copy_doc gives us that for free; we only clear the dates
// that belonged to the block that just came out.
function replant(frm) {
    const doc = frappe.model.copy_doc(frm.doc);
    doc.replaces = frm.doc.name;
    doc.status = "Planned";
    doc.purchase_invoice = null;
    doc.invoiced_qty = 0;
    for (const f of ["planting_date", "first_bending_date", "second_bending_date",
                     "planned_uprooting_date", "cycle_end_date"]) {
        doc[f] = null;
    }
    frappe.set_route("Form", "Crop Cycle", doc.name);
    frappe.show_alert(__("Set the planting date — the rest carried over from {0}.",
                         [frm.doc.name]), 7);
}

function ask_year(frm) {
    const planting = frm.doc.planting_date
        ? frappe.datetime.str_to_obj(frm.doc.planting_date).getFullYear()
        : new Date().getFullYear();
    frappe.prompt(
        [{
            fieldname: "year", fieldtype: "Int", label: __("Budget Year"),
            default: new Date().getFullYear(), reqd: 1,
            description: __("This block was planted in {0}.", [planting]),
        }, {
            fieldname: "overwrite_manual", fieldtype: "Check",
            label: __("Overwrite locked and hand-edited weeks"), default: 0,
            description: __("Off by default — a planner's edits survive a regenerate."),
        }],
        (v) => run_budget(frm, v),
        __("Generate Budget"), __("Generate"),
    );
}

function run_budget(frm, values) {
    frappe.call({
        method: "upande_agriculture.budget.generate_budget",
        args: {
            greenhouse: frm.doc.greenhouse,
            variety: frm.doc.variety,
            year: values.year,
            overwrite_manual: values.overwrite_manual ? 1 : 0,
        },
        freeze: true,
        freeze_message: __("Building the 52-week budget…"),
        callback: ({ message: r }) => {
            if (!r) return;
            const preserved = r.weeks_preserved
                ? `<br>${r.weeks_preserved} locked week(s) left untouched.` : "";
            frappe.msgprint({
                title: __("Budget built"),
                indicator: "green",
                message: __(
                    "<b>{0}</b> stems across {1} week(s), from {2} crop cycle(s)."
                    + "<br><br><a href='/app/production-projection/{3}'>{3}</a>{4}",
                    [frappe.format(r.total_stems, { fieldtype: "Int" }),
                     r.weeks_written, r.cycles_used, r.projection, preserved],
                ),
            });
        },
    });
}
