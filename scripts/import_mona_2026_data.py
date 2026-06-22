"""
Import real Mona data into mona2.local so the Production Budget spreadsheet
shows useful comparisons.

1. Greenhouse linking: planting record maps variety+date to GH number. We use
   the variety-name match to set `greenhouse` on each Production Projection
   for 2026 where the bare variety appears in the planting record.
2. Production Forecast docs from `Forecast wk 1-10.xlsx` and
   `FORECAST WK 49-52.xlsx`. One Forecast doc per (variety_full_name, GH).
3. Also seed a few Plan Form rows so plan-vs-budget shows something.

This is a ONE-OFF script, idempotent — safe to re-run.
"""

from __future__ import annotations

import sys, re, openpyxl
from collections import defaultdict
from datetime import date

sys.path.insert(0, "/home/teddy5456/frappe-bench/apps")
import frappe
frappe.init(site="mona2.local")
frappe.connect()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VARIETY_ALIASES = {
    "EVA RED": "Ever-Red",
    "EVER RED": "Ever-Red",
    "MADAM RED": "Madam-Red",
    "PROUD": "Proud",
    "ATHENA": "Athena",
    "EVER RED PRO 3": "Ever-Red-Pro-3",
    "REVIVAL SWEET": "Revival-Sweet",
    "CONFIDENTIAL": "Confidential",
    "FIREWORKS": "Fireworks",
    "REFLEX": "Reflex",
    "PALOMA": "Paloma",
    "ALICIA": "Alicia",
    "DEEP PURPLE": "Deep-Purple",
    "DINARA": "Dinara",
    "EVER PINK": "Ever-Pink",
}


def normalize_variety(name: str) -> str:
    """'EVER RED' -> 'Ever-Red', or pass-through for unmapped."""
    if not name:
        return ""
    n = name.strip().upper()
    if n in VARIETY_ALIASES:
        return VARIETY_ALIASES[n]
    # Best-effort: Title-case each word, hyphenate
    return "-".join(w.title() for w in n.split())


def length_suffix(text: str) -> str:
    """'50 cm' -> '50cm'."""
    if not text:
        return ""
    m = re.search(r"(\d+)\s*c?m", str(text).lower())
    if not m:
        return ""
    return f"{m.group(1)}cm"


def map_gh(gh_short: str) -> str | None:
    """'GH2' -> 'Main GH 02 - MFL'."""
    if not gh_short:
        return None
    m = re.match(r"GH\s*(\d+)", str(gh_short).strip().upper())
    if not m:
        return None
    candidate = f"Main GH {int(m.group(1)):02d} - MFL"
    if frappe.db.exists("Warehouse", candidate):
        return candidate
    # Fallback: try MFK
    candidate2 = f"Main GH {int(m.group(1)):02d} - MFK"
    if frappe.db.exists("Warehouse", candidate2):
        return candidate2
    return None


# ---------------------------------------------------------------------------
# 1. Greenhouse linking from planting record
# ---------------------------------------------------------------------------

def link_greenhouses() -> None:
    print("\n=== 1. Linking greenhouses from planting record ===")
    wb = openpyxl.load_workbook(
        "/mnt/c/Users/teddy/Downloads/planting record latest.xlsx",
        data_only=True,
    )
    ws = wb["planting"]
    # Header row is r2: DATE | VARIETY | QTY | GH | PLT WK

    # variety_bare -> set of greenhouses (we may have multiple per variety; pick most recent)
    variety_gh: dict[str, str] = {}
    for row in ws.iter_rows(min_row=3, values_only=True):
        date_val, variety, qty, gh, wk = (row + (None,) * 5)[:5]
        if not variety or not gh:
            continue
        nvar = normalize_variety(variety)
        ngh = map_gh(gh)
        if nvar and ngh:
            variety_gh[nvar] = ngh  # last write wins (most recent at the bottom)

    print(f"  Found {len(variety_gh)} variety→GH mappings:")
    for v, g in sorted(variety_gh.items()):
        print(f"    {v:20s} -> {g}")

    # Apply: for each Production Projection where variety begins with one of
    # these bare names, set greenhouse if currently NULL.
    print("\n  Applying to Production Projections (year=2026, where greenhouse IS NULL):")
    touched = 0
    for var_bare, gh in variety_gh.items():
        projs = frappe.db.get_all(
            "Production Projection",
            filters={
                "projection_year": 2026,
                "greenhouse": ["is", "not set"],
                "variety": ["like", f"{var_bare}-%"],
            },
            fields=["name", "variety"],
        )
        for p in projs:
            frappe.db.set_value("Production Projection", p["name"],
                                "greenhouse", gh, update_modified=False)
            touched += 1
    frappe.db.commit()
    print(f"  Updated {touched} Production Projections with greenhouse.")


