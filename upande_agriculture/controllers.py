"""Doc-event wiring. Hooks dispatched via hooks.py:doc_events."""

from __future__ import annotations

import datetime
import frappe
from frappe.utils import getdate, now_datetime

from upande_agriculture.projection_calc import calculate_weekly_projection
from upande_agriculture.todo_helpers import upsert_todo


def crop_cycle_on_update(doc, method=None):
    _ensure_projection(doc)
    _ensure_milestone_todos(doc)


def _ensure_projection(cycle) -> None:
    proj_name = frappe.db.get_value(
        "Production Projection", {"crop_cycle": cycle.name}, "name"
    )
    proto = cycle.get("custom_crop_protocol")
    if not (cycle.planting_date and proto):
        return

    if proj_name:
        return  # already created; recalc is opt-in via api.regenerate_projection

    protocol = frappe.get_doc("Crop Protocol", proto)
    seasonal = _seasonal_factor_map(cycle.get("variety"))
    weeks = calculate_weekly_projection(
        protocol={
            "weeks_to_pinch": protocol.weeks_to_pinch,
            "weeks_pinch_to_first_harvest": protocol.weeks_pinch_to_first_harvest,
            "total_weeks_in_ground": protocol.total_weeks_in_ground,
            "total_stems_per_plant_life": protocol.total_stems_per_plant_life,
            "flush_schedule": [
                {"flush_number": f.flush_number,
                 "weeks_after_pinch": f.weeks_after_pinch,
                 "stems_per_plant": f.stems_per_plant}
                for f in (protocol.flush_schedule or [])
            ],
        },
        plants_planted=int(cycle.get("custom_total_expected_stems") or
                            (protocol.plants_per_sqm or 0)),
        planting_date=getdate(cycle.planting_date),
        seasonal_factors=seasonal,
    )

    proj = frappe.get_doc({
        "doctype": "Production Projection",
        "name": f"PP-{cycle.name}",
        "variety": cycle.get("variety"),
        "greenhouse": cycle.get("greenhouse"),
        "crop_cycle": cycle.name,
        "crop_protocol": proto,
        "projection_year": getdate(cycle.planting_date).year,
        "planting_date": cycle.planting_date,
        "company": cycle.get("custom_company"),
        "source": "Hybrid",
        "last_calculated_at": now_datetime(),
        "weeks": [{"week": w["week_number"],
                    "projected_stems": w["projected_stems"],
                    "is_locked": 0, "manual_override": 0} for w in weeks],
    })
    proj.insert(ignore_permissions=True)


def _seasonal_factor_map(variety: str | None) -> dict[int, float]:
    if not variety or not frappe.db.exists("Seasonal Yield Factor", {"variety": variety}):
        return {}
    syf = frappe.get_doc("Seasonal Yield Factor", {"variety": variety})
    return {int(f.month): float(f.factor or 1.0) for f in (syf.seasonal_factors or [])}


def _ensure_milestone_todos(cycle) -> None:
    gh = cycle.get("greenhouse")
    if not gh:
        return
    supervisor = frappe.db.get_value("Warehouse", gh, "custom_supervisor")
    pdate = getdate(cycle.planting_date) if cycle.planting_date else None
    if not (supervisor and pdate):
        return
    proto = cycle.get("custom_crop_protocol")
    if not proto:
        return
    p = frappe.get_doc("Crop Protocol", proto)
    pinch_date = pdate + datetime.timedelta(weeks=int(p.weeks_to_pinch or 0))
    first_harvest = pinch_date + datetime.timedelta(weeks=int(p.weeks_pinch_to_first_harvest or 0))
    uproot = pdate + datetime.timedelta(weeks=int(p.total_weeks_in_ground or 52))

    for tag, desc, due in [
        ("pinch", f"Pinch {cycle.get('variety')} in {gh}", pinch_date),
        ("first_harvest", f"First harvest expected for {cycle.get('variety')} in {gh}", first_harvest),
        ("uproot", f"Uproot {cycle.get('variety')} in {gh}", uproot),
    ]:
        upsert_todo(
            reference_type="Crop Cycle", reference_name=cycle.name,
            tag=tag, description=desc, assigned_to=supervisor, due_date=due,
        )
