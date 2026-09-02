"""Budget generation, forecast revisions, and calibration from actuals.

Three jobs, all keyed on (greenhouse, variety):

  generate_budget      Crop Cycles x Crop Protocol -> full-year Production Projection
  revise_forecast      clone the live forecast, supersede the old one
  calibrate_variety    read harvest actuals back into the protocol
"""

from __future__ import annotations

import datetime
import json
import re

import frappe
from frappe import _
from frappe.utils import getdate

from upande_agriculture import weekcal
from upande_agriculture.projection_calc import (
    build_budget_year,
    iso_weeks_in_year,
    split_by_grade,
)

LENGTH_SUFFIX = re.compile(r"-\s*(\d+)\s*cm$", re.IGNORECASE)

PROTOCOL_FIELDS = (
    "crop_type",
    "weeks_to_first_bending",
    "weeks_to_second_bending",
    "weeks_between_cuts",
    "stems_per_plant_first_harvest",
    "stems_per_cut",
    "max_stems_per_plant_per_cut",
    "weeks_to_first_flush",
    "reject_pct",
    "productive_life_weeks",
)

CYCLE_FIELDS = (
    "name",
    "greenhouse",
    "variety",
    "crop_protocol",
    "planting_date",
    "first_bending_date",
    "second_bending_date",
    "planned_uprooting_date",
    "cycle_end_date",
    "qty_planted",
)


def base_variety(item_code: str | None) -> str:
    """'ATHENA-60CM' -> 'ATHENA'. Harvest is length-graded; plants are not."""
    return LENGTH_SUFFIX.sub("", (item_code or "").strip())


def grade_of(item_code: str | None) -> int | None:
    m = LENGTH_SUFFIX.search((item_code or "").strip())
    return int(m.group(1)) if m else None


def _protocol_dict(name: str | None) -> dict | None:
    if not name:
        return None
    doc = frappe.get_cached_doc("Crop Protocol", name)
    out = {f: doc.get(f) for f in PROTOCOL_FIELDS}
    # projection_calc wants plain dicts, not child Documents.
    out["flush_schedule"] = [
        {"flush_number": r.flush_number, "weeks_after_first_flush": r.weeks_after_first_flush,
         "stems_per_plant": r.stems_per_plant}
        for r in (doc.flush_schedule or [])
    ]
    return out


def _resolve_protocol(cycle: dict) -> dict | None:
    """The cycle's own protocol, else any protocol for that variety."""
    proto = _protocol_dict(cycle.get("crop_protocol"))
    if proto:
        return proto
    fallback = frappe.db.get_value(
        "Crop Protocol", {"variety_item": cycle.get("variety")}, "name"
    )
    return _protocol_dict(fallback)


def _cycles_for(greenhouse: str, variety: str) -> list[tuple[dict, dict]]:
    """Every planting of a variety in a house, paired with its protocol.

    Cycles that resolve to no protocol are skipped - there is nothing to
    project from - rather than silently contributing zero.
    """
    rows = frappe.db.get_all(
        "Crop Cycle",
        filters={"greenhouse": greenhouse, "variety": variety},
        fields=list(CYCLE_FIELDS),
        order_by="planting_date asc",
    )
    out = []
    for r in rows:
        proto = _resolve_protocol(r)
        if proto:
            out.append((dict(r), proto))
    return out


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

@frappe.whitelist()
def generate_budget(greenhouse: str, variety: str, year: int,
                    overwrite_manual: int = 0) -> dict:
    """Build or refresh the 52-week Production Projection for a house x variety.

    Weeks flagged week_locked or manual_override are preserved unless
    overwrite_manual is set, so a planner's hand edits survive a regenerate.
    """
    year = int(year)
    overwrite_manual = int(overwrite_manual or 0)

    cycles = _cycles_for(greenhouse, variety)
    if not cycles:
        frappe.throw(_(
            "No Crop Cycle with a Crop Protocol found for {0} in {1}."
        ).format(variety, greenhouse))

    from upande_agriculture.controllers import _seasonal_factor_map

    # Whatever rule is configured now gets used AND stamped, so regenerating is
    # how an existing budget moves onto a changed rule.
    rule = weekcal.get_week_rule()
    weeks = build_budget_year(cycles, year, _seasonal_factor_map(variety), rule)

    name = frappe.db.get_value("Production Projection", {
        "greenhouse": greenhouse, "variety": variety, "projection_year": year,
    }, "name")
    if name:
        doc = frappe.get_doc("Production Projection", name)
    else:
        doc = frappe.new_doc("Production Projection")
        doc.update({
            "greenhouse": greenhouse, "variety": variety, "projection_year": year,
            "company": frappe.db.get_value("Warehouse", greenhouse, "company"),
        })
    doc.source = "Calculated from Protocol"
    doc.week_rule = rule

    existing = {int(w.week or 0): w for w in (doc.weeks or [])}
    written = skipped = 0
    for wk in range(1, iso_weeks_in_year(year, rule) + 1):
        stems = weeks.get(wk, 0)
        row = existing.get(wk)
        if row is None:
            if not stems:
                continue
            doc.append("weeks", {"week": wk, "projected_stems": stems})
            written += 1
            continue
        if not overwrite_manual and (row.week_locked or row.manual_override):
            skipped += 1
            continue
        row.projected_stems = stems
        written += 1

    doc.save(ignore_permissions=True)
    return {
        "projection": doc.name,
        "cycles_used": len(cycles),
        "weeks_written": written,
        "weeks_preserved": skipped,
        "total_stems": sum(weeks.values()),
    }


@frappe.whitelist()
def generate_all_budgets(year: int, greenhouse: str | None = None) -> dict:
    """Regenerate every (greenhouse, variety) budget that has crop cycles."""
    year = int(year)
    filters = {"greenhouse": greenhouse} if greenhouse else {}
    pairs = frappe.db.get_all(
        "Crop Cycle", filters=filters, fields=["greenhouse", "variety"],
        group_by="greenhouse, variety",
    )
    done, failed = [], []
    for p in pairs:
        try:
            done.append(generate_budget(p.greenhouse, p.variety, year))
        except Exception as e:
            failed.append({"greenhouse": p.greenhouse, "variety": p.variety,
                           "error": str(e)})
    return {"generated": len(done), "failed": failed,
            "total_stems": sum(d["total_stems"] for d in done)}


