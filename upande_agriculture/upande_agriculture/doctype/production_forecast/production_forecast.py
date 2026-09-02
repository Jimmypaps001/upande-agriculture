# Copyright (c) 2026, Upande Ltd and contributors
# For license information, please see license.txt

import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from upande_agriculture.projection_calc import iso_weeks_in_year

# Child-row fields this module derives; everything else on a week row is a
# human judgement and is never written back programmatically.
DERIVED_FIELDS = (
    "actual_stems", "var_actual_vs_budget",
    "var_actual_vs_manual", "var_actual_vs_revised",
)


def ensure_fiscal_year(year) -> None:
    """A forecast can be opened for any year; not every year has a Fiscal
    Year record waiting for it yet, so create the missing one rather than
    block planning on unrelated Accounts setup.

    Frappe checks Link integrity before a new document's own validate() runs,
    so this has to be called explicitly, before insert() -- every place that
    builds a Production Forecast programmatically does that. A doc created
    from the desk UI doesn't need it: Frappe's own Link field already offers
    "Create a New Fiscal Year" inline when one doesn't exist yet.
    """
    if not year:
        return
    year = str(year)
    if frappe.db.exists("Fiscal Year", year):
        return
    frappe.get_doc({
        "doctype": "Fiscal Year", "year": year,
        "year_start_date": f"{year}-01-01", "year_end_date": f"{year}-12-31",
    }).insert(ignore_permissions=True)


class ProductionForecast(Document):
    def validate(self):
        ensure_fiscal_year(self.forecast_year)
        self.check_window()
        self.sync_weeks()
        self.pull_actuals()

    def on_update_after_submit(self):
        # A revised forecast or manual budget typed after submit must move
        # the variances with it -- otherwise the headline number lies.
        self.pull_actuals(persist=True)

    def pull_actuals(self, persist: bool = False):
        """Fill actual_stems from harvest records and recompute variances.

        Only 'all' rows get actuals (harvest records don't carry a length
        grade), and only for weeks that have started -- a future week showing
        actual 0 would read as a catastrophe instead of just not-yet.
        With persist=True the derived values are written straight to the DB
        rows, which is what a submitted document needs.
        """
        keys = set()
        for w in (self.weeks or []):
            if w.week_number and (w.grade or "all") == "all":
                keys.add((int(w.iso_year or self.forecast_year), int(w.week_number)))
        totals = actual_week_totals(self.greenhouse, self.variety, keys)
        today = getdate(nowdate())

        for w in (self.weeks or []):
            if not w.week_number:
                continue
            started = False
            if (w.grade or "all") == "all":
                yr = int(w.iso_year or self.forecast_year)
                wk = int(w.week_number)
                # Int columns can't hold NULL, so "hasn't happened yet" is 0
                # actuals AND 0 variances -- a future week must read neutral,
                # not like a total crop failure against its forecast.
                started = _week_start(yr, wk) <= today
                w.actual_stems = totals.get((yr, wk), 0) if started else 0
            _set_variances(w, started)
            if persist and w.name:
                frappe.db.set_value(
                    "Production Forecast Week", w.name,
                    {f: int(w.get(f) or 0) for f in DERIVED_FIELDS},
                    update_modified=False,
                )

    def check_window(self):
        last = iso_weeks_in_year(int(self.forecast_year)) if self.forecast_year else 53
        if not self.start_week or not (1 <= int(self.start_week) <= last):
            frappe.throw(_("Start Week must be between 1 and {0}.").format(last))
        if not self.window_weeks:
            self.window_weeks = 6
        # Production Forecast now carries the full-season manual budget too, so
        # the window may run the whole year -- only the calendar itself bounds it.
        if not (1 <= int(self.window_weeks) <= last):
            frappe.throw(_("Window must be 1 to {0} weeks.").format(last))

    def sync_weeks(self):
        """Reshape the weeks table to match the window, keeping what's typed.

        Budget figures are always refreshed — the budget is the reference the
        forecast is revising against, so a stale copy would be misleading.
        Forecast numbers, reasons and notes are never touched on a row that
        already exists, so widening the window cannot overwrite a judgement
        already made — including a deliberate zero.
        """
        last = iso_weeks_in_year(int(self.forecast_year))
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


