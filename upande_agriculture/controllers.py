"""Doc-event wiring. Hooks dispatched via hooks.py:doc_events."""

from __future__ import annotations

import datetime
import frappe
from frappe.utils import getdate

from upande_agriculture.todo_helpers import upsert_todo


def crop_cycle_on_update(doc, method=None):
    _ensure_milestone_todos(doc)
    _sync_greenhouse_from_crop_cycle(doc)
    # Runs AFTER the Greenhouse ledger sync -- that sync's own prefill only
    # knows whole-bed standing/not-standing, so it would otherwise clobber
    # the more precise partial count this one writes.
    _sync_bed_master_from_crop_cycle(doc)


def _sync_bed_master_from_crop_cycle(cycle) -> None:
    """Mirror each of this cycle's beds straight onto the Bed master --
    status, variety, and how many plants remain -- independent of whether a
    Greenhouse ledger document exists for the house. That ledger's own sync
    (sync_bed_master, on the Greenhouse) is a separate, optional view; a bed
    shouldn't have to wait on it to know what's actually growing there.

    Only a bed still crediting THIS cycle (or one this cycle is actively
    giving up) is touched -- a bed already replanted to something else
    belongs to that new planting now, and its own save will have written it.
    """
    meta = frappe.get_meta("Bed")
    if not meta.has_field("status"):
        return
    has_plant_count = meta.has_field("plant_count")

    for row in (cycle.beds or []):
        if not row.bed:
            continue
        if row.status == "Uprooted":
            updates = {"status": "Uprooted", "variety": None}
        else:
            updates = {
                "status": "Partially Uprooted" if row.status == "Partially Uprooted" else "Planted",
                "variety": cycle.variety,
            }
        if has_plant_count:
            updates["plant_count"] = 0 if row.status == "Uprooted" else int(row.plants_remaining or 0)
        current = frappe.db.get_value(
            "Bed", row.bed, ["status", "variety"] + (["plant_count"] if has_plant_count else []),
            as_dict=True,
        )
        if current and any(str(updates.get(f) or "") != str(current.get(f) or "") for f in updates):
            frappe.db.set_value("Bed", row.bed, updates, update_modified=False)


def _sync_greenhouse_from_crop_cycle(cycle) -> None:
    """Keep the Greenhouse bed ledger in step with what this Crop Cycle knows.

    Guarded by the same flag greenhouse.py's reverse sync sets: an uproot or
    replant logged on the Greenhouse saves a Crop Cycle, which would
    otherwise land right back here and re-save the same Greenhouse before
    its own sync has finished -- round and round. Only the outermost sync in
    a chain does any work; a sync started BY that chain is a no-op.

    Only this cycle's own Bed Range rows (tagged by name) are touched --
    anything else on the ledger, another cycle's rows or one typed in by
    hand, is left exactly as it was. Best-effort: a conflict on the
    Greenhouse side (e.g. a bed already claimed by something untracked)
    warns rather than blocking the Crop Cycle save that triggered this.
    """
    from frappe import _
    from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
        OCCUPIED_STATUSES,
        cycle_bed_ranges,
    )

    if frappe.flags.get("in_greenhouse_cycle_sync"):
        return
    house = cycle.get("greenhouse")
    if not house:
        return

    is_new_greenhouse = not frappe.db.exists("Greenhouse", house)
    gh = frappe.get_doc("Greenhouse", house) if not is_new_greenhouse else frappe.get_doc({
        "doctype": "Greenhouse", "greenhouse": house,
    })

    # Beds the ledger currently credits to this cycle -- captured BEFORE the
    # rows are rebuilt below, so ground the cycle has since given up (ended,
    # or partially uprooted on its own Uproot Log) can be recognised and
    # cleared on Individual Beds too. Without that, an ended cycle keeps its
    # variety standing on the ledger forever: varieties_grown is rolled up
    # from Individual Beds, not from Bed Range.
    owned: set[int] = set()
    for r in (gh.bed_range or []):
        if r.crop_cycle == cycle.name and r.from_bed and r.to_bed:
            lo, hi = sorted((int(r.from_bed), int(r.to_bed)))
            owned.update(range(lo, hi + 1))

    gh.bed_range = [r for r in (gh.bed_range or []) if r.crop_cycle != cycle.name]

    standing: set[int] = set()
    if cycle.get("status") != "Ended":
        # cycle_bed_ranges excludes anything this cycle has since logged as
        # uprooted -- without that, this sync would re-claim the cycle's
        # FULL original planting on every save, overwriting whatever a
        # partial uproot/replant already put on those beds.
        for row in cycle_bed_ranges([{
            "name": cycle.name, "variety": cycle.get("variety"),
            "crop_protocol": cycle.get("crop_protocol"),
            "planting_date": cycle.get("planting_date"),
            "plants_per_sqm": cycle.get("plants_per_sqm"),
        }]):
            row["crop_cycle"] = cycle.name
            gh.append("bed_range", row)
            standing.update(range(int(row["from_bed"]), int(row["to_bed"]) + 1))

    # A bed this cycle owned but no longer stands on is out of the ground.
    # Only rows still showing THIS cycle's variety are touched -- a bed
    # already replanted to something else (via a Greenhouse Replanting Log)
    # belongs to that new planting now.
    for b in (gh.individual_beds or []):
        if not b.bed_number:
            continue
        n = int(b.bed_number)
        if (n in owned and n not in standing
                and b.variety == cycle.get("variety")
                and b.status in OCCUPIED_STATUSES):
            b.status = "Uprooted"
            b.plant_count = 0

    try:
        gh.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(title=f"Greenhouse sync failed for {cycle.name}")
        frappe.msgprint(
            _("{0} saved, but couldn't sync into the Greenhouse ledger for {1} — "
              "check it there directly.").format(cycle.name, house),
            indicator="orange", title=_("Greenhouse sync skipped"),
        )
        return

    frappe.msgprint(
        _("{0} greenhouse record {1}.").format(
            "Created" if is_new_greenhouse else "Updated", gh.name),
        indicator="green", alert=True,
    )


