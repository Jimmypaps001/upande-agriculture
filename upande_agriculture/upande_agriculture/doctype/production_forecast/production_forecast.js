frappe.ui.form.on("Production Forecast", {
    refresh(frm) {
        fill_window(frm);
        if (frm.is_new()) return;

        if (frm.doc.status === "Active") {
            frm.add_custom_button(__("New Revision"), () => new_revision(frm));
        }
        if (frm.doc.supersedes) {
            frm.add_custom_button(__("Previous Revision"), () => {
                frappe.set_route("Form", "Production Forecast", frm.doc.supersedes);
            });
        }
        if (frm.doc.status === "Superseded") {
            frm.dashboard.set_headline(
                __("Superseded — a later revision replaced this one. Kept for the record."),
                "orange");
        }
    },
    // The server fills the window on save; doing it here too means the grid
    // is right before saving, which is when people actually look at it.
    greenhouse: fill_window,
    variety: fill_window,
    forecast_year: fill_window,
    start_week: fill_window,
    window_weeks: fill_window,
});

function fill_window(frm) {
    const { greenhouse, variety, forecast_year, start_week } = frm.doc;
    const window_weeks = frm.doc.window_weeks || 6;
    if (!(greenhouse && variety && forecast_year && start_week)) return;

    frappe.call({
        method: "upande_agriculture.budget.budget_week_map",
        args: { greenhouse, variety, year: forecast_year },
        callback: ({ message: r }) => {
            if (!r) return;
            const budget = r.weeks || {};
            const wanted = [];
            for (let i = 0; i < window_weeks; i++) {
                const wk = start_week + i;
                if (wk <= 52) wanted.push(wk);
            }

            // Keep what is already typed; only reshape the window.
            const kept = {};
            (frm.doc.weeks || []).forEach((w) => { if (w.week_number) kept[w.week_number] = w; });

            frm.clear_table("weeks");
            wanted.forEach((wk) => {
                const budgeted = budget[wk] || 0;
                const old = kept[wk];
                const row = frm.add_child("weeks", {
                    week_number: wk,
                    budget_stems: budgeted,
                    forecasted_stems: old ? old.forecasted_stems : budgeted,
                });
                if (old) { row.reason = old.reason; row.note = old.note; }
            });
            frm.refresh_field("weeks");
            budget_headline(frm, r, wanted);
        },
    });
}

function budget_headline(frm, r, wanted) {
    if (frm.doc.status === "Superseded") return;
    if (!r.has_budget) {
        frm.dashboard.set_headline(
            __("No budget exists for {0} / {1} in {2}, so every week reads zero. "
               + "Generate the budget from the Crop Cycle first.",
               [frm.doc.variety, frm.doc.greenhouse, frm.doc.forecast_year]), "orange");
        return;
    }
    const inWindow = wanted.reduce((t, wk) => t + (r.weeks[wk] || 0), 0);
    if (!inWindow) {
        frm.dashboard.set_headline(
            __("The budget has {0} stems in {1}, but none in weeks {2}-{3}. "
               + "Check the window.",
               [r.total.toLocaleString(), frm.doc.forecast_year,
                wanted[0], wanted[wanted.length - 1]]), "orange");
        return;
    }
    frm.dashboard.set_headline(
        __("Budget across this window: <b>{0}</b> stems.", [inWindow.toLocaleString()]));
}

// Revising never overwrites: the live doc flips to Superseded and stays
// readable, so forecast accuracy can be scored by horizon later.
function new_revision(frm) {
    frappe.prompt(
        [{ fieldname: "start_week", fieldtype: "Int", label: __("Start ISO Week"),
           default: frappe.datetime.get_week_number
               ? frappe.datetime.get_week_number(frappe.datetime.nowdate())
               : frm.doc.start_week, reqd: 1 },
         { fieldname: "window_weeks", fieldtype: "Int", label: __("Window (weeks)"),
           default: frm.doc.window_weeks || 6, reqd: 1,
           description: __("Typically 4 to 10.") }],
        (v) => frappe.call({
            method: "upande_agriculture.budget.revise_forecast",
            args: { greenhouse: frm.doc.greenhouse, variety: frm.doc.variety,
                    year: frm.doc.forecast_year, start_week: v.start_week,
                    window_weeks: v.window_weeks },
            freeze: true,
            callback: ({ message: r }) => {
                frappe.show_alert({
                    message: __("Revision {0} created", [r.revision]), indicator: "green" });
                frappe.set_route("Form", "Production Forecast", r.forecast);
            },
        }),
        __("New Revision"), __("Create"),
    );
}
