// Greenhouse — plant, replant, and uproot beds without leaving the form.
// Each action is one row on a child table; the controller does the real
// work (expanding a range into Individual Beds, replaying logs in date
// order, refusing more plants than are standing).

frappe.ui.form.on("Greenhouse", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Plant Beds…"), () => plant_beds(frm), __("Actions"));
        // Replant/Uproot pick from what's standing right now -- a stale copy
        // (this form left open since an earlier action, or changed elsewhere
        // via a Crop Cycle sync) would offer beds under the wrong variety.
        // Reload straight from the server every time, right before showing
        // the picker, rather than trust whatever frm.doc already holds.
        frm.add_custom_button(__("Replant Beds…"), () => frm.reload_doc().then(() => replant_beds(frm)), __("Actions"));
        frm.add_custom_button(__("Uproot Beds…"), () => frm.reload_doc().then(() => uproot_beds(frm)), __("Actions"));
    },

    // A greenhouse that's already planted (tracked on Crop Cycle for yield
    // and budgeting) shouldn't mean retyping the same ranges here. Only on
    // a fresh, unsaved ledger -- an existing one has its own curated rows.
    greenhouse(frm) {
        if (!frm.is_new() || !frm.doc.greenhouse) return;
        frappe.call({
            method: "upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse.bed_ranges_from_crop_cycles",
            args: { warehouse: frm.doc.greenhouse },
            callback: ({ message: rows }) => {
                if (!rows || !rows.length) return;
                rows.forEach((r) => frm.add_child("bed_range", r));
                frm.refresh_field("bed_range");
                frappe.show_alert({
                    message: __("Prefilled {0} bed range(s) from existing Crop Cycles here — check them before saving.", [rows.length]),
                    indicator: "blue",
                }, 7);
            },
        });
    },
});

function plant_beds(frm) {
    frappe.prompt(
        [{
            fieldname: "from_bed", fieldtype: "Int", label: __("From Bed"), reqd: 1,
        }, {
            fieldname: "to_bed", fieldtype: "Int", label: __("To Bed"), reqd: 1,
        }, {
            fieldname: "variety", fieldtype: "Link", options: "Item", label: __("Variety"), reqd: 1,
        }, {
            fieldname: "crop_protocol", fieldtype: "Link", options: "Crop Protocol",
            label: __("Crop Protocol"),
        }, {
            fieldname: "planting_date", fieldtype: "Date", label: __("Planting Date"),
            default: frappe.datetime.get_today(), reqd: 1,
        }, {
            fieldname: "bed_length", fieldtype: "Float", label: __("Bed Length (m)"), reqd: 1,
        }, {
            fieldname: "bed_width", fieldtype: "Float", label: __("Bed Width (m)"), reqd: 1,
        }],
        (v) => {
            frm.add_child("bed_range", v);
            frm.save().then(() => frappe.show_alert({
                message: __("Beds {0}-{1} planted with {2}.", [v.from_bed, v.to_bed, v.variety]),
                indicator: "green",
            }, 7));
        },
        __("Plant Beds"), __("Plant"),
    );
}

// A bed only counts as pickable if something is actually standing on it.
const OCCUPIED = ["Planted", "Producing", "Harvesting", "Transplanted"];

// bed_number -> contiguous run -- same idea as the server's _contiguous_runs,
// so "Reflex, beds 1-89" reads the way the ledger itself groups things.
function contiguous_runs(numbers) {
    const sorted = [...numbers].sort((a, b) => a - b);
    const runs = [];
    let start = sorted[0], prev = sorted[0];
    for (const n of [...sorted.slice(1), null]) {
        if (n === prev + 1) { prev = n; continue; }
        runs.push([start, prev]);
        start = prev = n;
    }
    return runs;
}

// {variety: {beds: [...], plants: total}} from what's standing right now.
function standing_by_variety(frm) {
    const out = {};
    (frm.doc.individual_beds || []).forEach((b) => {
        if (!OCCUPIED.includes(b.status)) return;
        const v = out[b.variety] || (out[b.variety] = { beds: [], plants: 0 });
        v.beds.push(b.bed_number);
        v.plants += b.plant_count || 0;
    });
    return out;
}

// Plants actually standing on a bed span right now, regardless of variety --
// this is what a range's quantity should reflect, live, as From/To change.
function standing_in_range(frm, lo, hi) {
    if (!lo || !hi) return 0;
    if (lo > hi) [lo, hi] = [hi, lo];
    return (frm.doc.individual_beds || [])
        .filter((b) => OCCUPIED.includes(b.status) && b.bed_number >= lo && b.bed_number <= hi)
        .reduce((t, b) => t + (b.plant_count || 0), 0);
}

// Wired onto both From Bed and To Bed -- whichever one changes, the
// quantity recomputes from what's actually standing on the range now.
function bed_range_qty_sync(frm, dialog, qty_fieldname) {
    return function () {
        dialog.set_value(qty_fieldname, standing_in_range(
            frm, dialog.get_value("from_bed"), dialog.get_value("to_bed"),
        ));
    };
}

function occupied_beds_html(by_variety) {
    const rows = Object.entries(by_variety).map(([variety, v]) => {
        const ranges = contiguous_runs(v.beds).map(([lo, hi]) => (lo === hi ? `${lo}` : `${lo}-${hi}`)).join(", ");
        return `<tr><td>${frappe.utils.escape_html(variety)}</td><td>${ranges}</td>`
            + `<td style="text-align:right">${v.plants.toLocaleString()}</td></tr>`;
    }).join("");
    if (!rows) return `<p class="text-muted">${__("Nothing standing here yet.")}</p>`;
    return `<table class="table table-bordered" style="margin-bottom:12px">
        <thead><tr><th>${__("Variety")}</th><th>${__("Beds")}</th><th>${__("Plants")}</th></tr></thead>
        <tbody>${rows}</tbody></table>`;
}

