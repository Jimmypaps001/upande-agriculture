"""Seed a realistic multi-greenhouse farm for testing the budget tool.

Layout and per-variety rates are taken from Mona's BUDGET 2026-2027 sheet, so
the grid exercises what the farm actually looks like: houses split between two
to four varieties, long-stem varieties alongside high-count ones, and blocks at
different ages — including GH 14-17 planted this February, which are still
ramping and therefore show real week-to-week movement.

Idempotent: run it as often as you like.

    bench --site core.local execute upande_agriculture.scripts.seed_demo_farm.run
"""

import datetime

import frappe

COMPANY = "Test Farm Co"
ABBR = "TFC"
DENSITY = 7.0  # plants per m², standard for cut roses on this farm

# variety -> (target stems/m²/yr, cut weeks, grade mix {cm: pct})
# The mix is what separates these commercially: Reflex is a long-stem variety
# that makes fewer, taller stems; Athena makes many at 50-60.
VARIETIES = {
    "Athena":        (130, 7, {50: 30.0, 60: 55.0, 70: 13.0, 80: 2.0}),
    "Sweet-Revival": (130, 7, {50: 28.0, 60: 54.0, 70: 15.0, 80: 3.0}),
    "Ever-Red":      (100, 7, {40: 1.5, 50: 24.0, 60: 60.0, 70: 12.0, 80: 2.5}),
    "Madam-Red":     (120, 7, {40: 0.1, 50: 24.8, 60: 74.5, 70: 0.6}),
    "Confidential":  (100, 8, {50: 12.0, 60: 38.0, 70: 35.0, 80: 15.0}),
    "Reflex":        (70,  9, {60: 8.0, 70: 27.0, 80: 45.0, 90: 20.0}),
    "Fireworks":     (70,  9, {60: 12.0, 70: 33.0, 80: 40.0, 90: 15.0}),
    "Paloma":        (100, 8, {50: 15.0, 60: 45.0, 70: 30.0, 80: 10.0}),
}

# greenhouse -> [(variety, area m², planting date)]
# Dates: most blocks are mature; GH 14-17 went in this February and are still
# climbing towards their ceiling.
FEB = datetime.date(2026, 2, 9)
LAYOUT = {
    1:  [("Athena", 5000, datetime.date(2023, 9, 4)),
         ("Sweet-Revival", 5000, datetime.date(2023, 9, 4))],
    3:  [("Confidential", 5000, datetime.date(2024, 3, 11)),
         ("Reflex", 2500, datetime.date(2024, 3, 11)),
         ("Fireworks", 2500, datetime.date(2024, 3, 11))],
    4:  [("Confidential", 2500, datetime.date(2024, 6, 3)),
         ("Paloma", 2500, datetime.date(2024, 6, 3)),
         ("Fireworks", 2500, datetime.date(2022, 10, 10)),
         ("Reflex", 2500, datetime.date(2022, 10, 10))],
    6:  [("Athena", 6000, datetime.date(2025, 1, 13)),
         ("Madam-Red", 4000, datetime.date(2025, 1, 13))],
    7:  [("Paloma", 10000, datetime.date(2024, 11, 4))],
    8:  [("Madam-Red", 5000, datetime.date(2023, 4, 3)),
         ("Ever-Red", 5000, datetime.date(2023, 4, 3))],
    9:  [("Sweet-Revival", 8000, datetime.date(2025, 5, 5))],
    10: [("Reflex", 5000, datetime.date(2024, 8, 12)),
         ("Confidential", 5000, datetime.date(2024, 8, 12))],
    11: [("Athena", 9000, datetime.date(2022, 7, 4))],          # ageing block
    14: [("Athena", 6000, FEB), ("Madam-Red", 4000, FEB)],
    15: [("Ever-Red", 10000, FEB)],
    16: [("Confidential", 5000, FEB), ("Paloma", 5000, FEB)],
    17: [("Reflex", 4000, FEB), ("Fireworks", 3000, FEB),
         ("Sweet-Revival", 3000, FEB)],
}


def _item(variety):
    if frappe.db.exists("Item", variety):
        return variety
    frappe.get_doc({
        "doctype": "Item", "item_code": variety, "item_name": variety,
        "item_group": "All Item Groups", "stock_uom": "Piece", "is_stock_item": 1,
    }).insert(ignore_permissions=True)
    return variety


