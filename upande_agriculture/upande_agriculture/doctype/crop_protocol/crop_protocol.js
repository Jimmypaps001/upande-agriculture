frappe.ui.form.on("Crop Protocol", {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__("Calibrate from Actuals"), () => calibrate(frm));

        // The multiplier compounds every cut, so without a ceiling the model
        // grows without bound. Say so before someone budgets off it.
        if (frm.doc.stems_per_cut > 1 && !frm.doc.max_stems_per_plant_per_cut) {
            frm.dashboard.set_headline(
                __("Set <b>Max Stems Per Plant Per Cut</b> — without it the "
                   + "{0}× multiplier compounds forever and no budget can be built.",
                   [frm.doc.stems_per_cut]), "orange");
        } else if (frm.doc.weeks_between_cuts && frm.doc.max_stems_per_plant_per_cut) {
            const per_1000 = 1000 * frm.doc.max_stems_per_plant_per_cut
                / frm.doc.weeks_between_cuts * (1 - (frm.doc.reject_pct || 0) / 100);
            frm.dashboard.set_headline(
                __("At full production this variety yields about <b>{0}</b> stems "
                   + "per week per 1,000 plants.", [Math.round(per_1000).toLocaleString()]));
        } else if (frm.doc.crop_type === "Summer Flower" && frm.doc.total_stems_per_plant_life) {
            const per_1000 = 1000 * frm.doc.total_stems_per_plant_life
                * (1 - (frm.doc.reject_pct || 0) / 100);
            frm.dashboard.set_headline(
                __("Across its {0} flushes, this variety yields about <b>{1}</b> "
                   + "stems per 1,000 plants each year.",
                   [frm.doc.total_flushes, Math.round(per_1000).toLocaleString()]));
        }
    },
});

function calibrate(frm) {
    frappe.prompt(
        [{
            fieldname: "greenhouse", fieldtype: "Link", options: "Warehouse",
            label: __("Greenhouse"),
            description: __("Leave blank to read every house together."),
        }, {
            fieldname: "apply", fieldtype: "Check", label: __("Write the grade mix back"),
            default: 0,
            description: __("Yield figures are only ever reported — they need your eye."),
        }],
        (v) => frappe.call({
            method: "upande_agriculture.budget.calibrate_variety",
            args: { variety: frm.doc.variety_item, greenhouse: v.greenhouse || null,
                    apply: v.apply ? 1 : 0 },
            freeze: true,
            freeze_message: __("Reading harvest history…"),
            callback: ({ message: r }) => {
                if (!r) return;
                const rows = Object.entries(r.grade_mix || {})
                    .map(([len, pct]) => `<tr><td>${len} cm</td><td align="right">${pct}%</td></tr>`)
                    .join("") || `<tr><td colspan="2">no graded harvest found</td></tr>`;
                const warn = r.warning
                    ? `<p style="color:var(--orange-500)">${r.warning}</p>` : "";
                const implied = r.implied_max_stems_per_plant_per_cut
                    ? `<p>Actuals imply a ceiling of <b>${r.implied_max_stems_per_plant_per_cut}</b> `
                      + `stems/plant/cut (${r.steady_stems_per_week.toLocaleString()} stems/week `
                      + `across ${r.plants.toLocaleString()} plants).</p>`
                    : "";
                frappe.msgprint({
                    title: __("Calibration"),
                    indicator: r.warning ? "orange" : "green",
                    message: `<p>${r.weeks_observed} week(s) of harvest.</p>${warn}${implied}`
                        + `<table class="table table-bordered"><thead><tr><th>Grade</th>`
                        + `<th align="right">Share</th></tr></thead><tbody>${rows}</tbody></table>`
                        + (r.applied ? `<p>Grade mix written back.</p>` : ""),
                });
                if (r.applied) frm.reload_doc();
            },
        }),
        __("Calibrate from Actuals"), __("Run"),
    );
}