// Selecting a variety fills in its first bed run and current plant count --
// the grower can still edit either before submitting.
function variety_picker_field(by_variety, fieldname, on_pick) {
    return {
        fieldname, fieldtype: "Select", label: __("Variety Standing Here"),
        options: [""].concat(Object.keys(by_variety).sort()),
        description: __("Pick one to fill in its beds and count below; or type your own."),
        onchange() {
            const v = this.get_value();
            if (!v || !by_variety[v]) return;
            const [lo, hi] = contiguous_runs(by_variety[v].beds)[0];
            on_pick(lo, hi, by_variety[v].plants, v);
        },
    };
}

function replant_beds(frm) {
    const by_variety = standing_by_variety(frm);
    const dialog = new frappe.ui.Dialog({
        title: __("Replant Beds"),
        fields: [
            { fieldname: "reference", fieldtype: "HTML", options: occupied_beds_html(by_variety) },
            variety_picker_field(by_variety, "current_variety", (lo, hi) => {
                dialog.set_value("from_bed", lo);
                dialog.set_value("to_bed", hi);
            }),
            { fieldname: "replant_date", fieldtype: "Date", label: __("Date"),
              default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "from_bed", fieldtype: "Int", label: __("From Bed"), reqd: 1,
              onchange: () => bed_range_qty_sync(frm, dialog, "qty_replanted")() },
            { fieldname: "to_bed", fieldtype: "Int", label: __("To Bed"), reqd: 1,
              onchange: () => bed_range_qty_sync(frm, dialog, "qty_replanted")() },
            { fieldname: "qty_replanted", fieldtype: "Int", label: __("Qty Replanted"), reqd: 1,
              description: __("Filled in from what's standing on these beds — also uproots it, same date, in one step.") },
            { fieldname: "new_variety", fieldtype: "Link", options: "Item", label: __("New Variety"), reqd: 1 },
            { fieldname: "seedling_source", fieldtype: "Select", label: __("Seedling Source"),
              options: "\nPurchased from Breeder\nIn-house Propagation" },
        ],
        primary_action_label: __("Replant"),
        primary_action(v) {
            // A replant must land on an already-uprooted bed; logging both the
            // same date does both in one step (uproot sorts first on a tie).
            const outgoing = (frm.doc.individual_beds || [])
                .filter((b) => OCCUPIED.includes(b.status) && b.bed_number >= v.from_bed && b.bed_number <= v.to_bed);
            const standing = outgoing.reduce((t, b) => t + (b.plant_count || 0), 0);
            if (standing > 0) {
                frm.add_child("uprooting_logs", {
                    uproot_date: v.replant_date, from_bed: v.from_bed, to_bed: v.to_bed,
                    reason: "Variety Change", qty_uprooted: standing,
                });
            }
            frm.add_child("replanting_logs", {
                replant_date: v.replant_date, from_bed: v.from_bed, to_bed: v.to_bed,
                qty_replanted: v.qty_replanted, new_variety: v.new_variety,
                seedling_source: v.seedling_source,
            });
            dialog.hide();
            frm.save().then(() => frappe.show_alert({
                message: __("Beds {0}-{1} replanted with {2}.", [v.from_bed, v.to_bed, v.new_variety]),
                indicator: "green",
            }, 7));
        },
    });
    dialog.show();
}

function uproot_beds(frm) {
    const by_variety = standing_by_variety(frm);
    const dialog = new frappe.ui.Dialog({
        title: __("Uproot Beds"),
        fields: [
            { fieldname: "reference", fieldtype: "HTML", options: occupied_beds_html(by_variety) },
            variety_picker_field(by_variety, "current_variety", (lo, hi) => {
                dialog.set_value("from_bed", lo);
                dialog.set_value("to_bed", hi);
            }),
            { fieldname: "uproot_date", fieldtype: "Date", label: __("Date"),
              default: frappe.datetime.get_today(), reqd: 1 },
            { fieldname: "from_bed", fieldtype: "Int", label: __("From Bed"), reqd: 1,
              onchange: () => bed_range_qty_sync(frm, dialog, "qty_uprooted")() },
            { fieldname: "to_bed", fieldtype: "Int", label: __("To Bed"), reqd: 1,
              onchange: () => bed_range_qty_sync(frm, dialog, "qty_uprooted")() },
            { fieldname: "reason", fieldtype: "Select", label: __("Reason"), reqd: 1,
              options: "\nLow Yield\nDisease\nVariety Change\nAge (End of Life)\nStorm Damage\nOther" },
            { fieldname: "qty_uprooted", fieldtype: "Int", label: __("Qty Uprooted"), reqd: 1,
              description: __("Filled in from what's standing on these beds.") },
        ],
        primary_action_label: __("Uproot"),
        primary_action(v) {
            frm.add_child("uprooting_logs", v);
            dialog.hide();
            frm.save().then(() => frappe.show_alert({
                message: __("Beds {0}-{1} uprooted.", [v.from_bed, v.to_bed]),
                indicator: "orange",
            }, 7));
        },
    });
    dialog.show();
}