@frappe.whitelist()
def budget_by_grade(greenhouse: str, variety: str, year: int) -> dict:
    """The 52-week budget split into length grades via the protocol's grade mix.

    Budgets are per base variety; every harvest actual is length-graded. This
    is what makes the two comparable.
    """
    year = int(year)
    name = frappe.db.get_value("Production Projection", {
        "greenhouse": greenhouse, "variety": variety, "projection_year": year,
    }, "name")
    if not name:
        frappe.throw(_("No budget for {0} in {1} for {2}.").format(
            variety, greenhouse, year))

    proto_name = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "name")
    mix = []
    if proto_name:
        mix = [
            {"length_cm": r.length_cm, "pct": r.pct}
            for r in frappe.get_cached_doc("Crop Protocol", proto_name).grade_mix or []
        ]

    doc = frappe.get_doc("Production Projection", name)
    out = {}
    for w in doc.weeks:
        stems = int(w.projected_stems or 0)
        out[int(w.week)] = split_by_grade(stems, mix) if mix else {"ungraded": stems}
    return {"projection": name, "grade_mix": mix, "weeks": out}


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

@frappe.whitelist()
def budget_week_map(greenhouse: str, variety: str, year: int) -> dict:
    """{iso_week: budgeted stems} — lets the Forecast form fill before saving."""
    from upande_agriculture.upande_agriculture.doctype.production_forecast.production_forecast import (
        budget_weeks,
    )
    weeks = budget_weeks(greenhouse, variety, year)
    return {
        "weeks": {str(k): v for k, v in weeks.items()},
        "has_budget": bool(weeks),
        "total": sum(weeks.values()),
    }


@frappe.whitelist()
def revise_forecast(greenhouse: str, variety: str, year: int,
                    start_week: int, window_weeks: int = 6) -> dict:
    """Open a new forecast revision, superseding the current live one.

    Nothing is overwritten: the previous revision flips to Superseded and stays
    readable, so forecast accuracy can be scored by horizon later.
    """
    from upande_agriculture.upande_agriculture.doctype.production_forecast.production_forecast import (
        ensure_fiscal_year,
    )

    year, start_week = int(year), int(start_week)
    window_weeks = int(window_weeks or 6)
    ensure_fiscal_year(year)

    current = frappe.db.get_value("Production Forecast", {
        "greenhouse": greenhouse, "variety": variety,
        "forecast_year": year, "status": "Active",
    }, ["name", "revision"], as_dict=True)

    doc = frappe.new_doc("Production Forecast")
    doc.update({
        "greenhouse": greenhouse, "variety": variety, "forecast_year": year,
        "start_week": start_week, "window_weeks": window_weeks, "status": "Active",
        "company": frappe.db.get_value("Warehouse", greenhouse, "company"),
        "revision": int(current.revision or 1) + 1 if current else 1,
        "supersedes": current.name if current else None,
    })

    # The controller fills the window from the budget on validate; carry the
    # previous revision's judgement forward on top of it.
    prior = {}
    if current:
        prior = {
            int(w.week_number): w
            for w in frappe.get_doc("Production Forecast", current.name).weeks
        }
    if prior:
        doc.run_method("validate")
        for row in doc.weeks:
            old = prior.get(int(row.week_number))
            if old:
                row.manual_budget_stems = old.manual_budget_stems
                row.revised_forecast_stems = old.revised_forecast_stems
                row.reason = old.reason
                row.note = old.note

    doc.insert(ignore_permissions=True)

    if current:
        frappe.db.set_value("Production Forecast", current.name, "status", "Superseded")

    return {"forecast": doc.name, "revision": doc.revision,
            "superseded": current.name if current else None,
            "weeks": len(doc.weeks)}


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

