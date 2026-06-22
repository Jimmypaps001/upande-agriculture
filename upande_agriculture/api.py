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

@frappe.whitelist()
def get_budget_grid(year: int, mode: str = "budget") -> dict:
    """
    Return the full annual budget grid for a year.

    Returns:
        {
            "year": 2026,
            "rows": [
                {
                    "projection": "PP-CC-...",  # Production Projection docname
                    "greenhouse": "GH-A1",
                    "variety": "Athena White",
                    "source": "Manual" | "Calculated from Protocol" | "Hybrid",
                    "weeks": [w1, w2, ..., w52],      # primary layer (budget)
                    "forecast": [...] | None,         # only in mode=compare
                    "plan": [...] | None,
                    "actual": [...] | None,
                    "total": 132400,
                },
                ...
            ],
            "month_offsets": [1, 5, 9, 14, 18, 22, 27, 31, 35, 40, 44, 48],
            "month_labels":  ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"],
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

    rows: list[dict] = []
    for p in projections:
        weeks = _projection_week_array(p["name"])
        row = {
            "projection": p["name"],
            "greenhouse": p["greenhouse"],
            "variety": p["variety"],
            "source": p["source"],
            "weeks": weeks,
            "total": sum(weeks),
        }
        if mode == "compare":
            row["forecast"] = _forecast_week_array(p["greenhouse"], p["variety"], year)
            row["plan"] = _plan_week_array(p["greenhouse"], p["variety"], year)
            row["actual"] = _actual_week_array(p["greenhouse"], p["variety"], year)
        rows.append(row)

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

