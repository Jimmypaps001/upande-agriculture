"""The production model, tested without a Frappe runtime."""

import datetime
import unittest

from upande_agriculture.projection_calc import (
    build_budget_year,
    cycle_weekly_stems,
    production_start,
    split_by_grade,
)

# 10,000 plants, 7-week cut cycle, plateau 1.61 stems/plant/cut.
# Steady state = 10000 * 1.61 / 7 = 2300 stems/week.
PROTOCOL = {
    "weeks_to_first_bending": 5,
    "weeks_to_second_bending": 5,
    "weeks_between_cuts": 7,
    "stems_per_plant_first_harvest": 0.7,
    "stems_per_cut": 1.5,
    "max_stems_per_plant_per_cut": 1.61,
    "reject_pct": 0,
    "productive_life_weeks": 260,
}
CYCLE = {"planting_date": datetime.date(2026, 1, 5), "qty_planted": 10000}


class TestProductionStart(unittest.TestCase):
    def test_derived_from_protocol_offsets(self):
        # planting + 5 (first bend) + 5 (second bend) + 7 (first cut) = 17 weeks
        self.assertEqual(
            production_start(CYCLE, PROTOCOL),
            datetime.date(2026, 1, 5) + datetime.timedelta(weeks=17),
        )

    def test_recorded_bending_date_wins(self):
        cycle = {**CYCLE, "second_bending_date": datetime.date(2026, 6, 1)}
        self.assertEqual(
            production_start(cycle, PROTOCOL),
            datetime.date(2026, 6, 1) + datetime.timedelta(weeks=7),
        )


class TestSteadyState(unittest.TestCase):
    def test_converges_to_plants_times_plateau_over_cut_cycle(self):
        """A mature block sits exactly on the plateau, every week."""
        weeks = build_budget_year([(CYCLE, PROTOCOL)], 2028)
        rates = [v for v in weeks.values() if v]
        self.assertEqual(len(rates), 52, "a mature block produces every week")
        self.assertEqual(set(rates), {2300})

    def test_early_waves_run_above_the_plateau(self):
        """The first cut lands almost together, so it out-rates steady state.

        The ramp lives in year 1 only — by year 2 the spread has filled a cut
        cycle and the block is flat.
        """
        self.assertGreater(max(build_budget_year([(CYCLE, PROTOCOL)], 2026).values()), 2300)
        self.assertEqual(max(build_budget_year([(CYCLE, PROTOCOL)], 2027).values()), 2300)

    def test_annual_total_matches_weekly_rate(self):
        total = sum(build_budget_year([(CYCLE, PROTOCOL)], 2028).values())
        self.assertEqual(total, 52 * 2300)

    def test_ceiling_caps_the_multiplier(self):
        """Without the ceiling, 1.5x per cut compounds without bound."""
        weeks = build_budget_year([(CYCLE, PROTOCOL)], 2030)
        self.assertEqual(max(weeks.values()), 2300)


class TestContinuity(unittest.TestCase):
    """De-synchronisation must close the gaps between waves.

    Widening the spread one week per wave left dead weeks deep into year two;
    a rose block reaches continuous production inside its first year.
    """

    def test_no_gaps_once_established(self):
        weeks = build_budget_year([(CYCLE, PROTOCOL)], 2027)
        missing = [w for w in range(1, 53) if not weeks.get(w)]
        self.assertEqual(missing, [], f"dead weeks in year 2: {missing}")

    def test_continuous_for_an_eight_week_cut_cycle_too(self):
        """The gap the grower actually hit: 8-week cycle, 2.0 first harvest."""
        proto = {**PROTOCOL, "weeks_between_cuts": 8,
                 "stems_per_plant_first_harvest": 2.0,
                 "max_stems_per_plant_per_cut": 3.0}
        weeks = build_budget_year([(CYCLE, proto)], 2027)
        missing = [w for w in range(1, 53) if not weeks.get(w)]
        self.assertEqual(missing, [], f"dead weeks: {missing}")

    def test_spread_reaches_a_full_cut_cycle_by_wave_four(self):
        """Doubling gets there in 4 waves; +1 per wave would take 8."""
        self.assertEqual(min(2 ** (4 - 1), 8), 8)


