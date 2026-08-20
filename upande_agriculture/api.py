"""Whitelisted endpoints for the mona-shelve mobile app.

Schema corrections applied vs. brief:
- Projection Week: uses field `week` (not `week_number`).
- Production Forecast Week: uses `week_number` (confirmed correct).
- regenerate_projection: iterates proj.weeks rows using row.week (not row.week_number).
- submit_production_plan: uses insert() only — Production Plan Form is not submittable.
- promote_trial_to_cycle: Flower Trial has no varieties child table on mona2.
  Uses scalar field `variety_yield` instead of `varieties_tested[0].variety`.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import getdate

from upande_agriculture import weekcal
from upande_agriculture.projection_calc import iso_weeks_in_year

from upande_agriculture.controllers import _seasonal_factor_map
from upande_agriculture.todo_helpers import upsert_todo

# Grid arrays are fixed width; 53 covers every ISO year (2026 is one of them).
MAX_ISO_WEEK = 53


# ---------------------------------------------------------------------------
# 1. get_week_summary
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_week_summary(
    greenhouse: str,
    variety: str,
    iso_week: int,
    iso_year: int,
) -> dict:
    """Return budget / forecast / plan / actual stems for a given week.

    Also returns the calendar dates that week covers. The week number alone is
    unreadable — W35 of 2026 is 24–30 August — and the range was already being
    computed here to bound the actuals query, so it costs nothing to hand back.

    The dates use the rule stamped on the projection being summarised, not
    today's setting, so an older budget keeps reporting the dates it was built
    against.
    """
    iso_week = int(iso_week)
    iso_year = int(iso_year)
    rule = frappe.db.get_value(
        "Production Projection",
        {"greenhouse": greenhouse, "variety": variety, "projection_year": iso_year},
        "week_rule",
    ) or weekcal.get_week_rule()
    monday, sunday = weekcal.week_range(iso_year, iso_week, rule)

    budget = _sum_projection_week(greenhouse, variety, iso_week, iso_year)
    forecast = _sum_forecast_week(greenhouse, variety, iso_week, iso_year)
    plan = _sum_plan_week(greenhouse, variety, iso_week, iso_year)
    actual = _sum_actual_harvest(greenhouse, variety, monday, sunday)
    return {
        "budget": budget,
        "forecast": forecast,
        "plan": plan,
        "actual": actual,
        "week_start": str(monday),
        "week_end": str(sunday),
        "week_label": weekcal.week_label(iso_year, iso_week, rule),
        "week_rule": rule,
    }


def _sum_projection_week(gh: str, variety: str, w: int, y: int) -> int:
    # Projection Week child table uses field `week` (verified via tabDocField).
    r = frappe.db.sql(
        """
        SELECT COALESCE(SUM(pw.projected_stems), 0) AS s
        FROM `tabProduction Projection` pp
        JOIN `tabProjection Week` pw ON pw.parent = pp.name
        WHERE pp.greenhouse = %s AND pp.variety = %s
          AND pp.projection_year = %s AND pw.week = %s
        """,
        (gh, variety, y, w),
        as_dict=True,
    )
    return int(r[0]["s"] if r else 0)


def _sum_forecast_week(gh: str, variety: str, w: int, y: int) -> int:
    # Production Forecast Week child table uses field `week_number` (verified).
    # A revised forecast (typed after submit) is the latest call for the week
    # and supersedes the original number; 0/unset means "not revised".
    r = frappe.db.sql(
        """
        SELECT COALESCE(SUM(
            CASE WHEN fw.revised_forecast_stems > 0
                 THEN fw.revised_forecast_stems
                 ELSE fw.forecasted_stems END), 0) AS s
        FROM `tabProduction Forecast` pf
        JOIN `tabProduction Forecast Week` fw ON fw.parent = pf.name
        WHERE pf.greenhouse = %s AND pf.variety = %s
          AND pf.forecast_year = %s AND fw.week_number = %s
          AND pf.status = 'Active'
        """,
        (gh, variety, y, w),
        as_dict=True,
    )
    return int(r[0]["s"] if r else 0)


def _sum_plan_week(gh: str, variety: str, w: int, y: int) -> int:
    r = frappe.db.sql(
        """
        SELECT COALESCE(SUM(pv.planned_stems), 0) AS s
        FROM `tabProduction Plan Form` pf
        JOIN `tabProduction Plan Variety` pv ON pv.parent = pf.name
        WHERE pf.greenhouse = %s AND pv.variety = %s
          AND pf.plan_year = %s AND pf.plan_week = %s
          AND pf.docstatus < 2
        """,
        (gh, variety, y, w),
        as_dict=True,
    )
    return int(r[0]["s"] if r else 0)


def _sum_actual_harvest(
    gh: str,
    variety: str,
    start: datetime.date,
    end: datetime.date,
) -> int:
    if not frappe.db.has_table("tabActual Harvest"):
        return 0
    r = frappe.db.sql(
        """
        SELECT COALESCE(SUM(stems), 0) AS s
        FROM `tabActual Harvest`
        WHERE warehouse = %s AND variety = %s
          AND harvest_date BETWEEN %s AND %s
        """,
        (gh, variety, start, end),
        as_dict=True,
    )
    return int(r[0]["s"] if r else 0)


# ---------------------------------------------------------------------------
# 2. promote_trial_to_cycle
# ---------------------------------------------------------------------------

@frappe.whitelist()
def promote_trial_to_cycle(
    trial_name: str,
    greenhouse: str,
    planting_date: str,
    uproot_cycle_name: str | None = None,
) -> dict:
    """Create a Crop Cycle from an approved Flower Trial.

    Schema note: Flower Trial on mona2 has no varieties child table.
    The variety is read from the scalar field `variety_yield`.
    """
    trial = frappe.get_doc("Flower Trial", trial_name)
    if (trial.get("recommendation") or "").strip() != "Approve for Production":
        frappe.throw(_("Trial is not approved for production."))

    variety = trial.get("variety_yield")

    # Look up the protocol linked to this variety (best-effort; may be None).
    proto_name = frappe.db.get_value(
        "Crop Protocol", {"variety_item": variety}, "name"
    ) if variety else None

    # A new planting ends the cycle it replaces.
    if uproot_cycle_name:
        frappe.db.set_value("Crop Cycle", uproot_cycle_name, {
            "status": "Ended",
            "cycle_end_date": getdate(planting_date),
        })

    cycle = frappe.get_doc({
        "doctype": "Crop Cycle",
        "greenhouse": greenhouse,
        "variety": variety,
        "crop_protocol": proto_name,
        "planting_date": getdate(planting_date),
        "status": "Active",
        "breeder": trial.get("breeder"),
        "notes": f"From Flower Trial {trial_name}",
    }).insert(ignore_permissions=True)

    return {"crop_cycle": cycle.name, "greenhouse": greenhouse}


# ---------------------------------------------------------------------------
# 3. mark_cycle_harvestable
# ---------------------------------------------------------------------------

@frappe.whitelist()
def mark_cycle_harvestable(crop_cycle_name: str) -> dict:
    """Ensure an Item exists for the cycle's variety and return its name."""
    cycle = frappe.get_doc("Crop Cycle", crop_cycle_name)
    variety = cycle.get("variety")
    if not variety:
        frappe.throw(_("Crop Cycle has no variety set."))

    item_code = variety
    if not frappe.db.exists("Item", variety):
        item_group = (
            "Flower"
            if frappe.db.exists("Item Group", "Flower")
            else "All Item Groups"
        )
        item_doc = frappe.get_doc({
            "doctype": "Item",
            "item_code": variety,
            "item_name": variety,
            "item_group": item_group,
            "stock_uom": "Nos",
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        item_code = item_doc.name

    return {"item": item_code}


# ---------------------------------------------------------------------------
# 4. regenerate_projection
# ---------------------------------------------------------------------------

@frappe.whitelist()
def regenerate_projection(projection_name: str) -> dict:
    """Rebuild a projection's weeks from the crop cycles behind it.

    Thin wrapper over budget.generate_budget, kept because the Production
    Budget page calls this name. Locked / manually-overridden weeks survive.
    """
    from upande_agriculture.budget import generate_budget

    proj = frappe.get_doc("Production Projection", projection_name)
    if proj.source == "Manual":
        frappe.throw(_(
            "Cannot recalculate a Manual projection. "
            "Switch source to Hybrid first."
        ))
    if not proj.greenhouse:
        frappe.throw(_("Projection has no greenhouse — cannot find its crop cycles."))

    res = generate_budget(proj.greenhouse, proj.variety, proj.projection_year)
    return {"weeks_updated": res["weeks_written"], **res}


# ---------------------------------------------------------------------------
# 5. submit_production_plan
# ---------------------------------------------------------------------------

@frappe.whitelist()
def submit_production_plan(payload: str | dict) -> dict:
    """Insert a Production Plan Form from a JSON payload.

    Note: Production Plan Form is NOT submittable (docstatus never set to 1).
    We use insert() only; the on_update hook creates ToDos automatically.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    plan = frappe.get_doc({"doctype": "Production Plan Form", **payload})
    plan.insert(ignore_permissions=True, ignore_mandatory=True)

    todos = frappe.db.count(
        "ToDo",
        {
            "reference_type": "Production Plan Form",
            "reference_name": plan.name,
        },
    )
    return {"production_plan_form": plan.name, "todos_created": int(todos)}


# ---------------------------------------------------------------------------
# 6. list_active_cycles
# ---------------------------------------------------------------------------

@frappe.whitelist()
def list_active_cycles(greenhouse: str | None = None) -> list[dict]:
    """Return all Active Crop Cycles, optionally filtered by greenhouse.

    """
    filters: dict[str, Any] = {"status": "Active"}
    if greenhouse:
        filters["greenhouse"] = greenhouse

    rows = frappe.db.get_all(
        "Crop Cycle",
        filters=filters,
        fields=[
            "name",
            "greenhouse",
            "variety",
            "crop_protocol",
            "planting_date",
            "first_bending_date",
            "second_bending_date",
            "planned_uprooting_date",
            "qty_planted",
            "status",
        ],
        order_by="planting_date desc",
    )
    return rows


# ---------------------------------------------------------------------------
# Budget spreadsheet — used by the /app/budget desk page
# ---------------------------------------------------------------------------

import re

_LENGTH_SUFFIX = re.compile(r"-\d+cm$", re.IGNORECASE)


def _variety_base(variety: str | None) -> str:
    """'Athena-50cm' -> 'Athena'.  Falls back to the input on no match.

    Used only for legacy / display fall-backs. The canonical mapping
    from variant to template comes from ERPNext's Item.variant_of.
    """
    if not variety:
        return ""
    return _LENGTH_SUFFIX.sub("", variety)


def _variant_codes(template: str) -> list[str]:
    """Item codes that belong to this variety template (template itself +
    every Item whose `variant_of` points at it). Used to aggregate harvest
    receipts (which are posted against the length-specific variant) up to
    the template-keyed Projection."""
    if not template:
        return []
    rows = frappe.db.sql_list(
        "SELECT name FROM `tabItem` WHERE name = %s OR variant_of = %s",
        (template, template),
    )
    return list(rows) or [template]


@frappe.whitelist()
def get_budget_grid(year: int, mode: str = "compact") -> dict:
    """
    Return the annual budget grid for a year, aggregated by (greenhouse,
    base variety). One row sums all length variants (e.g. Athena-50cm +
    Athena-60cm + Athena-70cm + Athena-110cm all roll up into 'Athena').

    Returns:
        {
            "year": 2026,
            "rows": [
                {
                    "key": "Main GH 01 - MFL||Athena",     # stable id
                    "greenhouse": "Main GH 01 - MFL",
                    "variety": "Athena",                   # base, no length
                    "variants": ["Athena-50cm","Athena-60cm", ...],
                    "projections": ["PP-...", "PP-...", ...],
                    "source": "Manual" | "Hybrid" | "Mixed",
                    "weeks": [int x 52],                   # budget, summed
                    "forecast": [int x 52] | None,         # only in compare mode
                    "plan": [int x 52] | None,
                    "actual": [int x 52] | None,
                    "total": int,
                },
                ...
            ],
            "month_offsets": [1, 5, 9, 14, 18, 22, 27, 31, 35, 40, 44, 48],
            "month_labels":  ["Jan",...],
        }
    """
    year = int(year)
    # After the variant→template migration, each row in the spreadsheet
    # corresponds 1:1 with a Production Projection keyed by variety template.
    projections = frappe.db.sql(
        """
        SELECT pp.name, pp.greenhouse, pp.variety, pp.source
        FROM `tabProduction Projection` pp
        WHERE pp.projection_year = %s
        ORDER BY pp.greenhouse, pp.variety
        """,
        (year,),
        as_dict=True,
    )

    rows: list[dict] = []
    for p in projections:
        gh = p["greenhouse"]
        template = p["variety"]
        row = {
            "key": f"{gh or ''}||{template}",
            "greenhouse": gh,
            "variety": template,
            "projection": p["name"],
            "variants": _variant_codes(template),
            "source": p["source"] or "Manual",
            "weeks": _projection_week_array(p["name"]),
            # Actuals always included — used by heatmap shading + variance %
            # in both modes, not just compare.
            "actual": _actual_week_array(gh, template, year),
        }
        row["total"] = sum(row["weeks"])
        if mode == "compare":
            row["forecast"] = _forecast_week_array(gh, template, year)
            row["plan"] = _plan_week_array(gh, template, year)
        rows.append(row)

    rows.sort(key=lambda r: (r["greenhouse"] or "ZZZ", r["variety"]))

    return {
        "year": year,
        "rows": rows,
        "month_offsets": [1, 5, 9, 14, 18, 22, 27, 31, 35, 40, 44, 48],
        "month_labels": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
    }


def _projection_week_array(projection_name: str) -> list[int]:
    rows = frappe.db.sql(
        """
        SELECT week, projected_stems
        FROM `tabProjection Week`
        WHERE parent = %s
        """,
        (projection_name,),
        as_dict=True,
    )
    weeks = [0] * MAX_ISO_WEEK
    for r in rows:
        w = int(r["week"] or 0)
        if 1 <= w <= MAX_ISO_WEEK:
            weeks[w - 1] = int(r["projected_stems"] or 0)
    return weeks


def _forecast_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    """Sum forecasted stems across all variants of `variety` (treated as a
    template). Forecasts in mona2 were imported per-variant (e.g.
    `Ever-Red-50cm`), so we look up the template's variant codes and SUM."""
    codes = _variant_codes(variety)
    if not codes:
        return [0] * MAX_ISO_WEEK
    placeholders = ", ".join(["%s"] * len(codes))
    rows = frappe.db.sql(
        f"""
        SELECT fw.week_number, SUM(
            CASE WHEN fw.revised_forecast_stems > 0
                 THEN fw.revised_forecast_stems
                 ELSE fw.forecasted_stems END) AS s
        FROM `tabProduction Forecast` pf
        JOIN `tabProduction Forecast Week` fw ON fw.parent = pf.name
        WHERE pf.greenhouse = %s AND pf.variety IN ({placeholders})
          AND pf.forecast_year = %s AND pf.status = 'Active'
        GROUP BY fw.week_number
        """,
        (greenhouse, *codes, year),
        as_dict=True,
    )
    weeks = [0] * MAX_ISO_WEEK
    for r in rows:
        w = int(r["week_number"] or 0)
        if 1 <= w <= MAX_ISO_WEEK:
            weeks[w - 1] = int(r["s"] or 0)
    return weeks


def _plan_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    """Same as forecast — sum across the template's variants."""
    codes = _variant_codes(variety)
    if not codes:
        return [0] * MAX_ISO_WEEK
    placeholders = ", ".join(["%s"] * len(codes))
    rows = frappe.db.sql(
        f"""
        SELECT pf.plan_week, SUM(pv.planned_stems) AS s
        FROM `tabProduction Plan Form` pf
        JOIN `tabProduction Plan Variety` pv ON pv.parent = pf.name
        WHERE pf.greenhouse = %s AND pv.variety IN ({placeholders})
          AND pf.plan_year = %s AND pf.docstatus < 2
        GROUP BY pf.plan_week
        """,
        (greenhouse, *codes, year),
        as_dict=True,
    )
    weeks = [0] * MAX_ISO_WEEK
    for r in rows:
        w = int(r["plan_week"] or 0)
        if 1 <= w <= MAX_ISO_WEEK:
            weeks[w - 1] = int(r["s"] or 0)
    return weeks


_GH_PREFIX = re.compile(r"^(Main GH \d{2})")


def _gh_prefix(greenhouse: str | None) -> str | None:
    """`Main GH 01 - MFL` -> `Main GH 01`, used to match against MFK twin."""
    if not greenhouse:
        return None
    m = _GH_PREFIX.match(greenhouse)
    return m.group(1) if m else greenhouse


def _actual_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    """Sum harvested stems per ISO week using the same source the Harvest
    Dashboard reads — `stock_entry_type='Harvesting'` rows keyed by
    `se.custom_greenhouse`, excluding QC-rejected entries (those have
    `custom_quality_section` populated).

    The harvest dashboard normalises `custom_greenhouse` by stripping
    the trailing ` - MFK` suffix before comparison; we apply the same
    normalisation so we can match `Main GH 01 - MFL` projections to
    `Main GH 01 - MFK` harvest entries via the shared prefix.
    """
    prefix = _gh_prefix(greenhouse)
    if not prefix:
        return [0] * MAX_ISO_WEEK
    codes = _variant_codes(variety)
    if not codes:
        return [0] * MAX_ISO_WEEK
    placeholders = ", ".join(["%s"] * len(codes))
    rows = frappe.db.sql(
        f"""
        SELECT WEEK(se.posting_date, 3) AS w, SUM(sed.qty) AS s
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.stock_entry_type = 'Harvesting'
          AND (se.custom_quality_section IS NULL OR se.custom_quality_section = '')
          AND TRIM(REPLACE(se.custom_greenhouse, ' - MFK', '')) = %s
          AND sed.item_code IN ({placeholders})
          AND YEAR(se.posting_date) = %s
        GROUP BY WEEK(se.posting_date, 3)
        """,
        (prefix, *codes, year),
        as_dict=True,
    )
    weeks = [0] * MAX_ISO_WEEK
    for r in rows:
        w = int(r["w"] or 0)
        if 1 <= w <= MAX_ISO_WEEK:
            weeks[w - 1] = int(r["s"] or 0)
    return weeks


@frappe.whitelist()
def update_projection_week(projection: str, week: int, value: int) -> dict:
    """Update one cell. Sets manual_override=1 if the row is Hybrid."""
    week = int(week); value = int(value)
    proj = frappe.get_doc("Production Projection", projection)
    target = None
    for w in proj.weeks:
        if int(w.week or 0) == week:
            target = w
            break
    if not target:
        target = proj.append("weeks", {"week": week})
    target.projected_stems = value
    if proj.source == "Hybrid":
        target.manual_override = 1
    proj.save(ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def bulk_update_projection_weeks(updates: str | list[dict]) -> dict:
    """Apply many cell changes in one save per projection.

    updates: list of {"projection": str, "week": int, "value": int}
    """
    if isinstance(updates, str):
        updates = json.loads(updates)
    by_proj: dict[str, list[dict]] = {}
    for u in updates or []:
        by_proj.setdefault(u["projection"], []).append(u)

    touched = 0
    for proj_name, changes in by_proj.items():
        proj = frappe.get_doc("Production Projection", proj_name)
        week_map = {int(w.week or 0): w for w in proj.weeks}
        for ch in changes:
            w_num = int(ch["week"])
            row = week_map.get(w_num) or proj.append("weeks", {"week": w_num})
            row.projected_stems = int(ch["value"] or 0)
            if proj.source == "Hybrid":
                row.manual_override = 1
            week_map[w_num] = row
            touched += 1
        proj.save(ignore_permissions=True)

    return {"ok": True, "touched": touched}


@frappe.whitelist()
def set_projection_source(projection: str, source: str) -> dict:
    """Toggle a single Projection's source (Manual / Hybrid / Calculated)."""
    if source not in ("Manual", "Hybrid", "Calculated from Protocol"):
        frappe.throw(_("Invalid source: {0}").format(source))
    frappe.db.set_value("Production Projection", projection, "source", source)
    if source != "Manual":
        # Recalculate immediately so the new weeks land.
        try:
            regenerate_projection(projection)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "set_projection_source recalc")
    return {"ok": True, "source": source}


# ---------------------------------------------------------------------------
# Aggregated-row write path (one grid row covers many length variants)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def bulk_update_aggregated_weeks(updates: str | list[dict]) -> dict:
    """
    Apply many aggregated-cell changes. Each update specifies the
    aggregated (greenhouse, base variety, week) and a new total — we
    distribute the delta proportionally across the underlying length
    variants. When all variants are zero at that week, the value is
    split equally.

    updates: list of {"greenhouse": str, "variety_base": str, "year": int,
                       "week": int, "value": int}
    """
    if isinstance(updates, str):
        updates = json.loads(updates)
    touched_projections: set[str] = set()
    week_count = 0

    # Group updates by (greenhouse, variety_template, year) → one Projection.
    by_group: dict[tuple, list[dict]] = {}
    for u in updates or []:
        key = (u.get("greenhouse") or "", u["variety_base"], int(u["year"]))
        by_group.setdefault(key, []).append(u)

    for (gh, template, year), changes in by_group.items():
        proj_name = frappe.db.get_value("Production Projection", {
            "projection_year": year,
            "greenhouse": gh or None,
            "variety": template,
        }, "name")
        if not proj_name:
            # No existing projection for this (gh, template, year) — create
            # one so the operator's edits don't get silently dropped.
            doc = frappe.get_doc({
                "doctype": "Production Projection",
                "greenhouse": gh or None,
                "variety": template,
                "projection_year": year,
                "source": "Manual",
            })
        else:
            doc = frappe.get_doc("Production Projection", proj_name)

        week_map = {int(w.week or 0): w for w in doc.weeks}
        for ch in changes:
            wnum = int(ch["week"])
            new_val = int(ch["value"] or 0)
            row = week_map.get(wnum)
            if not row:
                row = doc.append("weeks", {"week": wnum})
                week_map[wnum] = row
            row.projected_stems = new_val
            if doc.source == "Hybrid":
                row.manual_override = 1
            week_count += 1

        if proj_name:
            doc.save(ignore_permissions=True)
        else:
            doc.insert(ignore_permissions=True)
        touched_projections.add(doc.name)

    return {"ok": True, "projections_touched": len(touched_projections),
            "weeks_touched": week_count}


@frappe.whitelist()
def bulk_apply_formula(updates: str | list[dict], operation: str,
                        operand: float) -> dict:
    """
    Apply an arithmetic operation to a batch of aggregated cells.

    operation: "add" | "subtract" | "multiply" | "percent_add" | "percent_sub" | "set"
    operand: numeric value
    updates: list of {greenhouse, variety_base, year, week, current}

    Returns the new aggregated values so the client can update the grid.
    """
    if isinstance(updates, str):
        updates = json.loads(updates)
    op = operation
    val = float(operand)

    def _apply(cur: float) -> int:
        if op == "add":
            return int(round(cur + val))
        if op == "subtract":
            return int(round(max(0, cur - val)))
        if op == "multiply":
            return int(round(max(0, cur * val)))
        if op == "percent_add":
            return int(round(cur * (1 + val / 100.0)))
        if op == "percent_sub":
            return int(round(max(0, cur * (1 - val / 100.0))))
        if op == "set":
            return int(round(val))
        frappe.throw(_("Unknown operation: {0}").format(op))

    new_updates = []
    out = []
    for u in updates or []:
        new_val = _apply(float(u.get("current") or 0))
        new_updates.append({
            "greenhouse": u.get("greenhouse"),
            "variety_base": u["variety_base"],
            "year": u["year"],
            "week": u["week"],
            "value": new_val,
        })
        out.append({**u, "value": new_val})

    res = bulk_update_aggregated_weeks(new_updates)
    return {"ok": True, "applied": out, **res}


@frappe.whitelist()
def bulk_set_aggregated_source(rows: str | list[dict], source: str) -> dict:
    """Apply a source change to every projection under N aggregated rows.

    rows: [{greenhouse, variety_base, year}]
    """
    if isinstance(rows, str):
        rows = json.loads(rows)
    if source not in ("Manual", "Hybrid", "Calculated from Protocol"):
        frappe.throw(_("Invalid source: {0}").format(source))
    touched = 0
    for r in rows or []:
        gh = r.get("greenhouse") or None
        template = r["variety_base"]
        year = int(r["year"])
        proj_name = frappe.db.get_value("Production Projection", {
            "projection_year": year,
            "greenhouse": gh,
            "variety": template,
        }, "name")
        if not proj_name:
            continue
        frappe.db.set_value("Production Projection", proj_name, "source",
                             source, update_modified=False)
        if source != "Manual":
            try:
                regenerate_projection(proj_name)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "bulk_set source recalc")
        touched += 1
    frappe.db.commit()
    return {"ok": True, "projections_touched": touched}


@frappe.whitelist()
def copy_aggregated_row(source_greenhouse: str, source_variety_base: str,
                         target_greenhouse: str, year: int,
                         target_variety_base: str = "") -> dict:
    """
    Copy a full 52-week pattern from one (greenhouse, variety) to another.
    Creates Projection records on the target side if they don't exist.

    If target_variety_base is empty, the variety stays the same.
    """
    year = int(year)
    target_variety_base = target_variety_base or source_variety_base

    src_proj = frappe.db.get_value("Production Projection", {
        "projection_year": year,
        "greenhouse": source_greenhouse or None,
        "variety": source_variety_base,
    }, "name")
    if not src_proj:
        frappe.throw(_("No source projection found."))
    src_weeks = _projection_week_array(src_proj)

    if not any(src_weeks):
        frappe.throw(_("Source row has no data to copy."))

    # Write to the target template projection (creating it if missing).
    updates = []
    for w in range(1, MAX_ISO_WEEK + 1):
        updates.append({
            "greenhouse": target_greenhouse,
            "variety_base": target_variety_base,
            "year": year,
            "week": w,
            "value": src_weeks[w - 1],
        })
    res = bulk_update_aggregated_weeks(updates)
    return {"ok": True, "weeks_copied": 52, **res}


@frappe.whitelist()
def get_prior_year_actuals(year: int) -> dict:
    """Prior-year actuals per (greenhouse, base variety) for overlay charts.

    Mirrors the Harvest Dashboard's canonical source: Harvesting Stock
    Entries with `custom_quality_section` empty (i.e. not QC-rejected).
    """
    prev = int(year) - 1
    rows = frappe.db.sql(
        """
        SELECT TRIM(REPLACE(se.custom_greenhouse, ' - MFK', '')) AS gh_prefix,
               sed.item_code AS variety,
               WEEK(se.posting_date, 3) AS w,
               SUM(sed.qty) AS s
        FROM `tabStock Entry` se
        JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
        WHERE se.docstatus = 1
          AND se.stock_entry_type = 'Harvesting'
          AND (se.custom_quality_section IS NULL OR se.custom_quality_section = '')
          AND se.custom_greenhouse IS NOT NULL
          AND sed.item_code LIKE '%%cm'
          AND YEAR(se.posting_date) = %s
        GROUP BY gh_prefix, sed.item_code, w
        """,
        (prev,),
        as_dict=True,
    )
    out: dict[str, list[int]] = {}
    for r in rows:
        base = _variety_base(r["variety"])
        prefix = r["gh_prefix"] or ""
        # Index by both MFL and MFK candidates so the JS lookup works no
        # matter which suffix the projection greenhouse uses.
        for suffix in ("MFL", "MFK"):
            key = f"{prefix} - {suffix}||{base}"
            arr = out.setdefault(key, [0] * MAX_ISO_WEEK)
            w = int(r["w"] or 0)
            if 1 <= w <= MAX_ISO_WEEK:
                arr[w - 1] += int(r["s"] or 0)
    return {"year": prev, "rows": out}