@frappe.whitelist()
def calibrate_variety(variety: str, greenhouse: str | None = None,
                      min_weeks: int = 12, apply: int = 0) -> dict:
    """Back-solve protocol numbers from real harvest.

    Returns what the actuals imply and, with apply=1, writes the grade mix
    onto the protocol. Yield parameters are reported but never auto-applied:
    they need a grower's eye, and a short/gappy history skews them badly.
    """
    apply = int(apply)
    min_weeks = int(min_weeks)
    weekly = _actual_weekly(variety, greenhouse)
    mix = _actual_grade_mix(variety, greenhouse)

    result = {
        "variety": variety, "greenhouse": greenhouse or "all",
        "weeks_observed": len(weekly), "grade_mix": mix, "applied": False,
    }

    if len(weekly) < min_weeks:
        result["warning"] = (
            f"Only {len(weekly)} weeks of harvest — need {min_weeks}+ "
            f"(2-3 full cut cycles) before yield figures mean anything."
        )
    else:
        values = sorted(weekly.values())
        # Median of the top half: the plateau, not the ramp or the gaps.
        upper = values[len(values) // 2:]
        result["steady_stems_per_week"] = int(round(sum(upper) / len(upper)))

    plants = _plants_for(variety, greenhouse)
    if plants and result.get("steady_stems_per_week"):
        cut_weeks = frappe.db.get_value(
            "Crop Protocol", {"variety_item": variety}, "weeks_between_cuts"
        )
        if cut_weeks:
            result["implied_max_stems_per_plant_per_cut"] = round(
                result["steady_stems_per_week"] * int(cut_weeks) / plants, 3
            )
        result["plants"] = plants

    proto_name = frappe.db.get_value("Crop Protocol", {"variety_item": variety}, "name")
    result["protocol"] = proto_name
    if apply and proto_name and mix:
        doc = frappe.get_doc("Crop Protocol", proto_name)
        # Manual rows are the grower's; only Measured rows are ours to replace.
        doc.grade_mix = [r for r in (doc.grade_mix or []) if r.source == "Manual"]
        for length_cm, pct in sorted(mix.items()):
            doc.append("grade_mix", {"length_cm": length_cm, "pct": pct,
                                     "source": "Measured"})
        doc.save(ignore_permissions=True)
        result["applied"] = True

    return result


def _harvest_rows(variety: str, greenhouse: str | None):
    conds = ["se.docstatus = 1", "se.stock_entry_type LIKE '%%Harvest%%'"]
    args: list = []
    if greenhouse:
        conds.append("sed.t_warehouse = %s")
        args.append(greenhouse)
    return frappe.db.sql(
        f"""
        SELECT sed.item_code, sed.qty, se.posting_date
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE {' AND '.join(conds)}
        """,
        args, as_dict=True,
    )


def _actual_weekly(variety: str, greenhouse: str | None) -> dict[tuple, float]:
    out: dict[tuple, float] = {}
    target = base_variety(variety).upper()
    for r in _harvest_rows(variety, greenhouse):
        if base_variety(r["item_code"]).upper() != target:
            continue
        cal = getdate(r["posting_date"]).isocalendar()
        key = (cal[0], cal[1])
        out[key] = out.get(key, 0.0) + float(r["qty"] or 0)
    return out


def _actual_grade_mix(variety: str, greenhouse: str | None) -> dict[int, float]:
    by_grade: dict[int, float] = {}
    target = base_variety(variety).upper()
    for r in _harvest_rows(variety, greenhouse):
        if base_variety(r["item_code"]).upper() != target:
            continue
        g = grade_of(r["item_code"])
        if g is None:
            continue
        by_grade[g] = by_grade.get(g, 0.0) + float(r["qty"] or 0)
    total = sum(by_grade.values())
    if total <= 0:
        return {}
    return {g: round(100.0 * v / total, 2) for g, v in sorted(by_grade.items())}


def _plants_for(variety: str, greenhouse: str | None) -> int:
    filters = {"variety": variety, "status": "Active"}
    if greenhouse:
        filters["greenhouse"] = greenhouse
    rows = frappe.db.get_all("Crop Cycle", filters=filters, pluck="qty_planted")
    return sum(int(r or 0) for r in rows)


# ---------------------------------------------------------------------------
# Page payload
# ---------------------------------------------------------------------------

MONTHS = ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]

# Mona's own budget curve, read off BUDGET 2026-2027.xlsx. February is the peak
# everywhere (Valentine's), with December and May raised too — this is a market
# shape, not a biological one, so it is a constant rather than something the
# growth model derives.
MONTH_WEIGHT = [0.070, 0.077, 0.077, 0.092, 0.077, 0.140, 0.070, 0.086, 0.086, 0.070, 0.070, 0.085]

FORECAST_WINDOW = 11


def crop_year_of(d: datetime.date) -> str:
    """Sep-Aug. A date in Sep 2026 belongs to crop year 2026/27."""
    start = d.year if d.month >= 9 else d.year - 1
    return f"{start}/{str(start + 1)[-2:]}"


MAX_SPAN_WEEKS = 260  # five years — bounds the payload, not the user's ambition


def week_span(sy: int, sw: int, ey: int, ew: int) -> list:
    """Every (year, week) from start to end inclusive, rolling over year ends."""
    out, y, w = [], int(sy), int(sw)
    ey, ew = int(ey), int(ew)
    while (y, w) <= (ey, ew) and len(out) < MAX_SPAN_WEEKS:
        out.append((y, w))
        w += 1
        if w > iso_weeks_in_year(y):
            y, w = y + 1, 1
    return out


