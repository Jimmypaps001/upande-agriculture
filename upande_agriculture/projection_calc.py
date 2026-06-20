"""
Smart-fallback annual production projection.

Pure function — takes plain dicts/dates so it's testable without a Frappe
runtime. The Frappe controller wraps this to feed in real Crop Protocol +
Crop Cycle data.
"""

from __future__ import annotations

import datetime


WEEKS = 52
PEAK_PRIMARY = 0.60   # Flush peak week gets 60% of the flush's stems
PEAK_SECONDARY = 0.40 # The following week gets the remaining 40%


def calculate_weekly_projection(
    protocol: dict,
    plants_planted: int,
    planting_date: datetime.date,
    seasonal_factors: dict[int, float] | None = None,
) -> list[dict]:
    """
    Return 52 rows of {week_number, week_start_date, projected_stems}.

    Production window:
        first_harvest_offset = weeks_to_pinch + weeks_pinch_to_first_harvest
        producing weeks are [first_harvest_offset .. total_weeks_in_ground)

    Within the window:
        - If protocol['flush_schedule'] has rows, each flush contributes
          stems_per_plant * plants_planted, split 60/40 across its peak
          week and the next week.
        - Else, evenly distribute total_stems_per_plant_life * plants_planted
          across the window.

    Then apply seasonal_factors[month] (default 1.0) to every week.
    """
    seasonal_factors = seasonal_factors or {}
    weeks_to_pinch = int(protocol.get("weeks_to_pinch") or 0)
    weeks_p2fh = int(protocol.get("weeks_pinch_to_first_harvest") or 0)
    total_in_ground = int(protocol.get("total_weeks_in_ground") or WEEKS)
    first_harvest = weeks_to_pinch + weeks_p2fh

    # Initialise empty 52-week grid.
    rows: list[dict] = []
    for i in range(WEEKS):
        rows.append({
            "week_number": i + 1,
            "week_start_date": planting_date + datetime.timedelta(weeks=i),
            "projected_stems": 0,
        })

    producing_idx = [i for i in range(WEEKS) if first_harvest <= i < total_in_ground]

    if protocol.get("flush_schedule"):
        for flush in protocol["flush_schedule"]:
            peak_offset = weeks_to_pinch + int(flush.get("weeks_after_pinch") or 0)
            stems_total = float(flush.get("stems_per_plant") or 0) * plants_planted
            if peak_offset < WEEKS:
                rows[peak_offset]["projected_stems"] += stems_total * PEAK_PRIMARY
            if peak_offset + 1 < WEEKS:
                rows[peak_offset + 1]["projected_stems"] += stems_total * PEAK_SECONDARY
    else:
        total_stems = float(protocol.get("total_stems_per_plant_life") or 0) * plants_planted
        if producing_idx and total_stems > 0:
            per_week = total_stems / len(producing_idx)
            for i in producing_idx:
                rows[i]["projected_stems"] += per_week

    # Seasonal multiplier and integer rounding (last step).
    for r in rows:
        m = r["week_start_date"].month
        f = seasonal_factors.get(m, 1.0)
        r["projected_stems"] = int(round(r["projected_stems"] * f))

    return rows
