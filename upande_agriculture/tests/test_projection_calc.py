import datetime
import unittest

from upande_agriculture.projection_calc import calculate_weekly_projection


class TestProjectionCalc(unittest.TestCase):
    def _base_protocol(self):
        return {
            "weeks_to_pinch": 4,
            "weeks_pinch_to_first_harvest": 8,
            "total_weeks_in_ground": 52,
            "total_stems_per_plant_life": 120.0,
            "flush_schedule": [],
        }

    def test_returns_52_weeks(self):
        rows = calculate_weekly_projection(
            self._base_protocol(), plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
        )
        self.assertEqual(len(rows), 52)
        self.assertEqual(rows[0]["week_number"], 1)
        self.assertEqual(rows[-1]["week_number"], 52)

    def test_zero_stems_during_pinch_window(self):
        rows = calculate_weekly_projection(
            self._base_protocol(), plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
        )
        # First 4+8 = 12 weeks should be zero (pinch + ramp)
        for r in rows[:12]:
            self.assertEqual(r["projected_stems"], 0)

    def test_evenly_distributed_when_no_flush_schedule(self):
        rows = calculate_weekly_projection(
            self._base_protocol(), plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
        )
        # Production window: weeks 13..52 = 40 weeks
        # Total stems = 120 * 1000 = 120000 -> 3000/wk
        producing = [r for r in rows if r["projected_stems"] > 0]
        self.assertEqual(len(producing), 40)
        for r in producing:
            self.assertEqual(r["projected_stems"], 3000)

    def test_flush_schedule_overrides_even(self):
        proto = self._base_protocol()
        proto["flush_schedule"] = [
            {"flush_number": 1, "stems_per_plant": 5.0, "weeks_after_pinch": 8},
            {"flush_number": 2, "stems_per_plant": 6.0, "weeks_after_pinch": 18},
        ]
        rows = calculate_weekly_projection(
            proto, plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
        )
        # Flush 1 peak at week 4+8 = 12 (60%) and week 13 (40%):
        #   week_number 13 (zero-indexed 12) -> 5*1000*0.60 = 3000
        #   week_number 14 (zero-indexed 13) -> 5*1000*0.40 = 2000
        self.assertEqual(rows[12]["projected_stems"], 3000)
        self.assertEqual(rows[13]["projected_stems"], 2000)
        # Flush 2 peak at week 4+18 = 22:
        self.assertEqual(rows[22]["projected_stems"], 3600)  # 6*1000*0.60
        self.assertEqual(rows[23]["projected_stems"], 2400)

    def test_flush_before_harvest_window_is_dropped(self):
        proto = self._base_protocol()
        proto["flush_schedule"] = [
            # weeks_after_pinch = 0 -> peak at week_to_pinch = 4, BEFORE harvest opens at week 12
            {"flush_number": 1, "stems_per_plant": 5.0, "weeks_after_pinch": 0},
        ]
        rows = calculate_weekly_projection(
            proto, plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
        )
        # Week 5 (peak_offset 4) and week 6 (peak_offset 5) are both pre-harvest -> should be 0
        self.assertEqual(rows[4]["projected_stems"], 0)
        self.assertEqual(rows[5]["projected_stems"], 0)

    def test_seasonal_factor_applied(self):
        rows = calculate_weekly_projection(
            self._base_protocol(), plants_planted=1000,
            planting_date=datetime.date(2026, 1, 5),
            seasonal_factors={4: 0.5},   # April halved
        )
        # Find a producing week in April -- ISO week_start_date in month 4
        april_weeks = [
            r for r in rows
            if r["projected_stems"] > 0 and r["week_start_date"].month == 4
        ]
        self.assertTrue(april_weeks)
        for r in april_weeks:
            self.assertEqual(r["projected_stems"], 1500)  # 3000 * 0.5


if __name__ == "__main__":
    unittest.main()