class TestRamp(unittest.TestCase):
    def test_first_year_is_spiky_and_below_steady_total(self):
        y1 = sum(build_budget_year([(CYCLE, PROTOCOL)], 2026).values())
        y2 = sum(build_budget_year([(CYCLE, PROTOCOL)], 2027).values())
        self.assertLess(y1, y2, "ramp year must yield less than a mature year")

    def test_nothing_before_first_harvest(self):
        weeks = build_budget_year([(CYCLE, PROTOCOL)], 2026)
        # First cut is 17 weeks after a 5 Jan planting -> ISO week 18.
        self.assertFalse([w for w in weeks if w < 18])


class TestGuards(unittest.TestCase):
    def test_no_ceiling_means_no_projection(self):
        proto = {**PROTOCOL, "max_stems_per_plant_per_cut": 0}
        self.assertEqual(cycle_weekly_stems(CYCLE, proto), {})

    def test_no_plants_means_no_projection(self):
        self.assertEqual(cycle_weekly_stems({**CYCLE, "qty_planted": 0}, PROTOCOL), {})

    def test_uprooting_stops_production(self):
        cycle = {**CYCLE, "cycle_end_date": datetime.date(2026, 12, 31)}
        self.assertFalse(build_budget_year([(cycle, PROTOCOL)], 2027))

    def test_reject_pct_reduces_output(self):
        proto = {**PROTOCOL, "reject_pct": 10}
        full = sum(build_budget_year([(CYCLE, PROTOCOL)], 2028).values())
        cut = sum(build_budget_year([(CYCLE, proto)], 2028).values())
        self.assertAlmostEqual(cut / full, 0.9, places=2)


class TestMultipleCycles(unittest.TestCase):
    def test_two_blocks_in_one_house_are_summed(self):
        old = CYCLE
        new = {"planting_date": datetime.date(2026, 6, 1), "qty_planted": 5000}
        both = build_budget_year([(old, PROTOCOL), (new, PROTOCOL)], 2029)
        just_old = build_budget_year([(old, PROTOCOL)], 2029)
        self.assertGreater(sum(both.values()), sum(just_old.values()))
        # 15,000 plants at the plateau over a 7-week cycle.
        self.assertEqual(max(both.values()), round(15000 * 1.61 / 7))


class TestGradeSplit(unittest.TestCase):
    MIX = [
        {"length_cm": 60, "pct": 63.8}, {"length_cm": 50, "pct": 30.4},
        {"length_cm": 40, "pct": 5.0}, {"length_cm": 70, "pct": 0.4},
        {"length_cm": 100, "pct": 0.3},
    ]

    def test_split_is_exact(self):
        """Rounding must never lose or invent a stem."""
        for stems in (1, 7, 2300, 119_598):
            self.assertEqual(sum(split_by_grade(stems, self.MIX).values()), stems)

    def test_dominant_grade_gets_the_biggest_share(self):
        out = split_by_grade(2300, self.MIX)
        self.assertEqual(max(out, key=lambda k: out[k]), 60)

    def test_shares_are_normalised(self):
        """A mix summing to 80% still allocates every stem."""
        out = split_by_grade(1000, [{"length_cm": 60, "pct": 40},
                                    {"length_cm": 50, "pct": 40}])
        self.assertEqual(sum(out.values()), 1000)
        self.assertEqual(out[60], 500)

    def test_empty_mix_returns_nothing(self):
        self.assertEqual(split_by_grade(100, []), {})


class TestIsoYearLength(unittest.TestCase):
    """2026 has 53 ISO weeks. A budget that stops at 52 loses a week of stems."""

    def test_known_53_week_years(self):
        from upande_agriculture.projection_calc import iso_weeks_in_year
        self.assertEqual(iso_weeks_in_year(2026), 53)
        for year in (2024, 2025, 2027, 2028):
            self.assertEqual(iso_weeks_in_year(year), 52, year)

    def test_week_53_is_budgeted(self):
        proto = {**PROTOCOL, "weeks_between_cuts": 7}
        cycle = {"planting_date": datetime.date(2024, 1, 1), "qty_planted": 10000}
        weeks = build_budget_year([(cycle, proto)], 2026)
        self.assertIn(53, weeks, "week 53 of 2026 must carry stems")