def _protocol(variety):
    """One protocol per variety, back-solved from its target annual rate.

    steady stems/m²/yr = density x ceiling / cut_weeks x 52, so the ceiling is
    the only free parameter once the rate and cut cycle are known.
    """
    name = f"{variety} (demo)"
    rate, cut_weeks, mix = VARIETIES[variety]
    ceiling = round(rate * cut_weeks / (DENSITY * 52), 3)

    doc = (frappe.get_doc("Crop Protocol", name)
           if frappe.db.exists("Crop Protocol", name) else frappe.new_doc("Crop Protocol"))
    doc.update({
        "protocol_name": name, "variety_item": _item(variety), "crop_type": "Rose",
        "weeks_to_first_bending": 6, "weeks_to_second_bending": 5,
        "weeks_between_cuts": cut_weeks,
        "stems_per_plant_first_harvest": round(ceiling * 0.25, 3),
        "stems_per_cut": 1.5,
        "max_stems_per_plant_per_cut": ceiling,
        "reject_pct": 5.75, "productive_life_years": 5.0,
    })
    doc.set("grade_mix", [])
    for cm, pct in sorted(mix.items()):
        doc.append("grade_mix", {"length_cm": cm, "pct": pct})
    doc.save(ignore_permissions=True) if doc.get("name") and not doc.is_new() \
        else doc.insert(ignore_permissions=True)
    return doc.name


def _warehouse(n):
    name = f"Main GH {n:02d} - {ABBR}"
    if frappe.db.exists("Warehouse", name):
        return name
    doc = frappe.get_doc({
        "doctype": "Warehouse", "warehouse_name": f"Main GH {n:02d}",
        "company": COMPANY, "is_group": 0, "warehouse_type": "Greenhouse",
    })
    # upande_core makes custom_farm mandatory on this site; copy whatever the
    # existing houses use rather than inventing a value.
    if frappe.get_meta("Warehouse").has_field("custom_farm"):
        doc.custom_farm = frappe.db.get_value(
            "Warehouse", {"warehouse_type": "Greenhouse",
                          "custom_farm": ("is", "set")}, "custom_farm")
    doc.insert(ignore_permissions=True)
    return name


BED_LEN, BED_WIDTH = 56.0, 0.8


def _beds(house, first, last):
    """Crop Cycle validates its bed range against real Bed records, so the
    beds have to exist before the cycle that sits on them."""
    if not frappe.db.exists("DocType", "Bed"):
        return 0
    farm = None
    if frappe.get_meta("Bed").has_field("farm"):
        farm = frappe.db.get_value("Warehouse", house, "custom_farm") \
            if frappe.get_meta("Warehouse").has_field("custom_farm") else None
    made = 0
    for n in range(first, last + 1):
        if frappe.db.exists("Bed", {"greenhouse": house, "bed": n}):
            continue
        doc = frappe.get_doc({
            "doctype": "Bed", "greenhouse": house, "bed": n, "unit_type": "Bed",
            "bed_length": BED_LEN, "bed_width": BED_WIDTH,
            "bed_area": BED_LEN * BED_WIDTH,
        })
        if farm:
            doc.farm = farm
        doc.insert(ignore_permissions=True)
        made += 1
    return made


def run():
    made = {"protocols": 0, "warehouses": 0, "cycles": 0, "skipped": 0}
    for variety in VARIETIES:
        _protocol(variety)
        made["protocols"] += 1

    made["beds"] = 0
    for gh_no, plantings in sorted(LAYOUT.items()):
        house = _warehouse(gh_no)
        made["warehouses"] += 1
        # Beds are numbered continuously across the house, not per variety.
        cursor = 1
        for variety, area, planted in plantings:
            n_beds = max(1, int(round(area / (BED_LEN * BED_WIDTH))))
            first, last = cursor, cursor + n_beds - 1
            cursor = last + 1
            exists = frappe.db.exists("Crop Cycle", {
                "greenhouse": house, "variety": variety, "planting_date": planted})
            if exists:
                made["skipped"] += 1
                continue
            made["beds"] += _beds(house, first, last)
            doc = frappe.new_doc("Crop Cycle")
            doc.update({
                "greenhouse": house, "variety": variety,
                "crop_protocol": f"{variety} (demo)", "crop_type": "Production",
                "status": "Active", "planting_date": planted,
                "first_bending_date": planted + datetime.timedelta(weeks=6),
                "second_bending_date": planted + datetime.timedelta(weeks=11),
                "planted_area": float(area), "plants_per_sqm": DENSITY,
                "qty_planted": int(area * DENSITY),
                "bed_range": f"{first}-{last}",
            })
            doc.insert(ignore_permissions=True)
            made["cycles"] += 1

    frappe.db.commit()
    made["total_cycles"] = frappe.db.count("Crop Cycle", {"status": ("!=", "Ended")})
    print(made)
    return made
