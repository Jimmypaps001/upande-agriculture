# Copyright (c) 2026, Upande Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from upande_agriculture.projection_calc import iso_weeks_in_year

MAX_WINDOW = 26


class ProductionForecast(Document):
    def validate(self):
        self.check_window()
        self.sync_weeks()

    def check_window(self):
        last = iso_weeks_in_year(self.forecast_year) if self.forecast_year else 53
        if not self.start_week or not (1 <= int(self.start_week) <= last):
            frappe.throw(_("Start Week must be between 1 and {0}.").format(last))
        if not self.window_weeks:
            self.window_weeks = 6
        if not (1 <= int(self.window_weeks) <= MAX_WINDOW):
            frappe.throw(
                _("Window must be 1 to {0} weeks. A forecast reaching further "
                  "than that is a budget, not a forecast.").format(MAX_WINDOW)
            )

    def sync_weeks(self):
        """Reshape the weeks table to match the window, keeping what's typed.

        Budget figures are always refreshed — the budget is the reference the
        forecast is revising against, so a stale copy would be misleading.
        Forecast numbers, reasons and notes are never touched on a row that
        already exists, so widening the window cannot overwrite a judgement
        already made — including a deliberate zero.
        """
        last = iso_weeks_in_year(self.forecast_year)
        wanted = [
            wk for wk in range(int(self.start_week),
                               int(self.start_week) + int(self.window_weeks))
            if wk <= last
        ]
        budget = budget_weeks(self.greenhouse, self.variety, self.forecast_year)
        # A row is identified by (year, week, grade) — a window may cross a year
        # end, and a grade may be revised without touching the week total.
        existing = {}
        for w in (self.weeks or []):
            if not w.week_number:
                continue
            w.grade = (w.grade or "all").strip() or "all"
            w.iso_year = int(w.iso_year or self.forecast_year)
            existing[(w.iso_year, int(w.week_number), w.grade)] = w

        rows = []
        for wk in wanted:
            budgeted = budget.get(wk, 0)
            key = (int(self.forecast_year), wk, "all")
            old = existing.pop(key, None)
            if old:
                old.budget_stems = budgeted
                rows.append(old)
            else:
                rows.append(self.append("weeks", {
                    "week_number": wk,
                    "iso_year": int(self.forecast_year),
                    "grade": "all",
                    "budget_stems": budgeted,
                    "forecasted_stems": budgeted,
                }))
            # Grade revisions for a week that is staying ride along with it.
            for (yr, w, g), row in list(existing.items()):
                if w == wk:
                    rows.append(existing.pop((yr, w, g)))

        # Anything left belongs to a week the window no longer covers, and the
        # window is the forecast's scope — narrowing it drops those weeks.
        rows.sort(key=lambda r: (int(r.iso_year or 0), int(r.week_number or 0),
                                 "" if (r.grade or "all") == "all" else r.grade))

        self.weeks = []
        for idx, row in enumerate(rows, start=1):
            row.idx = idx
            self.weeks.append(row)


def budget_weeks(greenhouse, variety, year) -> dict[int, int]:
    """{iso_week: budgeted stems} for a greenhouse x variety x year."""
    if not (greenhouse and variety and year):
        return {}
    rows = frappe.db.sql(
        """
        SELECT pw.week, pw.projected_stems
        FROM `tabProduction Projection` pp
        JOIN `tabProjection Week` pw ON pw.parent = pp.name
        WHERE pp.greenhouse = %s AND pp.variety = %s AND pp.projection_year = %s
        """,
        (greenhouse, variety, int(year)), as_dict=True,
    )
    return {int(r["week"]): int(r["projected_stems"] or 0) for r in rows}
