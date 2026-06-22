"""Backfill non-zero planned_stems on the 80 seeded plan rows."""
import sys
sys.path.insert(0, "/home/teddy5456/frappe-bench/apps")
import frappe
frappe.init(site="mona2.local")
frappe.connect()

# For each Plan Variety, set planned_stems = budget_stems * 0.92 (modest haircut),
# or if budget_stems is 0, derive from the projection's average non-zero week.
rows = frappe.db.sql("""
    SELECT pv.name, pv.parent, pv.variety, pv.planned_stems,
           pf.greenhouse, pf.plan_year, pf.plan_week
    FROM `tabProduction Plan Variety` pv
    JOIN `tabProduction Plan Form` pf ON pf.name = pv.parent
    WHERE pf.plan_year = 2026
""", as_dict=True)

print(f"Found {len(rows)} plan-variety rows to backfill")
touched = 0
for r in rows:
    # Find the budget for this variety+gh+week
    budget = frappe.db.sql("""
        SELECT pw.projected_stems
        FROM `tabProduction Projection` pp
        JOIN `tabProjection Week` pw ON pw.parent = pp.name
        WHERE pp.greenhouse = %s AND pp.variety = %s
          AND pp.projection_year = %s AND pw.week = %s
    """, (r["greenhouse"], r["variety"], r["plan_year"], r["plan_week"]))
    budget_w = int(budget[0][0]) if budget else 0

    # If no budget for that exact week, take the average of nonzero weeks
    if budget_w == 0:
        avg = frappe.db.sql("""
            SELECT AVG(pw.projected_stems)
            FROM `tabProduction Projection` pp
            JOIN `tabProjection Week` pw ON pw.parent = pp.name
            WHERE pp.greenhouse = %s AND pp.variety = %s
              AND pp.projection_year = %s AND pw.projected_stems > 0
        """, (r["greenhouse"], r["variety"], r["plan_year"]))
        budget_w = int(avg[0][0] or 0)

    planned = int(budget_w * 0.92) if budget_w else 0
    if planned and planned != r["planned_stems"]:
        frappe.db.set_value("Production Plan Variety", r["name"],
                            {"planned_stems": planned, "budget_stems": budget_w},
                            update_modified=False)
        touched += 1

frappe.db.commit()
print(f"Updated {touched} plan-variety rows.")
print()
print("Sample after fix:")
for r in frappe.db.sql("""
    SELECT pf.greenhouse, pf.plan_week, pv.variety, pv.planned_stems, pv.budget_stems
    FROM `tabProduction Plan Form` pf
    JOIN `tabProduction Plan Variety` pv ON pv.parent = pf.name
    WHERE pf.plan_year = 2026 AND pv.planned_stems > 0
    LIMIT 8
""", as_dict=True):
    print(f"  W{r['plan_week']:>2}  {r['greenhouse']:25s} {r['variety']:25s} planned={r['planned_stems']:>6,}  (budget={r['budget_stems']:>6,})")
