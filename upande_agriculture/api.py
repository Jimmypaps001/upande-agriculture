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
from frappe.utils import getdate, now_datetime

from upande_agriculture.controllers import _seasonal_factor_map
from upande_agriculture.projection_calc import calculate_weekly_projection
from upande_agriculture.todo_helpers import upsert_todo


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
    """Return budget / forecast / plan / actual stems for a given ISO week."""
    iso_week = int(iso_week)
    iso_year = int(iso_year)
    monday = datetime.date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + datetime.timedelta(days=6)

    budget = _sum_projection_week(greenhouse, variety, iso_week, iso_year)
    forecast = _sum_forecast_week(greenhouse, variety, iso_week, iso_year)
    plan = _sum_plan_week(greenhouse, variety, iso_week, iso_year)
    actual = _sum_actual_harvest(greenhouse, variety, monday, sunday)
    return {
        "budget": budget,
        "forecast": forecast,
        "plan": plan,
        "actual": actual,
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
    r = frappe.db.sql(
        """
        SELECT COALESCE(SUM(fw.forecasted_stems), 0) AS s
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

    if uproot_cycle_name:
        old = frappe.get_doc("Crop Cycle", uproot_cycle_name)
        old.cycle_status = "Ended"
        old.custom_uprooting_date = getdate(planting_date)
        old.save(ignore_permissions=True)

    # Look up the protocol linked to this variety (best-effort; may be None).
    proto_name = frappe.db.get_value(
        "Crop Protocol", {"variety_item": variety}, "name"
    ) if variety else None

    cycle = frappe.get_doc({
        "doctype": "Crop Cycle",
        "greenhouse": greenhouse,
        "custom_crop_protocol": proto_name,
        "variety": variety,
        "planting_date": getdate(planting_date),
        "cycle_status": "Active",
        "breeder": trial.get("breeder"),
        "custom_flower_trial": trial_name,
    }).insert(ignore_permissions=True)

    proj_name = frappe.db.get_value(
        "Production Projection", {"crop_cycle": cycle.name}, "name"
    )
    return {"crop_cycle": cycle.name, "projection": proj_name}


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
    """Recalculate unlocked weeks for a Hybrid projection.

    Schema note: Projection Week rows use field `week` (not `week_number`).
    """
    proj = frappe.get_doc("Production Projection", projection_name)
    if proj.source == "Manual":
        frappe.throw(_(
            "Cannot recalculate a Manual projection. "
            "Switch source to Hybrid first."
        ))
    if not proj.crop_cycle:
        frappe.throw(_("Projection has no linked Crop Cycle."))

    cycle = frappe.get_doc("Crop Cycle", proj.crop_cycle)
    proto = frappe.get_doc("Crop Protocol", cycle.custom_crop_protocol)
    seasonal = _seasonal_factor_map(cycle.get("variety"))

    new_weeks = calculate_weekly_projection(
        protocol={
            "weeks_to_pinch": proto.weeks_to_pinch,
            "weeks_pinch_to_first_harvest": proto.weeks_pinch_to_first_harvest,
            "total_weeks_in_ground": proto.total_weeks_in_ground,
            "total_stems_per_plant_life": proto.total_stems_per_plant_life,
            "flush_schedule": [
                {
                    "flush_number": f.flush_number,
                    "weeks_after_pinch": f.weeks_after_pinch,
                    "stems_per_plant": f.stems_per_plant,
                }
                for f in (proto.flush_schedule or [])
            ],
        },
        plants_planted=int(
            cycle.get("custom_total_expected_stems") or proto.plants_per_sqm or 0
        ),
        planting_date=getdate(cycle.planting_date),
        seasonal_factors=seasonal,
    )

    # Index new values by week number for O(n) update.
    # calculate_weekly_projection returns dicts with key "week_number".
    by_week: dict[int, int] = {
        int(w["week_number"]): int(w["projected_stems"]) for w in new_weeks
    }

    updated = 0
    for row in proj.weeks:
        # Skip locked / manually-overridden rows in Hybrid mode.
        if proj.source == "Hybrid" and (row.is_locked or row.manual_override):
            continue
        # Projection Week child rows use field `week` (verified schema).
        row_week = int(row.week)
        new_val = by_week.get(row_week)
        if new_val is not None and int(row.projected_stems or 0) != new_val:
            row.projected_stems = new_val
            updated += 1

    proj.last_calculated_at = now_datetime()
    proj.save(ignore_permissions=True)
    return {"weeks_updated": updated}


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
    """Return all Active Crop Cycles, optionally filtered by greenhouse."""
    filters: dict[str, Any] = {"cycle_status": "Active"}
    if greenhouse:
        filters["greenhouse"] = greenhouse

    return frappe.db.get_all(
        "Crop Cycle",
        filters=filters,
        fields=[
            "name",
            "variety",
            "greenhouse",
            "planting_date",
            "custom_next_harvest_date",
            "custom_current_flush",
            "cycle_status",
        ],
        order_by="planting_date desc",
    )


# ---------------------------------------------------------------------------
# Budget spreadsheet — used by the /app/budget desk page
# ---------------------------------------------------------------------------

import re

_LENGTH_SUFFIX = re.compile(r"-\d+cm$", re.IGNORECASE)


def _variety_base(variety: str | None) -> str:
    """'Athena-50cm' -> 'Athena'.  Falls back to the input on no match."""
    if not variety:
        return ""
    return _LENGTH_SUFFIX.sub("", variety)


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

    # Group by (greenhouse, base_variety)
    groups: dict[tuple, dict] = {}
    for p in projections:
        base = _variety_base(p["variety"])
        if not base:
            continue
        key = (p["greenhouse"] or "", base)
        g = groups.setdefault(key, {
            "key": f"{p['greenhouse'] or ''}||{base}",
            "greenhouse": p["greenhouse"],
            "variety": base,
            "variants": [],
            "projections": [],
            "sources": set(),
            "weeks": [0] * 52,
        })
        g["variants"].append(p["variety"])
        g["projections"].append(p["name"])
        g["sources"].add(p["source"] or "Manual")
        for i, v in enumerate(_projection_week_array(p["name"])):
            g["weeks"][i] += v

    rows: list[dict] = []
    for (gh, base), g in groups.items():
        sources = g.pop("sources")
        g["source"] = next(iter(sources)) if len(sources) == 1 else "Mixed"
        g["total"] = sum(g["weeks"])
        if mode == "compare":
            f_arr = [0] * 52
            p_arr = [0] * 52
            a_arr = [0] * 52
            for variant in g["variants"]:
                for i, v in enumerate(_forecast_week_array(gh, variant, year)):
                    f_arr[i] += v
                for i, v in enumerate(_plan_week_array(gh, variant, year)):
                    p_arr[i] += v
                for i, v in enumerate(_actual_week_array(gh, variant, year)):
                    a_arr[i] += v
            g["forecast"] = f_arr
            g["plan"] = p_arr
            g["actual"] = a_arr
        rows.append(g)

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
    weeks = [0] * 52
    for r in rows:
        w = int(r["week"] or 0)
        if 1 <= w <= 52:
            weeks[w - 1] = int(r["projected_stems"] or 0)
    return weeks


def _forecast_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    rows = frappe.db.sql(
        """
        SELECT fw.week_number, fw.forecasted_stems
        FROM `tabProduction Forecast` pf
        JOIN `tabProduction Forecast Week` fw ON fw.parent = pf.name
        WHERE pf.greenhouse = %s AND pf.variety = %s
          AND pf.forecast_year = %s AND pf.status = 'Active'
        """,
        (greenhouse, variety, year),
        as_dict=True,
    )
    weeks = [0] * 52
    for r in rows:
        w = int(r["week_number"] or 0)
        if 1 <= w <= 52:
            weeks[w - 1] = int(r["forecasted_stems"] or 0)
    return weeks


def _plan_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    rows = frappe.db.sql(
        """
        SELECT pf.plan_week, SUM(pv.planned_stems) AS s
        FROM `tabProduction Plan Form` pf
        JOIN `tabProduction Plan Variety` pv ON pv.parent = pf.name
        WHERE pf.greenhouse = %s AND pv.variety = %s
          AND pf.plan_year = %s AND pf.docstatus < 2
        GROUP BY pf.plan_week
        """,
        (greenhouse, variety, year),
        as_dict=True,
    )
    weeks = [0] * 52
    for r in rows:
        w = int(r["plan_week"] or 0)
        if 1 <= w <= 52:
            weeks[w - 1] = int(r["s"] or 0)
    return weeks


def _actual_week_array(greenhouse: str, variety: str, year: int) -> list[int]:
    if not frappe.db.has_table("tabActual Harvest"):
        return [0] * 52
    rows = frappe.db.sql(
        """
        SELECT WEEK(harvest_date, 3) AS w, SUM(stems) AS s
        FROM `tabActual Harvest`
        WHERE warehouse = %s AND variety = %s AND YEAR(harvest_date) = %s
        GROUP BY WEEK(harvest_date, 3)
        """,
        (greenhouse, variety, year),
        as_dict=True,
    )
    weeks = [0] * 52
    for r in rows:
        w = int(r["w"] or 0)
        if 1 <= w <= 52:
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

    # Group by (greenhouse, variety_base, year) so we open each set of
    # projections once.
    by_group: dict[tuple, list[dict]] = {}
    for u in updates or []:
        key = (u.get("greenhouse") or "", u["variety_base"], int(u["year"]))
        by_group.setdefault(key, []).append(u)

    for (gh, vbase, year), changes in by_group.items():
        # Find underlying projections (all length variants of `vbase`).
        projs = frappe.db.sql(
            """SELECT name FROM `tabProduction Projection`
               WHERE projection_year = %s
                 AND (greenhouse <=> %s)
                 AND (variety = %s OR variety LIKE %s)
            """,
            (year, gh or None, vbase, f"{vbase}-%"),
            as_dict=True,
        )
        if not projs:
            continue
        docs = [frappe.get_doc("Production Projection", p["name"]) for p in projs]

        for ch in changes:
            wnum = int(ch["week"])
            new_total = int(ch["value"] or 0)
            current = []
            for d in docs:
                # find the row for this week (or 0 if not yet present)
                v = next((int(w.projected_stems or 0) for w in d.weeks if int(w.week or 0) == wnum), 0)
                current.append(v)
            cur_sum = sum(current)

            if cur_sum > 0:
                # Proportional redistribution.
                new_values = [round(c * new_total / cur_sum) for c in current]
                # Fix rounding drift by absorbing into the largest contributor.
                drift = new_total - sum(new_values)
                if drift and new_values:
                    idx = new_values.index(max(new_values))
                    new_values[idx] += drift
            else:
                # All zeros: split equally, drift on the first.
                n = len(docs)
                per = new_total // n
                rem = new_total - per * n
                new_values = [per + (1 if i < rem else 0) for i in range(n)]

            for d, v in zip(docs, new_values):
                row = next((w for w in d.weeks if int(w.week or 0) == wnum), None)
                if not row:
                    row = d.append("weeks", {"week": wnum})
                row.projected_stems = v
                if d.source == "Hybrid":
                    row.manual_override = 1
                touched_projections.add(d.name)
                week_count += 1

        for d in docs:
            d.save(ignore_permissions=True)

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
        vbase = r["variety_base"]
        year = int(r["year"])
        projs = frappe.db.sql_list(
            """SELECT name FROM `tabProduction Projection`
               WHERE projection_year=%s AND (greenhouse <=> %s)
                 AND (variety=%s OR variety LIKE %s)""",
            (year, gh, vbase, f"{vbase}-%"),
        )
        for p in projs:
            frappe.db.set_value("Production Projection", p, "source", source,
                                 update_modified=False)
            if source != "Manual":
                try:
                    regenerate_projection(p)
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

    src_weeks = [0] * 52
    src_projs = frappe.db.sql_list(
        """SELECT name FROM `tabProduction Projection`
           WHERE projection_year=%s AND (greenhouse <=> %s)
             AND (variety=%s OR variety LIKE %s)""",
        (year, source_greenhouse or None, source_variety_base,
         f"{source_variety_base}-%"),
    )
    for p in src_projs:
        for i, v in enumerate(_projection_week_array(p)):
            src_weeks[i] += v

    if not any(src_weeks):
        frappe.throw(_("Source row has no data to copy."))

    # Find the target projections. If none exist, refuse — creating them
    # requires picking lengths and a Crop Cycle, which is more than this
    # endpoint should do silently.
    tgt_projs = frappe.db.sql_list(
        """SELECT name FROM `tabProduction Projection`
           WHERE projection_year=%s AND (greenhouse <=> %s)
             AND (variety=%s OR variety LIKE %s)""",
        (year, target_greenhouse or None, target_variety_base,
         f"{target_variety_base}-%"),
    )
    if not tgt_projs:
        frappe.throw(_("No matching projections at target — create a Crop Cycle there first."))

    # Distribute weekly source totals across target projections proportionally
    # to their current weekly values (or equally when target is all-zero).
    updates = []
    for w in range(1, 53):
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
    """Per-(greenhouse, variety_base) prior-year actuals for overlay charts."""
    prev = int(year) - 1
    if not frappe.db.has_table("tabActual Harvest"):
        return {"year": prev, "rows": {}}
    rows = frappe.db.sql(
        """
        SELECT warehouse, variety, WEEK(harvest_date, 3) AS w,
               SUM(stems) AS s
        FROM `tabActual Harvest`
        WHERE YEAR(harvest_date) = %s
        GROUP BY warehouse, variety, w
        """,
        (prev,),
        as_dict=True,
    )
    out: dict[str, list[int]] = {}
    for r in rows:
        base = _variety_base(r["variety"])
        key = f"{r['warehouse'] or ''}||{base}"
        arr = out.setdefault(key, [0] * 52)
        w = int(r["w"] or 0)
        if 1 <= w <= 52:
            arr[w - 1] += int(r["s"] or 0)
    return {"year": prev, "rows": out}

