frappe.ui.form.on("Production Forecast", {
    setup(frm) {
        // Only varieties this greenhouse has actually grown — not every Item.
        frm.set_query("variety", () => ({
            query: "upande_agriculture.upande_agriculture.doctype.production_forecast.production_forecast.variety_query",
            filters: { greenhouse: frm.doc.greenhouse },
        }));
    },

    refresh(frm) {
        fill_window(frm);
        if (frm.is_new()) return;

        frm.add_custom_button(__("Refresh Actuals"), () => {
            frappe.call({
                method: "upande_agriculture.upande_agriculture.doctype.production_forecast.production_forecast.refresh_actuals",
                args: { forecast: frm.doc.name },
                freeze: true,
                callback: ({ message: r }) => {
                    if (r) frappe.show_alert({
                        message: __("{0} week(s) filled — {1} stems harvested so far.",
                            [r.weeks_filled, (r.total_actual || 0).toLocaleString()]),
                        indicator: "green",
                    }, 6);
                    frm.reload_doc();
                },
            });
        });
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
                if (wk <= 53) wanted.push(wk);
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
                });
                if (old) {
                    // Reshaping the window must not eat judgements already
                    // typed — carry every human-entered column across.
                    for (const f of ["reason", "note", "iso_year", "grade",
                                     "manual_budget_stems", "revised_forecast_stems",
                                     "actual_stems"]) {
                        row[f] = old[f];
                    }
                }
            });
            frm.refresh_field("weeks");
        },
    });
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