def crop_cycle_on_trash(doc, method=None):
    frappe.db.delete("ToDo", {
        "reference_type": "Crop Cycle",
        "reference_name": doc.name,
    })
    _release_greenhouse_beds(doc)


def _release_greenhouse_beds(cycle) -> None:
    """A deleted cycle can't keep ground painted on the Greenhouse ledger.

    Same clearing the Ended path in _sync_greenhouse_from_crop_cycle does,
    but on_trash: without it a deleted cycle's beds stay standing under its
    variety forever, and the next planting on them is refused for a conflict
    nobody can see anymore. Best-effort -- a ledger problem must not block
    the delete itself.
    """
    from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
        OCCUPIED_STATUSES,
    )

    if frappe.flags.get("in_greenhouse_cycle_sync"):
        return
    house = cycle.get("greenhouse")
    if not house or not frappe.db.exists("Greenhouse", house):
        return

    gh = frappe.get_doc("Greenhouse", house)
    owned: set[int] = set()
    for r in (gh.bed_range or []):
        if r.crop_cycle == cycle.name and r.from_bed and r.to_bed:
            lo, hi = sorted((int(r.from_bed), int(r.to_bed)))
            owned.update(range(lo, hi + 1))
    gh.bed_range = [r for r in (gh.bed_range or []) if r.crop_cycle != cycle.name]

    for b in (gh.individual_beds or []):
        if not b.bed_number:
            continue
        if (int(b.bed_number) in owned and b.variety == cycle.get("variety")
                and b.status in OCCUPIED_STATUSES):
            b.status = "Uprooted"
            b.plant_count = 0

    try:
        gh.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(title=f"Greenhouse ledger release failed for deleted {cycle.name}")


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
                    "due_date": date,
                    "status": "Open",
                })
                existing_names.add(label)


def stock_entry_on_submit(doc, method=None):
    """A submitted harvest feeds the forecast immediately.

    The nightly rollup still runs as the safety net, but a grower checking
    the forecast minutes after scanning buckets should already see the
    stems. Cancel goes through the same path so a reversed entry pulls the
    number back down. Best-effort: forecast bookkeeping must never block a
    harvest from being recorded.
    """
    if doc.stock_entry_type != "Harvesting":
        return
    house = doc.get("custom_greenhouse") or doc.get("to_warehouse")
    if not house:
        return
    varieties = {d.item_code for d in (doc.items or []) if d.item_code}
    if not varieties:
        return
    forecasts = frappe.get_all(
        "Production Forecast",
        filters={"greenhouse": house, "variety": ("in", list(varieties)),
                 "status": ("!=", "Closed")},
        pluck="name",
    )
    for name in forecasts:
        try:
            fc = frappe.get_doc("Production Forecast", name)
            fc.pull_actuals(persist=True)
        except Exception:
            frappe.log_error(title=f"Live forecast refresh failed for {name}")


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
        # assigned_to is an Employee; a ToDo is allocated to a User, so it
        # only gets created once that Employee has a linked account.
        user = frappe.db.get_value("Employee", task.assigned_to, "user_id")
        if not user:
            continue
        tag = f"task-{task.idx or i}"
        upsert_todo(
            reference_type="Production Plan Form",
            reference_name=doc.name,
            tag=tag,
            description=f"{task.task_name} ({task.due_date or 'this week'})",
            assigned_to=user,
            due_date=task.due_date,
        )
