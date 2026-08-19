"""The week's work: what is planned per greenhouse, and what is happening now.

A Production Plan Form is one greenhouse for one ISO week. The grid reads them
as a board; the map reads them as live operations so a manager can see, without
opening anything, that GH 07 is being sprayed while GH 12 is being harvested.
"""

from __future__ import annotations

import datetime

import re

import frappe
from frappe import _
from frappe.utils import cint, getdate, now_datetime

from upande_agriculture.farm_map import house
from upande_agriculture.projection_calc import iso_weeks_in_year

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Operation -> what it means for the map. `live` operations are the ones worth
# an icon while in progress; everything else is still listed, just not flagged.
OPERATIONS = {
    "Harvest":     {"tone": "harvest", "live": True},
    "Spray":       {"tone": "spray", "live": True},
    "Irrigate":    {"tone": "water", "live": True},
    "Scout":       {"tone": "scout", "live": True},
    "Bend":        {"tone": "crop", "live": False},
    "Prune":       {"tone": "crop", "live": False},
    "De-leaf":     {"tone": "crop", "live": False},
    "Weed":        {"tone": "crop", "live": False},
    "Feed":        {"tone": "water", "live": False},
    "Plant":       {"tone": "crop", "live": False},
    "Uproot":      {"tone": "crop", "live": False},
    "Maintenance": {"tone": "fix", "live": False},
    "Other":       {"tone": "other", "live": False},
}

OPEN_STATES = ("Open", "In Progress")


def _iso(d=None):
    d = getdate(d or frappe.utils.nowdate())
    y, w, dow = d.isocalendar()
    return y, w, dow


def _day_name(d) -> str | None:
    """A stored due_date -> which weekday it falls on, for the board's day
    columns. None if the task has no due date yet."""
    if not d:
        return None
    return DAYS[getdate(d).isocalendar()[2] - 1]


def _date_for_day(year: int, week: int, day_name: str) -> datetime.date:
    """The board still drags a card onto a weekday column of the week being
    viewed -- this resolves that into the real calendar date due_date stores."""
    return datetime.date.fromisocalendar(int(year), int(week), DAYS.index(day_name) + 1)


def _plan_rows(year: int, week: int, greenhouse: str | None = None) -> list:
    """Every task in the week, flattened, with its parent plan."""
    cond, args = "", {"y": int(year), "w": int(week)}
    if greenhouse:
        cond = " AND pp.greenhouse = %(gh)s"
        args["gh"] = greenhouse
    rows = frappe.db.sql(
        f"""
        SELECT pp.name AS plan, pp.greenhouse AS plan_house, pp.plan_year, pp.plan_week,
               t.name AS task, t.idx, t.task_name, t.operation, t.greenhouse,
               t.target, t.due_date, t.assigned_to, t.status, t.beds,
               t.started_at, t.completed_at, t.completion_note
        FROM `tabProduction Plan Form` pp
        JOIN `tabProduction Plan Task` t ON t.parent = pp.name
        WHERE pp.plan_year = %(y)s AND pp.plan_week = %(w)s{cond}
        ORDER BY t.due_date, pp.greenhouse, t.idx
        """,
        args, as_dict=True,
    )
    for r in rows:
        r["due_day"] = _day_name(r["due_date"])
    return rows


