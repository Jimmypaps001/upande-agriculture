"""Nightly job: read Actual Harvest, fill actual_stems on Projection Week."""

import datetime
import frappe
from frappe.utils import getdate


def rollup_actuals() -> int:
    """Returns count of Projection Week rows updated.

    Walks every Production Projection, finds matching Actual Harvest rows
    for each week window, then writes actual_stems + variance back to the
    Projection Week child row.

    Soft-fails (returns 0) if tabActual Harvest does not exist on this site.
    """
    if not frappe.db.has_table("tabActual Harvest"):
        return 0

    projections = frappe.get_all(
        "Production Projection",
        fields=["name", "greenhouse", "variety", "projection_year", "planting_date"],
    )
    updated = 0
    for p in projections:
        pdate = getdate(p["planting_date"]) if p.get("planting_date") else None
        if not pdate:
            continue

        weeks = frappe.get_all(
            "Projection Week",
            filters={"parent": p["name"]},
            fields=["name", "week"],
            order_by="week asc",
        )
        for w in weeks:
            start = pdate + datetime.timedelta(weeks=int(w["week"]) - 1)
            end = start + datetime.timedelta(days=6)
            actual = frappe.db.sql(
                """
                SELECT COALESCE(SUM(quantity), 0)
                FROM `tabActual Harvest`
                WHERE greenhouse=%s AND variety=%s
                  AND harvest_date BETWEEN %s AND %s
                """,
                (p["greenhouse"], p["variety"], start, end),
            )[0][0]
            projected = frappe.db.get_value(
                "Projection Week", w["name"], "projected_stems"
            )
            variance = (actual or 0) - (projected or 0)
            frappe.db.set_value(
                "Projection Week",
                w["name"],
                {"actual_stems": actual, "variance": variance},
                update_modified=False,
            )
            updated += 1

    updated += _rollup_plan_tasks()
    frappe.db.commit()
    return updated


def _rollup_plan_tasks() -> int:
    """Same idea for Production Plan Task: a Harvest-operation task with a
    variety set gets its actual weekly total filled in, so the plan shows
    target vs. what was actually picked without anyone opening Actual
    Harvest by hand.

    Soft-fails (returns 0) if tabActual Harvest does not exist on this site.
    """
    if not frappe.db.has_table("tabActual Harvest"):
        return 0

    tasks = frappe.db.sql(
        """
        SELECT t.name, t.greenhouse, t.variety, p.plan_year, p.plan_week
        FROM `tabProduction Plan Task` t
        JOIN `tabProduction Plan Form` p ON p.name = t.parent
        WHERE t.operation = 'Harvest' AND t.variety IS NOT NULL AND t.variety != ''
          AND t.greenhouse IS NOT NULL AND t.greenhouse != ''
        """,
        as_dict=True,
    )
    updated = 0
    for t in tasks:
        monday = datetime.date.fromisocalendar(int(t.plan_year), int(t.plan_week), 1)
        sunday = monday + datetime.timedelta(days=6)
        harvested = frappe.db.sql(
            """
            SELECT COALESCE(SUM(quantity), 0)
            FROM `tabActual Harvest`
            WHERE greenhouse=%s AND variety=%s
              AND harvest_date BETWEEN %s AND %s
            """,
            (t.greenhouse, t.variety, monday, sunday),
        )[0][0]
        frappe.db.set_value("Production Plan Task", t.name, "harvested", harvested,
                            update_modified=False)
        updated += 1
    return updated
