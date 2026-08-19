"""
One-off migration: consolidate length-variant Production Projections into
their variety-template counterparts.

Before:
  Production Projection rows keyed by variant — Athena-50cm, Athena-60cm,
  Athena-70cm, Athena-110cm each had their own 52-week numbers.

After:
  One Projection per (greenhouse, template, year) — `Athena` × GH 01 ×
  2026 with weekly numbers = sum of the four variants' weeks.

The corresponding length-variant rows are deleted. Forecasts and Plans
keep their per-variant references (those are read directly from Stock
Entry / Plan Variety / Forecast Week — they don't reference Production
Projection records).

Idempotent: re-running is a no-op once variants are merged.
"""

import sys
sys.path.insert(0, "/home/teddy5456/frappe-bench/apps")
import frappe
frappe.init(site="mona2.local")
frappe.connect()


def execute():
    print("=== Migration: variant-keyed Projections -> template-keyed ===")

    groups = frappe.db.sql("""
        SELECT pp.greenhouse, i.variant_of AS template, pp.projection_year,
               GROUP_CONCAT(pp.name) AS names
        FROM `tabProduction Projection` pp
        JOIN `tabItem` i ON i.name = pp.variety
        WHERE i.variant_of IS NOT NULL AND i.variant_of != ''
        GROUP BY pp.greenhouse, i.variant_of, pp.projection_year
    """, as_dict=True)
    print(f"Groups to consolidate: {len(groups)}")

    merged = deleted = renamed = 0
    for g in groups:
        names = g["names"].split(",")
        template = g["template"]
        gh = g["greenhouse"]
        year = int(g["projection_year"] or 0)

        # Aggregate weekly numbers across all variants in this group.
        weeks = [0] * 52
        company = None
        source = None
        planting_date = None
        crop_protocol = None
        for n in names:
            rows = frappe.db.sql(
                "SELECT week, projected_stems FROM `tabProjection Week` WHERE parent=%s",
                (n,), as_dict=True)
            for w in rows:
                wn = int(w["week"] or 0)
                if 1 <= wn <= 52:
                    weeks[wn - 1] += int(w["projected_stems"] or 0)
            # Capture metadata once
            meta = frappe.db.get_value(
                "Production Projection", n,
                ["company", "source", "planting_date", "crop_protocol"],
                as_dict=True,
            ) or {}
            company = company or meta.get("company")
            source = source or meta.get("source")
            planting_date = planting_date or meta.get("planting_date")
            crop_protocol = crop_protocol or meta.get("crop_protocol")

        # Is there already a template-keyed Projection for this group?
        existing_template_proj = frappe.db.get_value(
            "Production Projection",
            {"greenhouse": gh, "variety": template, "projection_year": year},
            "name",
        )
        if existing_template_proj:
            # Use the existing template projection — overwrite its weeks.
            keep = existing_template_proj
            doc = frappe.get_doc("Production Projection", keep)
            doc.set("weeks", [])
        else:
            # Promote the first variant projection by renaming its variety.
            keep = names[0]
            doc = frappe.get_doc("Production Projection", keep)
            doc.variety = template
            doc.set("weeks", [])
            renamed += 1

        if company:        doc.company = company
        if source:         doc.source = source
        if planting_date:  doc.planting_date = planting_date
        if crop_protocol:  doc.crop_protocol = crop_protocol

        for i in range(52):
            if weeks[i] or doc.get("source") != "Manual":
                doc.append("weeks", {"week": i + 1, "projected_stems": weeks[i],
                                       "week_locked": 0, "manual_override": 0})
        doc.save(ignore_permissions=True)
        merged += 1

        # Delete remaining variant rows.
        for n in names:
            if n == keep:
                continue
            try:
                frappe.delete_doc("Production Projection", n,
                                   force=True, ignore_permissions=True,
                                   delete_permanently=True)
                deleted += 1
            except Exception as e:
                print(f"  [warn] could not delete {n}: {e}")

    frappe.db.commit()
    print(f"  Merged groups: {merged}")
    print(f"  Renamed-to-template projections: {renamed}")
    print(f"  Deleted variant projections: {deleted}")
    print()
    print("=== Post-migration state ===")
    total = frappe.db.count("Production Projection")
    by_template = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabProduction Projection` pp
        JOIN `tabItem` i ON i.name = pp.variety
        WHERE i.has_variants = 1
    """)[0][0]
    by_variant = frappe.db.sql("""
        SELECT COUNT(*) FROM `tabProduction Projection` pp
        JOIN `tabItem` i ON i.name = pp.variety
        WHERE i.variant_of IS NOT NULL AND i.variant_of != ''
    """)[0][0]
    print(f"  Total projections: {total}")
    print(f"    keyed by template (has_variants=1): {by_template}")
    print(f"    still keyed by variant (variant_of set): {by_variant}")


if __name__ == "__main__":
    execute()