def parse_beds(txt: str | None) -> list:
    """"3, 7-12" -> [3, 7, 8, 9, 10, 11, 12]. Blank means the whole house.

    Kept here rather than in the page so the map, the board and any report all
    expand a bed list the same way.
    """
    out: list = []
    for chunk in (txt or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            if lo > hi:
                lo, hi = hi, lo
            out.extend(range(lo, min(hi, lo + 999) + 1))
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                continue
    return sorted(set(out))


@frappe.whitelist()
def week_plan(year: int | None = None, week: int | None = None) -> dict:
    """The whole farm's week, grouped by day and by greenhouse."""
    ty, tw, tdow = _iso()
    year = int(year or ty)
    week = int(week or tw)
    rows = _plan_rows(year, week)

    by_day: dict[str, list] = {d: [] for d in DAYS}
    by_house: dict[str, dict] = {}
    counts = {"Open": 0, "In Progress": 0, "Done": 0, "Skipped": 0}

    for r in rows:
        gh = r["greenhouse"] or r["plan_house"]
        r["house"] = house(gh)
        r["greenhouse"] = gh
        r["tone"] = OPERATIONS.get(r["operation"] or "Other", OPERATIONS["Other"])["tone"]
        r["day_index"] = DAYS.index(r["due_day"]) if r["due_day"] in DAYS else 99
        # Only a task whose day has passed can be late; today's work is not.
        r["late"] = bool(r["status"] in OPEN_STATES
                         and (year, week) <= (ty, tw)
                         and r["day_index"] < (tdow - 1 if (year, week) == (ty, tw) else 7))
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["due_day"] in by_day:
            by_day[r["due_day"]].append(r)
        h = by_house.setdefault(gh, {"greenhouse": gh, "house": house(gh), "tasks": [],
                                     "open": 0, "done": 0, "late": 0, "operations": []})
        h["tasks"].append(r)
        h["open"] += 1 if r["status"] in OPEN_STATES else 0
        h["done"] += 1 if r["status"] == "Done" else 0
        h["late"] += 1 if r["late"] else 0
        if r["operation"] and r["operation"] not in h["operations"]:
            h["operations"].append(r["operation"])

    total = len(rows)
    done = counts.get("Done", 0) + counts.get("Skipped", 0)
    return {
        "year": year, "week": week, "today": DAYS[tdow - 1],
        "is_current": (year, week) == (ty, tw),
        "weeks_in_year": iso_weeks_in_year(year),
        "days": DAYS,
        "by_day": by_day,
        "houses": sorted(by_house.values(), key=lambda h: h["house"]),
        "counts": counts,
        "total": total,
        "progress": round(done / total * 100) if total else None,
        "late": sum(1 for r in rows if r["late"]),
        "operations": [{"name": k, **v} for k, v in OPERATIONS.items()],
    }


@frappe.whitelist()
def house_tasks(greenhouse: str, year: int | None = None,
                week: int | None = None) -> dict:
    """One greenhouse's week — what the map drawer shows on tap."""
    ty, tw, _ = _iso()
    year, week = int(year or ty), int(week or tw)
    plan = week_plan(year, week)
    for h in plan["houses"]:
        if h["greenhouse"] == greenhouse:
            return {"year": year, "week": week, "today": plan["today"], **h}
    return {"year": year, "week": week, "today": plan["today"],
            "greenhouse": greenhouse, "house": house(greenhouse),
            "tasks": [], "open": 0, "done": 0, "late": 0, "operations": []}


@frappe.whitelist()
def live_operations(year: int | None = None, week: int | None = None) -> dict:
    """{greenhouse: [operation, ...]} for what is under way right now.

    "Now" means In Progress, or Open and due today — a manager looking at the
    map wants the shift in front of them, not the whole week.
    """
    ty, tw, tdow = _iso()
    year, week = int(year or ty), int(week or tw)
    today = DAYS[tdow - 1]

    # Nothing planned for this week is not the same as nothing happening: it
    # usually means the plan has not been rolled forward. Fall back to the last
    # week that was planned and say so, rather than drawing an idle farm.
    stale = False
    if not frappe.db.exists("Production Plan Form", {"plan_year": year, "plan_week": week}):
        last = frappe.db.sql(
            """SELECT plan_year, plan_week FROM `tabProduction Plan Form`
               WHERE (plan_year * 100 + plan_week) <= %(k)s
               ORDER BY plan_year DESC, plan_week DESC LIMIT 1""",
            {"k": year * 100 + week}, as_dict=True)
        if last:
            year, week = int(last[0]["plan_year"]), int(last[0]["plan_week"])
            stale = True

    out: dict[str, dict] = {}
    for r in _plan_rows(year, week):
        gh = r["greenhouse"] or r["plan_house"]
        if not gh:
            continue
        running = r["status"] == "In Progress"
        # A real calendar date, not a bare weekday name -- a task in some
        # other week whose weekday happens to match today's no longer counts.
        due_now = r["status"] == "Open" and r["due_date"] and getdate(r["due_date"]) == getdate()
        # On a stale week "today" has already passed, so anything still open
        # counts as outstanding rather than nothing counting at all.
        if stale:
            if r["status"] not in ("In Progress", "Open"):
                continue
        elif not running and not due_now:
            continue
        op = r["operation"] or "Other"
        blk = out.setdefault(gh, {"operations": [], "running": 0, "due": 0})
        if op not in blk["operations"]:
            blk["operations"].append(op)
        blk["running"] += 1 if running else 0
        blk["due"] += 1 if due_now else 0
    return {"year": year, "week": week, "today": today, "houses": out,
            "stale": stale, "current_week": tw, "current_year": ty}


BED_NO = re.compile(r"(?:bed|row)\s*0*(\d+)\s*$", re.I)


def bed_number(bed_name: str | None) -> int | None:
    """'Main GH 01 - TFC - Bed 12' -> 12.

    Beds are keyed by number, not by document name, on purpose: the Bed records
    still carry the warehouse name they were created under, so a house that has
    been renamed would otherwise look like it had no beds at all.
    """
    m = BED_NO.search(bed_name or "")
    return int(m.group(1)) if m else None


@frappe.whitelist()
def house_bed_load(greenhouse: str, year: int | None = None,
                   week: int | None = None) -> dict:
    """Which beds in this house have work on them this week, and how much.

    Feeds the map's operation modal: the beds are the grid, the task count on
    each bed is the heat. A task with no beds named is house-wide and is
    reported separately rather than smeared across every bed, which would make
    the whole house look equally busy.
    """
    ty, tw, tdow = _iso()
    year, week = int(year or ty), int(week or tw)
    today = DAYS[tdow - 1]

    # Every bed the house actually has, from the cycles standing in it.
    rows = frappe.db.sql(
        """
        SELECT DISTINCT b.bed
        FROM `tabCrop Cycle` cc
        JOIN `tabCrop Cycle Bed` b ON b.parent = cc.name
        WHERE cc.greenhouse = %(gh)s
        """, {"gh": greenhouse}, as_dict=True)
    beds = sorted({n for n in (bed_number(r["bed"]) for r in rows) if n})

    tasks, load, house_wide = [], {}, 0
    for r in _plan_rows(year, week, greenhouse):
        on = parse_beds(r.get("beds"))
        if on:
            for b in on:
                load[b] = load.get(b, 0) + 1
        else:
            house_wide += 1
        tasks.append({
            "task": r["task"], "task_name": r["task_name"],
            "operation": r["operation"] or "Other", "status": r["status"],
            "due_day": r["due_day"], "assigned_to": r["assigned_to"],
            "target": r["target"], "beds": on,
            "running": r["status"] == "In Progress",
            # The actual date, not a weekday-name coincidence -- a future or
            # past week's same-named weekday no longer reads as "today".
            "today": bool(r["due_date"] and getdate(r["due_date"]) == getdate()),
        })

    # Beds a task names but the house has no record of — surfaced, not hidden.
    unknown = sorted(b for b in load if b not in beds)
    return {
        "greenhouse": greenhouse, "year": year, "week": week, "today": today,
        "beds": beds, "load": load, "unknown_beds": unknown,
        "max_load": max(load.values()) if load else 0,
        "house_wide": house_wide,
        "tasks": tasks,
        "counts": {
            "total": len(tasks),
            "running": sum(1 for t in tasks if t["running"]),
            "with_beds": sum(1 for t in tasks if t["beds"]),
        },
    }


@frappe.whitelist()
def set_task_status(task: str, status: str, note: str | None = None) -> dict:
    """Advance one task. Stamps the clock so 'in progress' can be trusted."""
    if status not in ("Open", "In Progress", "Done", "Skipped"):
        frappe.throw(_("Unknown status {0}.").format(status))
    parent = frappe.db.get_value("Production Plan Task", task, "parent")
    if not parent:
        frappe.throw(_("Task {0} not found.").format(task))

    doc = frappe.get_doc("Production Plan Form", parent)
    for row in doc.tasks:
        if row.name != task:
            continue
        row.status = status
        if status == "In Progress" and not row.started_at:
            row.started_at = now_datetime()
        if status in ("Done", "Skipped"):
            row.completed_at = now_datetime()
            if not row.started_at:
                row.started_at = now_datetime()
        elif status == "Open":
            row.started_at = None
            row.completed_at = None
        if note is not None:
            row.completion_note = note
        break
    else:
        frappe.throw(_("Task {0} is not on plan {1}.").format(task, parent))

    doc.save(ignore_permissions=True)
    return {"task": task, "status": status, "plan": parent}


@frappe.whitelist()
def update_task(task: str, due_day: str | None = None, task_name: str | None = None,
                operation: str | None = None, target=None,
                assigned_to: str | None = None, beds: str | None = None) -> dict:
    """Edit one task in place. Also the drag-and-drop endpoint.

    Dropping a card on another day is a due_day change and nothing else, so it
    shares this path rather than getting an endpoint that could drift from it.
    Only the fields actually passed are touched — the board sends one key.
    """
    parent = frappe.db.get_value("Production Plan Task", task, "parent")
    if not parent:
        frappe.throw(_("Task {0} not found.").format(task))
    if due_day is not None and due_day not in DAYS:
        frappe.throw(_("Unknown day {0}.").format(due_day))
    if operation is not None and operation not in OPERATIONS:
        frappe.throw(_("Unknown operation {0}.").format(operation))
    if assigned_to and not frappe.db.exists("Employee", assigned_to):
        frappe.throw(_("No employee {0}.").format(assigned_to))

    doc = frappe.get_doc("Production Plan Form", parent)
    for row in doc.tasks:
        if row.name != task:
            continue
        if due_day is not None:
            # due_day names a column on THIS plan's own week -- resolve it
            # to the real date the doctype stores.
            row.due_date = _date_for_day(doc.plan_year, doc.plan_week, due_day)
        if task_name:
            row.task_name = task_name
        if operation is not None:
            row.operation = operation
        if target is not None:
            row.target = cint(target) or None
        if assigned_to is not None:
            row.assigned_to = assigned_to or None
        if beds is not None:
            row.beds = ", ".join(str(b) for b in parse_beds(beds)) or None
        break
    else:
        frappe.throw(_("Task {0} is not on plan {1}.").format(task, parent))

    doc.save(ignore_permissions=True)
    return {"task": task, "plan": parent, "due_day": due_day}


@frappe.whitelist()
def delete_task(task: str) -> dict:
    """Remove a task from its plan."""
    parent = frappe.db.get_value("Production Plan Task", task, "parent")
    if not parent:
        frappe.throw(_("Task {0} not found.").format(task))
    doc = frappe.get_doc("Production Plan Form", parent)
    doc.tasks = [r for r in doc.tasks if r.name != task]
    for i, r in enumerate(doc.tasks, 1):
        r.idx = i
    doc.save(ignore_permissions=True)
    return {"task": task, "plan": parent, "deleted": True}


@frappe.whitelist()
def task_options() -> dict:
    """What the add/edit form may offer: operations, days, and who can be given a job."""
    employees = frappe.get_all(
        "Employee", filters={"status": "Active"},
        fields=["name", "employee_name as full_name"], order_by="employee_name", limit=200)
    return {
        "operations": sorted(OPERATIONS),
        "days": DAYS,
        "employees": employees,
        "greenhouses": [r["name"] for r in frappe.get_all(
            "Warehouse", filters={"is_group": 0}, fields=["name"], order_by="name")],
    }


@frappe.whitelist()
def add_task(greenhouse: str, task_name: str, operation: str = "Other",
             due_day: str | None = None, target: int | None = None,
             assigned_to: str | None = None, beds: str | None = None,
             year: int | None = None, week: int | None = None) -> dict:
    """Add a task to a greenhouse's week, creating the plan if it is the first."""
    ty, tw, tdow = _iso()
    year, week = int(year or ty), int(week or tw)
    if operation not in OPERATIONS:
        frappe.throw(_("Unknown operation {0}.").format(operation))
    if assigned_to and not frappe.db.exists("Employee", assigned_to):
        frappe.throw(_("No employee {0}.").format(assigned_to))
    due_day = due_day if due_day in DAYS else DAYS[tdow - 1]

    name = frappe.db.get_value("Production Plan Form", {
        "greenhouse": greenhouse, "plan_year": year, "plan_week": week}, "name")
    if name:
        doc = frappe.get_doc("Production Plan Form", name)
    else:
        doc = frappe.new_doc("Production Plan Form")
        doc.update({
            "greenhouse": greenhouse, "plan_year": year, "plan_week": week,
            "plan_period": f"{year}-W{week:02d}",
            "company": frappe.db.get_value("Warehouse", greenhouse, "company"),
        })
    doc.append("tasks", {
        "task_name": task_name, "operation": operation, "greenhouse": greenhouse,
        "due_date": _date_for_day(year, week, due_day), "target": int(target) if target else None,
        "assigned_to": assigned_to, "status": "Open",
        "beds": ", ".join(str(b) for b in parse_beds(beds)) or None,
    })
    doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
    return {"plan": doc.name, "tasks": len(doc.tasks)}
