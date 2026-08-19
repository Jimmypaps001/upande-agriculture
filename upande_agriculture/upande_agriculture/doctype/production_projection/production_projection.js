frappe.ui.form.on("Production Projection", {
    refresh(frm) {
        if (frm.is_new()) return;

        frm.add_custom_button(__("Rebuild from Crop Cycles"), () => {
            frappe.confirm(
                __("Rebuild all 52 weeks from the crop cycles in {0}?<br><br>"
                   + "Locked and hand-edited weeks are left alone.", [frm.doc.greenhouse]),
                () => frappe.call({
                    method: "upande_agriculture.budget.generate_budget",
                    args: { greenhouse: frm.doc.greenhouse, variety: frm.doc.variety,
                            year: frm.doc.projection_year },
                    freeze: true,
                    callback: ({ message: r }) => {
                        frappe.show_alert({
                            message: __("{0} weeks rebuilt, {1} preserved",
                                        [r.weeks_written, r.weeks_preserved]),
                            indicator: "green" });
                        frm.reload_doc();
                    },
                }));
        });

        frm.add_custom_button(__("Split by Grade"), () => frappe.call({
            method: "upande_agriculture.budget.budget_by_grade",
            args: { greenhouse: frm.doc.greenhouse, variety: frm.doc.variety,
                    year: frm.doc.projection_year },
            freeze: true,
            callback: ({ message: r }) => show_grades(r),
        }));

        total_headline(frm);
    },
});

function total_headline(frm) {
    const weeks = frm.doc.weeks || [];
    const total = weeks.reduce((t, w) => t + (w.projected_stems || 0), 0);
    if (!total) return;
    const producing = weeks.filter((w) => w.projected_stems > 0).length;
    frm.dashboard.set_headline(
        __("<b>{0}</b> stems budgeted across {1} producing week(s).",
           [total.toLocaleString(), producing]));
}

function show_grades(r) {
    if (!r) return;
    const lengths = (r.grade_mix || []).map((g) => g.length_cm);
    if (!lengths.length) {
        frappe.msgprint(__("This variety's Crop Protocol has no grade mix. "
            + "Run <b>Calibrate from Actuals</b> on the protocol to build one."));
        return;
    }
    const head = lengths.map((l) => `<th align="right">${l}cm</th>`).join("");
    const body = Object.entries(r.weeks)
        .filter(([, split]) => Object.values(split).some((v) => v))
        .map(([wk, split]) => `<tr><td>${wk}</td>`
            + lengths.map((l) => `<td align="right">${(split[l] || 0).toLocaleString()}</td>`).join("")
            + `</tr>`).join("");
    frappe.msgprint({
        title: __("Budget by Grade"), wide: true,
        message: `<div style="max-height:60vh;overflow:auto">`
            + `<table class="table table-bordered table-sm"><thead><tr><th>Week</th>${head}</tr></thead>`
            + `<tbody>${body}</tbody></table></div>`,
    });
}
