"""Seed a week of greenhouse operations so the plan board has something real.

Every active block gets the work its crop actually needs this week: harvest on
the cut cycle, a spray round, irrigation, scouting, and the establishment jobs
(bending, de-leafing) that only young blocks require.

Idempotent — clears and rewrites the target week.
"""

import datetime

import frappe

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# Work every house gets, whatever is planted in it.
ROUTINE = [
    ("Spray", "Preventative spray round", "Tuesday"),
    ("Irrigate", "Irrigation check and run", "Monday"),
    ("Scout", "Weekly pest and disease scout", "Wednesday"),
    ("Feed", "Fertigation batch", "Thursday"),
]


def _status(day_index, today_index, seed):
    """Past days are mostly finished; today is part-done; the future is open."""
    if day_index < today_index:
        return "Done" if seed % 7 else "Skipped"
    if day_index == today_index:
        return ("In Progress", "Done", "Open")[seed % 3]
    return "Open"


def run(year=None, week=None):
    today = frappe.utils.getdate(frappe.utils.nowdate())
    iso_y, iso_w, iso_dow = today.isocalendar()
    year, week = int(year or iso_y), int(week or iso_w)
    today_index = iso_dow - 1 if (year, week) == (iso_y, iso_w) else 7

    cycles = frappe.db.get_all(
        "Crop Cycle", filters={"status": ("!=", "Ended")},
        fields=["name", "greenhouse", "variety", "planting_date", "qty_planted"],
        order_by="greenhouse")
    if not cycles:
        print("no active crop cycles")
        return

    by_house = {}
    for c in cycles:
        by_house.setdefault(c.greenhouse, []).append(c)

    # Start clean so re-running does not pile up duplicates.
    for name in frappe.db.get_all("Production Plan Form",
                                  {"plan_year": year, "plan_week": week}, pluck="name"):
        frappe.delete_doc("Production Plan Form", name, force=1, ignore_permissions=True)

    made = tasks = 0
    for i, (gh, blocks) in enumerate(sorted(by_house.items())):
        doc = frappe.new_doc("Production Plan Form")
        doc.update({
            "greenhouse": gh, "plan_year": year, "plan_week": week,
            "plan_period": f"{year}-W{week:02d}",
            "company": frappe.db.get_value("Warehouse", gh, "company"),
        })

        rows = []
        for j, b in enumerate(blocks):
            age_wk = ((today - frappe.utils.getdate(b.planting_date)).days / 7
                      if b.planting_date else 99)
            # Harvest twice a week on a mature block; a young one is not cutting yet.
            if age_wk > 16:
                for day in ("Monday", "Thursday"):
                    rows.append(("Harvest", f"Cut {b.variety}", day,
                                 int((b.qty_planted or 0) * 0.35)))
            if age_wk < 30:
                rows.append(("Bend", f"Bend {b.variety} laterals", "Friday", None))
            if 8 < age_wk < 40:
                rows.append(("De-leaf", f"De-leaf {b.variety}", "Wednesday", None))
            if j == 0:
                rows.append(("Weed", "Bed weeding round", "Saturday", None))

        for op, label, day in ROUTINE:
            rows.append((op, label, day, None))
        if i % 4 == 0:
            rows.append(("Maintenance", "Check plastic and gutters", "Friday", None))

        for k, (op, label, day, target) in enumerate(rows):
            di = DAYS.index(day) if day in DAYS else 6
            doc.append("tasks", {
                "task_name": label, "operation": op, "greenhouse": gh,
                "due_day": day, "target": target,
                "status": _status(di, today_index, i * 7 + k),
            })
        doc.insert(ignore_permissions=True)
        made += 1
        tasks += len(doc.tasks)

    frappe.db.commit()
    print({"plans": made, "tasks": tasks, "year": year, "week": week})
    return {"plans": made, "tasks": tasks}