@frappe.whitelist()
def grid_payload(year: int | None = None, start_year=None, start_week=None,
                 end_year=None, end_week=None, mode: str | None = None) -> dict:
    """Everything the Production Budget page draws in one round trip.

    mode picks what the "budget" line actually shows: "manual" (default) is
    the Production Forecast's own figure -- typed by hand, falling back to
    its system snapshot where nobody has typed one yet. "automated" is the
    Production Projection model, computed live from the crop cycles.
    """
    mode = "automated" if (mode or "").lower() == "automated" else "manual"
    today = getdate(frappe.utils.nowdate())
    year = int(year or today.year)
    # The axis is a calendar axis, so it follows whatever rule is configured now.
    # A projection stamped with a different rule is genuinely out of step with it —
    # that is why changing the setting tells you to regenerate.
    rule = weekcal.get_week_rule()
    iso_year, now_week = weekcal.week_key(today, rule)

    if start_week and end_week:
        pairs = week_span(start_year or year, start_week, end_year or start_year or year, end_week)
    else:
        pairs = [(year, w) for w in range(now_week - 6, now_week + FORECAST_WINDOW - 6)
                 if 1 <= w <= iso_weeks_in_year(year, rule)]
    if not pairs:
        pairs = [(year, now_week)]
    weeks = [w for _, w in pairs]
    week_years = [y for y, _ in pairs]
    span_years = sorted({y for y, _ in pairs})

    cycles = frappe.db.get_all(
        "Crop Cycle",
        filters={"status": ("!=", "Ended")},
        # The model needs the dates too — without planting_date every block
        # silently budgets zero.
        fields=list(CYCLE_FIELDS) + ["planted_area"],
        order_by="greenhouse asc, variety asc",
    )

    from upande_agriculture.controllers import _seasonal_factor_map

    def _seasonal_for(variety):
        """A tenant's own factors win; otherwise the farm's budget curve."""
        return _seasonal_factor_map(variety) or default_seasonal_factors()

    # THE fix: the grid used to hardcode revised=[None]*n, so every number a
    # planner typed was saved and then never read back.
    _pairs = [(c["greenhouse"], c["variety"], year) for c in cycles]
    fc = active_forecasts(_pairs)
    mo = manual_month_overrides(_pairs)
    revs = active_revisions(_pairs)
    mb = manual_budget_map(_pairs) if mode == "manual" else {}

    blocks, budget_total, forecast_total = [], 0, 0
    for c in cycles:
        # Old-model leftovers carry no variety — nothing to budget, forecast
        # or attach actuals to, so they'd only render as empty "None" blocks.
        if not c.get("variety"):
            continue
        proto = _resolve_protocol(c)
        sf = _seasonal_for(c["variety"])
        by_year = ({y: build_budget_year([(c, proto)], y, sf) for y in span_years}
                   if proto else {})
        wk = by_year.get(year) or (build_budget_year([(c, proto)], year, sf) if proto else {})
        manual_wk = mb.get((c["greenhouse"], c["variety"]), {})
        if mode == "manual":
            # The typed budget wins week by week; a week nobody has opened a
            # forecast for yet still reads as the automated figure.
            wk = {**wk, **{w: v for (y2, w), v in manual_wk.items() if y2 == year}}
        annual = sum(wk.values())
        budget_total += annual
        per_week = [
            (manual_wk.get((y, w)) if mode == "manual" and (y, w) in manual_wk
             else by_year.get(y, {}).get(w))
            for y, w in pairs
        ]
        forecast_total += sum(v for v in per_week if v)

        rev = fc.get((c["greenhouse"], c["variety"]), {})
        rev_of = lambda g: [rev.get((y, w, g)) for y, w in pairs]
        mrev = mo.get((c["greenhouse"], c["variety"]), {})
        month_rev = [mrev.get(m) for m in MONTHS]

        mix = []
        if c.get("crop_protocol"):
            mix = [{"length_cm": r.length_cm, "pct": r.pct}
                   for r in frappe.get_cached_doc("Crop Protocol", c["crop_protocol"]).grade_mix or []]

        grades = []
        for row in sorted(mix, key=lambda r: r["length_cm"]):
            grades.append({
                "grade": f"{int(row['length_cm'])} cm",
                "values": [None if v is None else split_by_grade(v, mix).get(int(row["length_cm"]))
                           for v in per_week],
                "revised": rev_of(f"{int(row['length_cm'])} cm"),
            })
        if not grades:
            grades = [{"grade": "all", "values": per_week, "revised": rev_of("all")}]

        blocks.append({
            "key": c["name"],
            "greenhouse": c["greenhouse"],
            "variety": c["variety"],
            "area": c.get("planted_area") or 0,
            "rate": round(annual / c["planted_area"]) if c.get("planted_area") and annual else None,
            "budget_total": annual,
            "revision": revs.get((c["greenhouse"], c["variety"]), 1),
            "weekly": {
                "grades": grades,
                # A whole-week override sits above the grade rows.
                "revised": rev_of("all"),
                "budget": per_week,
                "actual": _actual_series(c["greenhouse"], c["variety"], pairs),
            },
            "monthly": {
                "grades": [{"grade": g["grade"],
                            "values": _to_months(annual, mix, g["grade"]),
                            "revised": [None] * 12} for g in grades],
                "revised": month_rev,
                "budget": [round(annual * w) for w in MONTH_WEIGHT],
                "actual": _monthly_actual(c["greenhouse"], c["variety"], year),
            },
            "lifetime": _lifetime(c, proto, mix, grades),
        })

    climate = weekly_climate(pairs, year)
    # Norm = median of what was actually measured, so "above/below normal"
    # means something on this farm rather than against a textbook constant.
    measured = sorted(c["light"] for c in climate.values() if c.get("source") == "measured")
    norm = round(measured[len(measured) // 2], 1) if measured else LIGHT_NORM
    # Flag a dull stretch ahead — that is when the flush slips and the
    # forecast needs a human eye.
    ahead = [climate[k]["light"] for (y, w) in pairs
             if (y, w) >= (iso_year, now_week) and (k := f"{y}-{w}") in climate]
    alert = None
    if ahead:
        avg = sum(ahead) / len(ahead)
        drop = round((norm - avg) / norm * 100)
        if drop >= 8:
            alert = f"Light sum {drop}% below normal ahead — flush likely to slip"
        elif drop <= -8:
            alert = f"Light sum {-drop}% above normal ahead — flush likely to arrive early"

    actual_weeks = [w for (y, w) in pairs if (y, w) < (iso_year, now_week)]

    # Farm-wide actuals, crop-year-to-date. The blocks only carry actuals for
    # varieties that HAVE a cycle -- stems cut for anything else would simply
    # vanish from this page, which reads as "no actuals" right after a real
    # harvest. Total everything, and name what has no cycle to land on.
    cy_start = datetime.date(today.year if today.month >= 9 else today.year - 1, 9, 1)
    harvest_rows = frappe.db.sql(
        """
        SELECT sed.item_code AS variety,
               COALESCE(NULLIF(se.custom_greenhouse, ''), sed.t_warehouse) AS greenhouse,
               SUM(sed.qty) AS stems,
               SUM(CASE WHEN se.posting_date >= %(monday)s THEN sed.qty ELSE 0 END) AS week_stems
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1 AND se.stock_entry_type LIKE '%%Harvest%%'
          AND se.posting_date BETWEEN %(start)s AND %(today)s
        GROUP BY sed.item_code, COALESCE(NULLIF(se.custom_greenhouse, ''), sed.t_warehouse)
        """,
        {"start": cy_start, "today": today,
         "monday": datetime.date.fromisocalendar(iso_year, now_week, 1)},
        as_dict=True,
    )
    covered = {(base_variety(b["variety"]).upper(), b["greenhouse"]) for b in blocks}
    unassigned: dict[tuple, int] = {}
    actual_total = week_actual_total = 0
    for r in harvest_rows:
        actual_total += int(r.stems or 0)
        week_actual_total += int(r.week_stems or 0)
        key = (base_variety(r.variety or "").upper(), r.greenhouse)
        if key not in covered:
            uk = (base_variety(r.variety or ""), r.greenhouse)
            unassigned[uk] = unassigned.get(uk, 0) + int(r.stems or 0)

    return {
        "actual_total": actual_total,
        "week_actual_total": week_actual_total,
        "unassigned_actuals": [
            {"variety": v, "greenhouse": g, "stems": s}
            for (v, g), s in sorted(unassigned.items(), key=lambda kv: -kv[1])
        ],
        "crop_year": crop_year_of(today),
        "year": year,
        "mode": mode,
        "weeks": weeks,
        "week_years": week_years,
        # Parallel to `weeks`. Nobody knows W35 means 24-30 Aug, so send both a
        # compact form for the column header and the full span for its tooltip.
        # Formatted here, not in JS, because parsing dates in the browser drags
        # timezones into a question that is purely about calendar days.
        "week_dates": [
            {
                "start": str(rng[0]),
                "end": str(rng[1]),
                "start_label": f"{rng[0].day} {rng[0]:%b}",
                "end_label": f"{rng[1].day} {rng[1]:%b}",
                "span": weekcal.week_label(y, w, rule).split(" · ", 1)[-1],
            }
            for y, w in pairs
            for rng in (weekcal.week_range(y, w, rule),)
        ],
        "week_rule": rule,
        "week_rule_label": weekcal.rule_label(rule),
        "weeks_in_year": iso_weeks_in_year(year, rule),
        "iso_year": iso_year,
        "months": MONTHS,
        "now_week": now_week,
        "now_month": MONTHS[(today.month - 9) % 12],
        "house_count": len({b["greenhouse"] for b in blocks}),
        "blocks": blocks,
        "budget_total": budget_total,
        "forecast_total": forecast_total,
        "actual_vs_budget": None,
        "actual_weeks": len(actual_weeks),
        "model_error": None,
        "model_note": "record actuals to calibrate",
        "revision": max(revs.values()) if revs else 1,
        "revision_note": None,
        "site": frappe.db.get_single_value("Global Defaults", "default_company") or "Farm",
        "climate": climate,
        "climate_note": ("Open-Meteo · Naivasha · daily max temp and shortwave "
                         "radiation averaged per ISO week"),
        "climate_alert": alert,
        "light_norm": norm,
    }


def _to_months(annual: int, mix: list, grade: str) -> list:
    """Spread an annual total over the Sep-Aug curve, then take this grade's share."""
    try:
        length = int(grade.split()[0])
    except (ValueError, IndexError):
        return [round(annual * w) for w in MONTH_WEIGHT]
    return [split_by_grade(round(annual * w), mix).get(length, 0) for w in MONTH_WEIGHT]


def _lifetime(cycle: dict, proto: dict, mix: list, grades: list) -> dict:
    """Year-by-year budget for the whole life of the planting.

    A rose block runs 5-8 years, so the annual figure alone hides the ramp at
    the start and the decline into uprooting. Crop years are Sep-Aug.
    """
    # No protocol means no yield model at all — the block appears in the grid
    # (its actuals still count) but a lifetime budget can't be derived for it.
    if not cycle.get("planting_date") or not proto:
        return {"years": [], "grades": [], "budget": [], "actual": []}
    plant = getdate(cycle["planting_date"])
    life_weeks = int(proto.get("productive_life_weeks") or 0) or 260
    end = plant + datetime.timedelta(weeks=life_weeks)

    first = plant.year if plant.month >= 9 else plant.year - 1
    last = end.year if end.month >= 9 else end.year - 1
    labels, totals = [], []
    for y in range(first, last + 1):
        # A crop year straddles two calendar years; sum both halves.
        wk = build_budget_year([(cycle, proto)], y)
        wk2 = build_budget_year([(cycle, proto)], y + 1)
        sep_dec = sum(v for w, v in wk.items() if w >= 35)
        jan_aug = sum(v for w, v in wk2.items() if w < 35)
        labels.append(f"{y}/{str(y + 1)[-2:]}")
        totals.append(sep_dec + jan_aug)
    return {
        "years": labels,
        "grades": [{"grade": g["grade"],
                    "values": [split_by_grade(t, mix).get(_len(g["grade"]), 0) if mix else t
                               for t in totals],
                    "revised": [None] * len(totals)} for g in grades],
        "budget": totals,
        "actual": [None] * len(totals),
    }


def _len(grade: str):
    try:
        return int(str(grade).split()[0])
    except (ValueError, IndexError):
        return None


def _monthly_actual(greenhouse: str, variety: str, year: int) -> list:
    """Harvest rolled into the Sep-Aug crop-year months."""
    weekly = _actual_weekly(variety, greenhouse)
    out = [0.0] * 12
    seen = [False] * 12
    for (y, w), qty in weekly.items():
        try:
            d = datetime.date.fromisocalendar(y, w, 4)
        except ValueError:
            continue
        idx = (d.month - 9) % 12
        out[idx] += qty
        seen[idx] = True
    return [int(v) if seen[i] else None for i, v in enumerate(out)]


def _actual_series(greenhouse: str, variety: str, pairs: list) -> list:
    """Actuals for (year, week) pairs — a window may straddle a year end."""
    weekly = _actual_weekly(variety, greenhouse)
    return [int(weekly[(y, w)]) if weekly.get((y, w)) else None for y, w in pairs]


@frappe.whitelist()
def set_forecast_cell(block: str, grade: str, week: int, value: int,
                      year: int | None = None, reason: str | None = None,
                      note: str | None = None) -> dict:
    """Write one (year, week, grade) into the live revision of a block's forecast.

    grade "all" (or the UI's "__all__") revises the whole week; a length grade
    revises only that grade and leaves the rest of the week alone.
    """
    week, value = int(week), int(value)
    grade = normalise_grade(grade)
    cycle = frappe.db.get_value("Crop Cycle", block, ["greenhouse", "variety"], as_dict=True)
    if not cycle:
        frappe.throw(_("Crop Cycle {0} not found.").format(block))
    year = int(year or getdate(frappe.utils.nowdate()).year)

    name = frappe.db.get_value("Production Forecast", {
        "greenhouse": cycle.greenhouse, "variety": cycle.variety,
        "forecast_year": year, "status": "Active",
    }, "name")
    if not name:
        res = revise_forecast(cycle.greenhouse, cycle.variety, year, week, FORECAST_WINDOW)
        name = res["forecast"]

    doc = frappe.get_doc("Production Forecast", name)
    for row in doc.weeks:
        if (int(row.week_number) == week
                and int(row.iso_year or year) == year
                and normalise_grade(row.grade) == grade):
            row.revised_forecast_stems = value
            if reason is not None:
                row.reason = reason
            if note is not None:
                row.note = note
            break
    else:
        doc.append("weeks", {
            "week_number": week, "iso_year": year, "grade": grade,
            "revised_forecast_stems": value, "reason": reason, "note": note,
        })
    doc.save(ignore_permissions=True)
    return {"forecast": doc.name, "revision": doc.revision,
            "week": week, "year": year, "grade": grade, "value": value}


@frappe.whitelist()
def revise_blocks(blocks, year: int | None = None, start_week: int | None = None,
                  window_weeks: int | None = None) -> dict:
    """Open a fresh revision for each block, superseding its live one.

    The grid revises a selection, not a single house — a planner reacting to a
    weather turn is revising everything they can see.
    """
    if isinstance(blocks, str):
        blocks = json.loads(blocks)
    today = getdate(frappe.utils.nowdate())
    year = int(year or today.year)
    start_week = int(start_week or today.isocalendar()[1])
    window_weeks = int(window_weeks or FORECAST_WINDOW)

    seen, done, failed = set(), [], []
    for block in blocks:
        c = frappe.db.get_value("Crop Cycle", block, ["greenhouse", "variety"], as_dict=True)
        if not c or (c.greenhouse, c.variety) in seen:
            continue
        seen.add((c.greenhouse, c.variety))
        try:
            done.append(revise_forecast(c.greenhouse, c.variety, year,
                                        start_week, window_weeks))
        except Exception as e:
            failed.append({"block": block, "error": str(e)})
    return {"revised": len(done), "revisions": done, "failed": failed}


@frappe.whitelist()
def cell_history(block: str, week: int, grade: str = "all",
                 year: int | None = None) -> dict:
    """Everything recorded about one (week, grade) cell.

    Two layers: what each revision of the forecast said, and — inside the live
    revision — each individual edit, because a planner may change their mind
    several times before a revision is closed.
    """
    week, grade = int(week), normalise_grade(grade)
    cycle = frappe.db.get_value("Crop Cycle", block, ["greenhouse", "variety"], as_dict=True)
    if not cycle:
        frappe.throw(_("Crop Cycle {0} not found.").format(block))
    year = int(year or getdate(frappe.utils.nowdate()).year)

    docs = frappe.db.get_all(
        "Production Forecast",
        filters={"greenhouse": cycle.greenhouse, "variety": cycle.variety,
                 "forecast_year": year},
        fields=["name", "revision", "status", "owner", "creation", "modified",
                "start_week", "window_weeks"],
        order_by="revision desc",
    )

    revisions, row_names, budget = [], {}, None
    for d in docs:
        row = frappe.db.get_value(
            "Production Forecast Week",
            {"parent": d["name"], "week_number": week, "grade": grade},
            ["name", "revised_forecast_stems", "manual_budget_stems", "budget_stems",
             "reason", "note"], as_dict=True)
        if not row and grade == "all":
            # rows written before the grade column existed
            row = frappe.db.get_value(
                "Production Forecast Week",
                {"parent": d["name"], "week_number": week, "grade": ("in", ["", None])},
                ["name", "revised_forecast_stems", "manual_budget_stems", "budget_stems",
                 "reason", "note"], as_dict=True)
        if not row:
            continue
        row_names[row["name"]] = d["revision"]
        if budget is None:
            budget = int(row["budget_stems"] or 0)
        # Int columns can't hold NULL, so a revision and an untouched row
        # both default to 0 -- a reason or note typed alongside it is what
        # tells a deliberate revision-to-zero apart from nothing having
        # happened yet (same convention active_forecasts() uses).
        rev = int(row["revised_forecast_stems"] or 0)
        changed = bool(rev or row["reason"] or row["note"])
        base = int(row["manual_budget_stems"] or row["budget_stems"] or 0)
        revisions.append({
            "revision": d["revision"], "status": d["status"], "forecast": d["name"],
            "value": rev if changed else base,
            "budget": int(row["budget_stems"] or 0),
            "reason": row["reason"], "note": row["note"],
            "by": d["owner"], "at": str(d["modified"]), "opened": str(d["creation"]),
            "window": f"W{d['start_week']}+{d['window_weeks']}",
            "changed": changed,
        })

    actual = _actual_weekly(cycle.variety, cycle.greenhouse).get((year, week))
    live = next((r for r in revisions if r["status"] == "Active"), None)
    return {
        "block": block, "greenhouse": cycle.greenhouse, "variety": cycle.variety,
        "week": week, "grade": grade, "year": year, "budget": budget,
        "actual": int(actual) if actual else None,
        "current": live["value"] if live else None,
        "revisions": revisions,
        "edits": _cell_edits(list(row_names), [d["name"] for d in docs]),
    }


def _cell_edits(row_names: list, doc_names: list) -> list:
    """Individual edits to those child rows, newest first, from Frappe's
    Version trail. Best effort — a purged version log is not an error."""
    if not row_names or not doc_names:
        return []
    out = []
    try:
        versions = frappe.db.get_all(
            "Version",
            filters={"ref_doctype": "Production Forecast", "docname": ("in", doc_names)},
            fields=["owner", "creation", "data"], order_by="creation desc", limit=200,
        )
    except Exception:
        return []
    wanted = set(row_names)
    for v in versions:
        try:
            data = json.loads(v["data"] or "{}")
        except Exception:
            continue
        for ch in data.get("row_changed") or []:
            # [table_fieldname, idx, child_row_name, [[field, old, new], ...]]
            if len(ch) < 4 or ch[0] != "weeks" or ch[2] not in wanted:
                continue
            for field, old, new in ch[3]:
                if field != "revised_forecast_stems":
                    continue
                out.append({"at": str(v["creation"]), "by": v["owner"],
                            "from": old, "to": new})
    return out


def normalise_grade(grade: str | None) -> str:
    """The grid says "__all__" for a whole-week edit; storage says "all"."""
    g = (grade or "").strip()
    return "all" if not g or g == "__all__" else g


def manual_month_overrides(pairs: list) -> dict:
    """{(greenhouse, variety): {month_name: stems}} typed on the Budget view.

    set_budget_cell spreads a month across its ISO weeks and flags them
    manual_override; this rolls them back up so the grid can show them.
    """
    if not pairs:
        return {}
    import datetime as _dt
    houses = sorted({h for h, _, _ in pairs})
    years = sorted({y for _, _, y in pairs})
    rows = frappe.db.sql(
        """
        SELECT pp.greenhouse, pp.variety, pp.projection_year, pw.week, pw.projected_stems
        FROM `tabProduction Projection` pp
        JOIN `tabProjection Week` pw ON pw.parent = pp.name
        WHERE pw.manual_override = 1
          AND pp.greenhouse IN %(houses)s
          AND pp.projection_year IN %(years)s
        """,
        {"houses": houses, "years": years}, as_dict=True,
    )
    out: dict = {}
    for r in rows:
        y, wk = int(r["projection_year"]), int(r["week"])
        try:
            month = _dt.date.fromisocalendar(y, wk, 4).month     # Thursday owns the week
        except ValueError:
            continue
        name = MONTHS[(month - 9) % 12]
        blk = out.setdefault((r["greenhouse"], r["variety"]), {})
        blk[name] = blk.get(name, 0) + int(r["projected_stems"] or 0)
    return out


def active_revisions(pairs: list) -> dict:
    """{(greenhouse, variety): revision} for the live forecast of each block."""
    if not pairs:
        return {}
    rows = frappe.db.get_all(
        "Production Forecast",
        filters={"status": "Active",
                 "greenhouse": ("in", sorted({h for h, _, _ in pairs})),
                 "forecast_year": ("in", sorted({y for _, _, y in pairs}))},
        fields=["greenhouse", "variety", "revision"],
    )
    return {(r["greenhouse"], r["variety"]): int(r["revision"] or 1) for r in rows}


def active_forecasts(pairs: list) -> dict:
    """{(greenhouse, variety): {(iso_year, week, grade): stems}} for live revisions.

    One query for the whole grid — a block never reads its own forecast.
    """
    if not pairs:
        return {}
    years = sorted({y for _, _, y in pairs})
    houses = sorted({h for h, _, _ in pairs})
    rows = frappe.db.sql(
        """
        SELECT pf.greenhouse, pf.variety, pf.forecast_year,
               fw.week_number, fw.iso_year, fw.grade, fw.revised_forecast_stems,
               fw.reason, fw.note
        FROM `tabProduction Forecast` pf
        JOIN `tabProduction Forecast Week` fw ON fw.parent = pf.name
        WHERE pf.status = 'Active'
          AND pf.greenhouse IN %(houses)s
          AND pf.forecast_year IN %(years)s
        """,
        {"houses": houses, "years": years}, as_dict=True,
    )
    out: dict = {}
    for r in rows:
        # Int columns can't hold NULL, so an untouched row and one revised
        # down to a deliberate zero both read back as 0 -- a reason or note
        # typed alongside it is what tells them apart.
        rev = int(r["revised_forecast_stems"] or 0)
        if not rev and not r["reason"] and not r["note"]:
            continue
        key = (r["greenhouse"], r["variety"])
        y = int(r["iso_year"] or r["forecast_year"])
        out.setdefault(key, {})[(y, int(r["week_number"]), normalise_grade(r["grade"]))] = rev
    return out


def manual_budget_map(pairs: list) -> dict:
    """{(greenhouse, variety): {(iso_year, week): stems}} — the Production
    Forecast's own budget line: what was typed by hand, falling back to its
    System Budget snapshot for any week nobody has typed over yet."""
    if not pairs:
        return {}
    years = sorted({y for _, _, y in pairs})
    houses = sorted({h for h, _, _ in pairs})
    rows = frappe.db.sql(
        """
        SELECT pf.greenhouse, pf.variety, pf.forecast_year,
               fw.week_number, fw.iso_year, fw.manual_budget_stems, fw.budget_stems
        FROM `tabProduction Forecast` pf
        JOIN `tabProduction Forecast Week` fw ON fw.parent = pf.name
        WHERE pf.status = 'Active'
          AND (fw.grade IS NULL OR fw.grade IN ('', 'all'))
          AND pf.greenhouse IN %(houses)s
          AND pf.forecast_year IN %(years)s
        """,
        {"houses": houses, "years": years}, as_dict=True,
    )
    out: dict = {}
    for r in rows:
        y = int(r["iso_year"] or r["forecast_year"])
        # Int columns can't hold NULL, so an untyped manual budget reads back
        # as 0 same as a deliberate one -- same convention _set_variances()
        # already uses for "Act - Manual". Fall back to the system snapshot.
        stems = r["manual_budget_stems"] or r["budget_stems"]
        out.setdefault((r["greenhouse"], r["variety"]), {})[(y, int(r["week_number"]))] = int(stems or 0)
    return out


# ---------------------------------------------------------------------------
# Climate
# ---------------------------------------------------------------------------

# Naivasha — where Mona's houses are. Open-Meteo is keyless and gives both
# past days and a 16-day forecast, which is what a 10-week window needs.
def default_seasonal_factors() -> dict[int, float]:
    """Calendar-month multipliers from the farm's own budget shape.

    MONTH_WEIGHT is the Sep-Aug curve out of BUDGET 2026-2027.xlsx. Normalising
    it to mean 1.0 turns it into a factor the model can apply without changing
    the annual total. February is a market shape, not a biological one - the
    Valentine's flush is timed deliberately - but it is what the farm plans to.

    A tenant that maintains Seasonal Yield Factor rows overrides this.
    """
    mean = sum(MONTH_WEIGHT) / len(MONTH_WEIGHT)
    # MONTH_WEIGHT runs Sep..Aug; calendar months are 9..12 then 1..8.
    months = list(range(9, 13)) + list(range(1, 9))
    return {m: round(w / mean, 4) for m, w in zip(months, MONTH_WEIGHT)}


FARM_LAT, FARM_LON = -0.7167, 36.4333
LIGHT_NORM = 22.0          # MJ/m2/day fallback. Equatorial highland sits far
                           # higher than temperate figures; the live norm below
                           # is derived from measurement rather than trusted.
CLIMATE_TTL = 60 * 60 * 6  # six hours


def _icon(rain_mm: float, light: float) -> str:
    """A word, not a pictograph — this is read next to numbers, not decoration."""
    if rain_mm >= 4:
        return "Wet"
    return "Bright" if light >= 17 else "Dull"


def weekly_climate(pairs: list, year: int) -> dict:
    """{"YYYY-W": {icon, temp, light, source}} for the requested (year, week) pairs.

    Daily readings are averaged into ISO weeks. Anything the API cannot cover
    falls back to the seasonal norm and is labelled as such, so the page never
    presents a guess as a measurement.
    """
    if not pairs:
        return {}
    pairs = [(int(y), int(w)) for y, w in pairs]
    key = f"uagri:climate:{pairs[0][0]}-{pairs[0][1]}:{pairs[-1][0]}-{pairs[-1][1]}"
    hit = frappe.cache().get_value(key)
    if hit:
        return hit

    import datetime as _dt
    start = _dt.date.fromisocalendar(pairs[0][0], pairs[0][1], 1)
    end = _dt.date.fromisocalendar(pairs[-1][0], pairs[-1][1], 7)
    # Open-Meteo serves ~92 days back and 16 forward; asking beyond that 400s
    # and loses the whole window, so clamp and let the tail use normals.
    today = getdate(frappe.utils.nowdate())
    start = max(start, today - _dt.timedelta(days=88))
    end = min(end, today + _dt.timedelta(days=15))
    if start > end:
        return {f"{y}-{w}": {"icon": "Normal", "temp": 23, "light": LIGHT_NORM, "source": "normal"}
                for y, w in pairs}
    out: dict = {}
    try:
        import requests
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": FARM_LAT, "longitude": FARM_LON,
                "daily": "temperature_2m_max,shortwave_radiation_sum,precipitation_sum",
                "timezone": "Africa/Nairobi",
                "start_date": start.isoformat(), "end_date": end.isoformat(),
            },
            timeout=8,
        )
        d = (r.json() or {}).get("daily") or {}
        buckets: dict = {}
        for i, day in enumerate(d.get("time") or []):
            iso = _dt.date.fromisoformat(day).isocalendar()
            b = buckets.setdefault(f"{iso[0]}-{iso[1]}", {"t": [], "l": [], "p": []})
            b["t"].append(d["temperature_2m_max"][i] or 0)
            b["l"].append(d["shortwave_radiation_sum"][i] or 0)
            b["p"].append(d["precipitation_sum"][i] or 0)
        for wk, b in buckets.items():
            if not b["t"]:
                continue
            light = round(sum(b["l"]) / len(b["l"]), 1)
            out[wk] = {
                "icon": _icon(sum(b["p"]) / len(b["p"]), light),
                "temp": round(sum(b["t"]) / len(b["t"])),
                "light": light,
                "source": "measured",
            }
    except Exception as e:
        frappe.log_error(f"weekly_climate: {e}", "upande_agriculture")

    # Weeks the API did not reach get the seasonal norm, clearly flagged.
    for y, w in pairs:
        out.setdefault(f"{y}-{w}", {"icon": "Normal", "temp": 23, "light": LIGHT_NORM, "source": "normal"})
    frappe.cache().set_value(key, out, expires_in_sec=CLIMATE_TTL)
    return out