def _week_start(iso_year: int, week: int) -> datetime.date:
    return datetime.date.fromisocalendar(iso_year, week, 1)


def _set_variances(w, started: bool) -> None:
    """Anything vs actuals only once the week has started (before that
    everything against actuals reads a neutral 0).

    'Actual - Revised' falls back to the Production Budget (manual, else
    system) when no revision was typed: an unrevised week's latest call IS
    that budget, so the headline variance never goes blank just because
    nobody revised. 'Actual - Production' stays 0 until a Production Budget
    is actually entered.
    """
    if not started:
        w.var_actual_vs_budget = 0
        w.var_actual_vs_manual = 0
        w.var_actual_vs_revised = 0
        return
    actual = int(w.actual_stems or 0)
    w.var_actual_vs_budget = actual - int(w.budget_stems or 0)
    w.var_actual_vs_manual = actual - int(w.manual_budget_stems) if w.manual_budget_stems else 0
    # Int columns can't hold NULL, so a revision and an untouched row both
    # default to 0 -- treat 0 as "not revised" and fall through, same
    # convention as Actual - Production above.
    latest_call = w.revised_forecast_stems or w.manual_budget_stems or w.budget_stems
    w.var_actual_vs_revised = actual - int(latest_call or 0)


def actual_week_totals(greenhouse, variety, keys: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
    """{(iso_year, iso_week): stems actually harvested} for this house x variety.

    Reads Actual Harvest where the site has it; otherwise falls back to
    submitted 'Harvesting' Stock Entries, which receive cut stems INTO the
    greenhouse warehouse (item = variety) -- that's how the packhouse apps
    record a harvest.
    """
    if not (keys and greenhouse and variety):
        return {}
    lo = min(_week_start(y, w) for y, w in keys)
    hi = max(_week_start(y, w) for y, w in keys) + datetime.timedelta(days=6)

    if frappe.db.has_table("tabActual Harvest"):
        rows = frappe.db.sql(
            """SELECT harvest_date AS d, quantity AS q FROM `tabActual Harvest`
               WHERE greenhouse=%s AND variety=%s AND harvest_date BETWEEN %s AND %s""",
            (greenhouse, variety, lo, hi), as_dict=True,
        )
    else:
        rows = frappe.db.sql(
            """SELECT se.posting_date AS d, sed.qty AS q
               FROM `tabStock Entry` se
               JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
               WHERE se.docstatus = 1 AND se.stock_entry_type = 'Harvesting'
                 AND sed.t_warehouse = %s AND sed.item_code = %s
                 AND se.posting_date BETWEEN %s AND %s""",
            (greenhouse, variety, lo, hi), as_dict=True,
        )

    totals: dict[tuple[int, int], int] = {}
    for r in rows:
        iso = getdate(r.d).isocalendar()
        key = (iso[0], iso[1])
        if key in keys:
            totals[key] = totals.get(key, 0) + int(r.q or 0)
    return totals


@frappe.whitelist()
def refresh_actuals(forecast: str) -> dict:
    """Pull today's harvest numbers into a forecast on demand (works on
    submitted documents too -- the derived columns are allow_on_submit)."""
    doc = frappe.get_doc("Production Forecast", forecast)
    doc.pull_actuals(persist=True)
    filled = [w for w in (doc.weeks or []) if w.actual_stems is not None]
    return {"weeks_filled": len(filled),
            "total_actual": sum(int(w.actual_stems) for w in filled)}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def variety_query(doctype, txt, searchfield, start, page_len, filters):
    """Link query for Variety on this form: only varieties this greenhouse
    has actually grown, via its Crop Cycles -- a full Item list is useless
    when the whole point is "what's in this house"."""
    greenhouse = (filters or {}).get("greenhouse")
    if not greenhouse:
        return []
    return frappe.db.sql(
        """
        SELECT DISTINCT i.name, i.item_name
        FROM `tabCrop Cycle` cc
        JOIN `tabItem` i ON i.name = cc.variety
        WHERE cc.greenhouse = %(greenhouse)s
          AND i.name LIKE %(txt)s
        ORDER BY i.name
        LIMIT %(page_len)s OFFSET %(start)s
        """,
        {"greenhouse": greenhouse, "txt": f"%{txt}%", "start": start, "page_len": page_len},
    )


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
