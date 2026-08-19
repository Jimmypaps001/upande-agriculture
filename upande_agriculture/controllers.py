"""Doc-event wiring. Hooks dispatched via hooks.py:doc_events."""

from __future__ import annotations

import datetime
import frappe
from frappe.utils import getdate

from upande_agriculture.todo_helpers import upsert_todo


def crop_cycle_on_update(doc, method=None):
    _ensure_milestone_todos(doc)


def crop_cycle_on_trash(doc, method=None):
    frappe.db.delete("ToDo", {
        "reference_type": "Crop Cycle",
        "reference_name": doc.name,
    })


def _seasonal_factor_map(variety: str | None) -> dict[int, float]:
    """Monthly yield multipliers for a variety, if the tenant maintains them."""
    if not frappe.db.exists("DocType", "Seasonal Yield Factor"):
        return {}
    if not variety or not frappe.db.exists("Seasonal Yield Factor", {"variety": variety}):
        return {}
    syf = frappe.get_doc("Seasonal Yield Factor", {"variety": variety})
    return {int(f.month): float(f.factor or 1.0) for f in (syf.seasonal_factors or [])}


def _ensure_milestone_todos(cycle) -> None:
    """Bending and uprooting reminders for the house supervisor."""
    house = cycle.get("greenhouse")
    if not house:
        return
    # custom_supervisor is added by upande_core; this app must still work
    # on a site that doesn't have it.
    if not frappe.get_meta("Warehouse").has_field("custom_supervisor"):
        return
    supervisor = frappe.db.get_value("Warehouse", house, "custom_supervisor")
    if not supervisor:
        return

    variety = cycle.get("variety")
    for tag, desc, due in [
        ("first_bending", f"First bending: {variety} in {house}", cycle.get("first_bending_date")),
        ("second_bending", f"Second bending: {variety} in {house}", cycle.get("second_bending_date")),
        ("uproot", f"Uproot {variety} in {house}", cycle.get("planned_uprooting_date")),
    ]:
        if not due:
            continue
        upsert_todo(
            reference_type="Crop Cycle", reference_name=cycle.name,
            tag=tag, description=desc,
            assigned_to=supervisor, due_date=getdate(due),
        )


def _autoseed_milestone_tasks(doc):
    """For each Active cycle in this greenhouse, append a task row for any
    milestone date that falls within this plan's ISO week. Idempotent —
    skips if a task with the same task_name already exists."""
    if not (doc.greenhouse and doc.plan_year and doc.plan_week):
        return
    monday = datetime.date.fromisocalendar(int(doc.plan_year), int(doc.plan_week), 1)
    sunday = monday + datetime.timedelta(days=6)
    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday",
                    "Friday", "Saturday", "Sunday"]

    cycles = frappe.db.get_all(
        "Crop Cycle",
        filters={"greenhouse": doc.greenhouse, "status": "Active"},
        fields=["name", "variety", "planting_date", "crop_protocol",
                "first_bending_date", "second_bending_date", "planned_uprooting_date"],
    )

    existing_names = {(t.task_name or "").strip() for t in (doc.tasks or [])}

    for c in cycles:
        for label, date in [
            (f"First bending {c.get('variety')}", c.get("first_bending_date")),
            (f"Second bending {c.get('variety')}", c.get("second_bending_date")),
            (f"Uproot {c.get('variety')}", c.get("planned_uprooting_date")),
        ]:
            if not date:
                continue
            date = frappe.utils.getdate(date)
            if monday <= date <= sunday and label not in existing_names:
                doc.append("tasks", {
                    "task_name": label,
                    "due_day": days_of_week[(date - monday).days],
                    "status": "Open",
                })
                existing_names.add(label)


def production_plan_form_before_save(doc, method=None):
    _autoseed_milestone_tasks(doc)


def production_plan_form_on_trash(doc, method=None):
    frappe.db.delete("ToDo", {
        "reference_type": "Production Plan Form",
        "reference_name": doc.name,
    })


def production_plan_form_on_update(doc, method=None):
    """Create one ToDo per task row, idempotent.

    Production Plan Form is not submittable (plan stays editable; a banner
    flags past-week plans). We use on_update so re-saves keep the ToDos in
    sync via the idempotent (reference, tag) upsert.

    Known v1 limitation: removing a task row does not auto-delete its
    previously-created ToDo. Supervisor closes obsolete ToDos manually.
    """
    for i, task in enumerate(doc.tasks or []):
        if not task.assigned_to:
            continue
        tag = f"task-{task.idx or i}"
        upsert_todo(
            reference_type="Production Plan Form",
            reference_name=doc.name,
            tag=tag,
            description=f"{task.task_name} ({task.due_day or 'this week'})",
            assigned_to=task.assigned_to,
            due_date=_due_date_for_plan(doc, task.due_day),
        )


def _due_date_for_plan(plan, due_day: str | None) -> datetime.date | None:
    if not (plan.plan_year and plan.plan_week):
        return None
    # ISO week -> Monday
    monday = datetime.date.fromisocalendar(int(plan.plan_year), int(plan.plan_week), 1)
    if not due_day:
        return monday + datetime.timedelta(days=6)  # Sunday
    days = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]
    return monday + datetime.timedelta(days=days.index(due_day))