# ---------------------------------------------------------------------------
# 2. Production Forecast docs from forecast Excel files
# ---------------------------------------------------------------------------

def parse_forecast_sheet(path: str, week_columns: list[int]) -> dict:
    """Return {full_variety_name: {week_n: stems}} from a forecast sheet.

    The sheet shape is:
      r2: 'FORECAST [YEAR]'
      r3: '' | 'Week' | <w1> | <w2> | ... | <wN> | 'TOTAL'
      r4: 'VARIETY' | 'LENGTH' | (empty cells)
      r5+: <variety> | <length> | <w1 value> | ...
    """
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[wb.sheetnames[0]]
    out: dict[str, dict[int, int]] = {}
    current_variety = None
    for row in ws.iter_rows(min_row=5, values_only=True):
        if not row or not any(row):
            continue
        variety_cell, length_cell = row[0], row[1]
        # Some rows are the subtotal (no variety, no length, just numbers).
        if variety_cell:
            current_variety = variety_cell
        if not length_cell:
            continue
        nvar = normalize_variety(current_variety or "")
        nlen = length_suffix(length_cell)
        if not (nvar and nlen):
            continue
        full = f"{nvar}-{nlen}"
        weeks_map = out.setdefault(full, {})
        # row[2:] aligns to week_columns
        for offset, w_num in enumerate(week_columns):
            cell = row[2 + offset]
            try:
                v = int(cell) if cell is not None else 0
            except (TypeError, ValueError):
                v = 0
            if v:
                weeks_map[w_num] = weeks_map.get(w_num, 0) + v
    return out


def write_forecasts() -> None:
    print("\n=== 2. Importing Production Forecasts ===")
    early = parse_forecast_sheet(
        "/mnt/c/Users/teddy/Downloads/Forecast wk 1-10.xlsx",
        list(range(1, 11)),
    )
    late = parse_forecast_sheet(
        "/mnt/c/Users/teddy/Downloads/FORECAST WK 49-52.xlsx",
        list(range(49, 53)),
    )
    # Merge
    combined: dict[str, dict[int, int]] = defaultdict(dict)
    for d in (early, late):
        for v, weeks in d.items():
            combined[v].update(weeks)
    print(f"  Forecast varieties (full names): {len(combined)}")

    # For each variety with a Production Projection in 2026, create/update
    # one Production Forecast doc covering its weeks.
    created = updated = skipped = 0
    for full_variety, weeks_map in combined.items():
        # Find the matching projection (gives us greenhouse)
        proj = frappe.db.get_all(
            "Production Projection",
            filters={"variety": full_variety, "projection_year": 2026},
            fields=["name", "greenhouse"],
            limit=1,
        )
        if not proj:
            print(f"    [skip] no projection for {full_variety}")
            skipped += 1
            continue
        greenhouse = proj[0].get("greenhouse")
        if not greenhouse:
            print(f"    [skip] projection {proj[0]['name']} has no greenhouse")
            skipped += 1
            continue

        # Dedupe: one Forecast per (greenhouse, variety, year, start_week)
        # We'll create two forecast docs — one for the early window (W1-10),
        # one for the late window (W49-52) — only if there are nonzero numbers.
        for window_label, start, end in [("early", 1, 10), ("late", 49, 52)]:
            window_weeks = {w: v for w, v in weeks_map.items() if start <= w <= end and v}
            if not window_weeks:
                continue
            existing = frappe.db.get_value(
                "Production Forecast",
                {"greenhouse": greenhouse, "variety": full_variety,
                 "forecast_year": 2026, "start_week": start},
                "name",
            )
            doc = frappe.get_doc("Production Forecast", existing) if existing else \
                  frappe.get_doc({
                      "doctype": "Production Forecast",
                      "greenhouse": greenhouse,
                      "variety": full_variety,
                      "forecast_year": 2026,
                      "start_week": start,
                      "window_weeks": end - start + 1,
                      "status": "Active",
                  })
            doc.weeks = []  # rebuild rows
            budget_weeks = _projection_budget_lookup(proj[0]["name"])
            for w in range(start, end + 1):
                doc.append("weeks", {
                    "week_number": w,
                    "forecasted_stems": window_weeks.get(w, 0),
                    "budget_stems": budget_weeks.get(w, 0),
                })
            if existing:
                doc.save(ignore_permissions=True)
                updated += 1
            else:
                doc.insert(ignore_permissions=True)
                created += 1
    frappe.db.commit()
    print(f"  Forecasts: created {created}, updated {updated}, skipped {skipped}")