@frappe.whitelist()
def set_budget_cell(block: str, month: str, value: int, grade: str | None = None) -> dict:
    """Override one month of a block's annual budget.

    The budget is normally derived (area × rate on the seasonal curve). Once a
    planner types a month it becomes a manual override, stored on the
    Production Projection weeks that fall inside that month so the weekly view
    stays consistent with the monthly one.
    """
    import datetime as _dt
    if month not in MONTHS:
        frappe.throw(_("Unknown month {0}.").format(month))
    value = int(value)
    cycle = frappe.db.get_value("Crop Cycle", block, ["greenhouse", "variety"], as_dict=True)
    if not cycle:
        frappe.throw(_("Crop Cycle {0} not found.").format(block))

    today = getdate(frappe.utils.nowdate())
    # Sep-Aug: months before September belong to the following calendar year.
    m_index = MONTHS.index(month)
    cal_month = ((8 + m_index) % 12) + 1
    year = today.year if cal_month >= 9 else today.year + (0 if today.month < 9 else 1)

    # A week belongs to the month containing its Thursday — the same rule the
    # read-back uses. Picking every week that merely touches the month made a
    # typed total land in two months at once.
    weeks = [wk for wk in range(1, iso_weeks_in_year(year) + 1)
             if _dt.date.fromisocalendar(year, wk, 4).month == cal_month]
    name = frappe.db.get_value("Production Projection", {
        "greenhouse": cycle.greenhouse, "variety": cycle.variety, "projection_year": year,
    }, "name")
    if not name:
        frappe.throw(_("No budget exists for {0} in {1} yet — press Rebuild first.")
                     .format(cycle.variety, cycle.greenhouse))

    doc = frappe.get_doc("Production Projection", name)
    # Spread exactly: rounding each week independently makes the month read
    # back as a different number from the one that was typed.
    n = max(len(weeks), 1)
    base, rem = divmod(value, n)
    shares = [base + (1 if i < rem else 0) for i in range(n)]
    touched = 0
    existing = {int(w.week): w for w in doc.weeks}
    for wk, share in zip(weeks, shares):
        row = existing.get(wk) or doc.append("weeks", {"week": wk})
        row.projected_stems = share
        row.manual_override = 1
        touched += 1
    doc.source = "Manual"
    doc.save(ignore_permissions=True)
    return {"projection": doc.name, "month": month, "weeks_touched": touched,
            "total": sum(shares)}