def _projection_budget_lookup(projection_name: str) -> dict[int, int]:
    rows = frappe.db.get_all(
        "Projection Week",
        filters={"parent": projection_name},
        fields=["week", "projected_stems"],
    )
    return {int(r["week"] or 0): int(r["projected_stems"] or 0) for r in rows}


# ---------------------------------------------------------------------------
# 3. Seed a few Production Plan Form rows so plan-vs-budget shows data
# ---------------------------------------------------------------------------

def seed_plans() -> None:
    print("\n=== 3. Seeding Production Plan Forms (current week + 1 next) ===")
    iso_year, iso_week, _ = date.today().isocalendar()
    weeks_to_seed = [(iso_year, iso_week), (iso_year, iso_week + 1)]

    # Group projections by greenhouse so we make one plan per (gh, week)
    projs = frappe.db.get_all(
        "Production Projection",
        filters={"projection_year": iso_year, "greenhouse": ["is", "set"]},
        fields=["name", "greenhouse", "variety"],
    )
    by_gh: dict[str, list[dict]] = defaultdict(list)
    for p in projs:
        by_gh[p["greenhouse"]].append(p)

    seeded = 0
    for greenhouse, ps in by_gh.items():
        for (yr, wk) in weeks_to_seed:
            existing = frappe.db.exists("Production Plan Form", {
                "greenhouse": greenhouse, "plan_year": yr, "plan_week": wk,
            })
            if existing:
                continue
            plan = frappe.get_doc({
                "doctype": "Production Plan Form",
                "greenhouse": greenhouse,
                "plan_year": yr,
                "plan_week": wk,
                "plan_period": f"{yr}-W{wk:02d}",
            })
            # Take up to 4 varieties for this GH
            for p in ps[:4]:
                budget_map = _projection_budget_lookup(p["name"])
                planned = int(budget_map.get(wk, 0) * 0.9) if budget_map else 0
                plan.append("varieties", {
                    "variety": p["variety"],
                    "planned_stems": planned,
                    "budget_stems": budget_map.get(wk, 0),
                })
            try:
                plan.insert(ignore_permissions=True)
                seeded += 1
            except Exception as e:
                print(f"    [skip] {greenhouse} W{wk}: {e}")
    frappe.db.commit()
    print(f"  Seeded {seeded} Production Plan Forms.")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    link_greenhouses()
    write_forecasts()
    seed_plans()

    # Summary
    print("\n=== Final state ===")
    print("Projections with greenhouse:",
          frappe.db.count("Production Projection",
                           {"greenhouse": ["is", "set"], "projection_year": 2026}))
    print("Production Forecasts (2026):",
          frappe.db.count("Production Forecast", {"forecast_year": 2026}))
    print("Production Plan Forms (2026):",
          frappe.db.count("Production Plan Form", {"plan_year": 2026}))
