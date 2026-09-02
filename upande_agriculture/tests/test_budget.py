"""Budget generation, forecast revisions and calibration against the DB."""

import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture import budget
from upande_agriculture.tests import default_uom, make_warehouse


class TestBudget(FrappeTestCase):
    HOUSE = "TEST GH BUDGET"
    VARIETY = "TEST-BUDGET-VARIETY"

    def setUp(self):
        self.house = make_warehouse(self.HOUSE)
        if not frappe.db.exists("Item", self.VARIETY):
            frappe.get_doc({
                "doctype": "Item", "item_code": self.VARIETY,
                "item_name": self.VARIETY, "item_group": "All Item Groups",
                "stock_uom": default_uom(),
            }).insert(ignore_permissions=True, ignore_mandatory=True)
        self.proto = self._protocol()
        self._cycle()

    def _protocol(self, name="TEST-BUDGET-PROTO"):
        values = {
            "variety_item": self.VARIETY,
            "weeks_to_first_bending": 5, "weeks_to_second_bending": 5,
            "weeks_between_cuts": 7, "stems_per_plant_first_harvest": 0.7,
            "stems_per_cut": 1.5, "max_stems_per_plant_per_cut": 1.61,
            "productive_life_weeks": 260,
        }
        if frappe.db.exists("Crop Protocol", name):
            d = frappe.get_doc("Crop Protocol", name)
            d.update(values)
            d.save(ignore_permissions=True)
            return d.name
        return frappe.get_doc({
            "doctype": "Crop Protocol", "protocol_name": name, **values,
        }).insert(ignore_permissions=True).name

    def _cycle(self, qty=10000, planting=datetime.date(2026, 1, 5)):
        existing = frappe.db.get_value("Crop Cycle", {
            "greenhouse": self.house, "variety": self.VARIETY,
            "planting_date": planting}, "name")
        if existing:
            return frappe.get_doc("Crop Cycle", existing)
        return frappe.get_doc({
            "doctype": "Crop Cycle", "greenhouse": self.house,
            "variety": self.VARIETY, "crop_protocol": self.proto,
            "planting_date": planting, "qty_planted": qty, "status": "Active",
        }).insert(ignore_permissions=True)

    def test_generates_a_mature_year_at_the_steady_rate(self):
        res = budget.generate_budget(self.house, self.VARIETY, 2028)
        self.assertEqual(res["cycles_used"], 1)
        doc = frappe.get_doc("Production Projection", res["projection"])
        rates = {int(w.projected_stems) for w in doc.weeks if w.projected_stems}
        # 10,000 plants x 1.61 / 7 weeks
        self.assertEqual(rates, {2300})
        self.assertEqual(res["total_stems"], 52 * 2300)

    def test_regenerating_preserves_locked_weeks(self):
        res = budget.generate_budget(self.house, self.VARIETY, 2028)
        doc = frappe.get_doc("Production Projection", res["projection"])
        doc.weeks[0].projected_stems = 999
        doc.weeks[0].week_locked = 1
        doc.save(ignore_permissions=True)

        again = budget.generate_budget(self.house, self.VARIETY, 2028)
        self.assertEqual(again["weeks_preserved"], 1)
        doc.reload()
        self.assertEqual(int(doc.weeks[0].projected_stems), 999)

    def test_overwrite_manual_ignores_the_lock(self):
        res = budget.generate_budget(self.house, self.VARIETY, 2028)
        doc = frappe.get_doc("Production Projection", res["projection"])
        doc.weeks[0].projected_stems = 999
        doc.weeks[0].week_locked = 1
        doc.save(ignore_permissions=True)

        budget.generate_budget(self.house, self.VARIETY, 2028, overwrite_manual=1)
        doc.reload()
        self.assertEqual(int(doc.weeks[0].projected_stems), 2300)

    def test_second_block_raises_the_whole_house(self):
        one = budget.generate_budget(self.house, self.VARIETY, 2029)["total_stems"]
        self._cycle(qty=5000, planting=datetime.date(2026, 6, 1))
        two = budget.generate_budget(self.house, self.VARIETY, 2029)
        self.assertEqual(two["cycles_used"], 2)
        self.assertGreater(two["total_stems"], one)

    def test_no_cycles_is_an_explicit_error(self):
        with self.assertRaises(frappe.ValidationError):
            budget.generate_budget(self.house, "NO-SUCH-VARIETY", 2028)

    def test_grade_split_covers_every_stem(self):
        proto = frappe.get_doc("Crop Protocol", self.proto)
        proto.grade_mix = []
        proto.append("grade_mix", {"length_cm": 60, "pct": 63.8, "source": "Measured"})
        proto.append("grade_mix", {"length_cm": 50, "pct": 36.2, "source": "Measured"})
        proto.save(ignore_permissions=True)

        budget.generate_budget(self.house, self.VARIETY, 2028)
        out = budget.budget_by_grade(self.house, self.VARIETY, 2028)
        for wk, split in out["weeks"].items():
            self.assertEqual(sum(split.values()), 2300, f"week {wk} lost stems")


class TestForecastRevisions(FrappeTestCase):
    HOUSE = "TEST GH FORECAST"
    VARIETY = "TEST-BUDGET-VARIETY"

    def setUp(self):
        from upande_agriculture.upande_agriculture.doctype.production_forecast.production_forecast import (
            ensure_fiscal_year,
        )

        self.house = make_warehouse(self.HOUSE)
        if not frappe.db.exists("Item", self.VARIETY):
            frappe.get_doc({
                "doctype": "Item", "item_code": self.VARIETY,
                "item_name": self.VARIETY, "item_group": "All Item Groups",
                "stock_uom": default_uom(),
            }).insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.delete("Production Forecast", {"greenhouse": self.house})
        ensure_fiscal_year(2028)

    def _budget(self):
        """A real budget so forecast rows have something to pull."""
        proto = "TEST-BUDGET-PROTO"
        if not frappe.db.exists("Crop Protocol", proto):
            frappe.get_doc({
                "doctype": "Crop Protocol", "protocol_name": proto,
                "variety_item": self.VARIETY, "weeks_to_first_bending": 5,
                "weeks_to_second_bending": 5, "weeks_between_cuts": 7,
                "stems_per_plant_first_harvest": 0.7, "stems_per_cut": 1.5,
                "max_stems_per_plant_per_cut": 1.61, "productive_life_weeks": 260,
            }).insert(ignore_permissions=True)
        if not frappe.db.exists("Crop Cycle", {"greenhouse": self.house,
                                               "variety": self.VARIETY}):
            frappe.get_doc({
                "doctype": "Crop Cycle", "greenhouse": self.house,
                "variety": self.VARIETY, "crop_protocol": proto,
                "planting_date": datetime.date(2026, 1, 5),
                "qty_planted": 10000, "status": "Active",
            }).insert(ignore_permissions=True)
        budget.generate_budget(self.house, self.VARIETY, 2028)

    def test_saving_a_forecast_fills_the_window_from_the_budget(self):
        """A forecast made in the UI must not come up with an empty table."""
        self._budget()
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 6, "status": "Active",
        }).insert(ignore_permissions=True)

        self.assertEqual([w.week_number for w in doc.weeks], [10, 11, 12, 13, 14, 15])
        # 2028 is a mature year: every week budgets 2,300.
        self.assertTrue(all(w.budget_stems == 2300 for w in doc.weeks))

    def test_widening_the_window_adds_weeks_and_keeps_edits(self):
        self._budget()
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 4, "status": "Active",
        }).insert(ignore_permissions=True)
        doc.weeks[0].revised_forecast_stems = 1500
        doc.weeks[0].reason = "Weather"
        doc.save(ignore_permissions=True)

        doc.window_weeks = 8
        doc.save(ignore_permissions=True)
        self.assertEqual(len(doc.weeks), 8)
        self.assertEqual(doc.weeks[0].revised_forecast_stems, 1500)
        self.assertEqual(doc.weeks[0].reason, "Weather")

    def test_narrowing_the_window_drops_weeks(self):
        self._budget()
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 8, "status": "Active",
        }).insert(ignore_permissions=True)
        doc.window_weeks = 3
        doc.save(ignore_permissions=True)
        self.assertEqual([w.week_number for w in doc.weeks], [10, 11, 12])

    def test_a_deliberate_zero_revision_is_not_reset(self):
        """Revising a week down to nothing is a judgement, not a blank."""
        self._budget()
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 4, "status": "Active",
        }).insert(ignore_permissions=True)
        doc.weeks[0].revised_forecast_stems = 0
        doc.weeks[0].reason = "Disease"
        doc.save(ignore_permissions=True)
        doc.window_weeks = 6
        doc.save(ignore_permissions=True)
        self.assertEqual(doc.weeks[0].revised_forecast_stems, 0)

    def test_budget_figures_refresh_when_the_budget_changes(self):
        self._budget()
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 4, "status": "Active",
        }).insert(ignore_permissions=True)
        self.assertEqual(doc.weeks[0].budget_stems, 2300)

        proj = frappe.get_doc("Production Projection", frappe.db.get_value(
            "Production Projection", {"greenhouse": self.house,
                                      "variety": self.VARIETY,
                                      "projection_year": 2028}, "name"))
        for w in proj.weeks:
            if int(w.week) == 10:
                w.projected_stems = 999
        proj.save(ignore_permissions=True)

        doc.save(ignore_permissions=True)
        self.assertEqual(doc.weeks[0].budget_stems, 999)

    def test_grid_payload_mode_picks_manual_vs_automated_budget(self):
        """The web page's Source toggle: Manual reads the typed Production
        Forecast figure, Automated reads the live Production Projection model
        untouched."""
        self._budget()

        def block_of(payload):
            return next(b for b in payload["blocks"]
                        if b["greenhouse"] == self.house and b["variety"] == self.VARIETY)

        def week10(mode):
            return block_of(budget.grid_payload(
                year=2028, start_year=2028, start_week=10,
                end_year=2028, end_week=10, mode=mode))["weekly"]["budget"][0]

        baseline = week10("automated")

        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 10, "window_weeks": 4, "status": "Active",
        }).insert(ignore_permissions=True)
        doc.weeks[0].manual_budget_stems = 9999
        doc.save(ignore_permissions=True)

        manual_payload = budget.grid_payload(year=2028, start_year=2028, start_week=10,
                                              end_year=2028, end_week=10, mode="manual")
        self.assertEqual(manual_payload["mode"], "manual")
        self.assertEqual(block_of(manual_payload)["weekly"]["budget"][0], 9999)

        # Typing a manual figure must never leak back into the automated model.
        self.assertEqual(week10("automated"), baseline)

        # Week 11 was never typed by hand — manual mode falls back to the
        # forecast's own System Budget snapshot rather than showing a false zero.
        week11_row = next(w for w in doc.weeks if w.week_number == 11)
        self.assertTrue(week11_row.budget_stems)
        manual11 = block_of(budget.grid_payload(
            year=2028, start_year=2028, start_week=11,
            end_year=2028, end_week=11, mode="manual"))["weekly"]["budget"][0]
        self.assertEqual(manual11, week11_row.budget_stems)

    def test_window_can_now_span_the_full_year(self):
        # Production Forecast carries the full-season manual budget too, so a
        # window that used to be refused past 26 weeks is fine up to the
        # calendar year itself.
        doc = frappe.get_doc({
            "doctype": "Production Forecast", "greenhouse": self.house,
            "variety": self.VARIETY, "forecast_year": 2028,
            "start_week": 1, "window_weeks": 40, "status": "Active",
        }).insert(ignore_permissions=True)
        self.assertEqual(len(doc.weeks), 40)

    def test_absurd_window_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype": "Production Forecast", "greenhouse": self.house,
                "variety": self.VARIETY, "forecast_year": 2028,
                "start_week": 10, "window_weeks": 60, "status": "Active",
            }).insert(ignore_permissions=True)

    def test_first_revision_starts_at_one(self):
        r = budget.revise_forecast(self.house, self.VARIETY, 2028, start_week=10)
        self.assertEqual(r["revision"], 1)
        self.assertIsNone(r["superseded"])
        self.assertEqual(r["weeks"], 6)

    def test_revising_supersedes_without_deleting(self):
        first = budget.revise_forecast(self.house, self.VARIETY, 2028, start_week=10)
        second = budget.revise_forecast(self.house, self.VARIETY, 2028, start_week=11)

        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["superseded"], first["forecast"])
        # The old revision survives, readable, flagged.
        self.assertEqual(
            frappe.db.get_value("Production Forecast", first["forecast"], "status"),
            "Superseded")
        self.assertEqual(
            frappe.db.get_value("Production Forecast", second["forecast"], "status"),
            "Active")

    def test_window_length_is_respected(self):
        r = budget.revise_forecast(self.house, self.VARIETY, 2028,
                                   start_week=10, window_weeks=10)
        self.assertEqual(r["weeks"], 10)

    def test_window_is_clipped_at_week_52(self):
        r = budget.revise_forecast(self.house, self.VARIETY, 2028,
                                   start_week=50, window_weeks=10)
        self.assertEqual(r["weeks"], 3)


class TestVarietyParsing(FrappeTestCase):
    def test_base_variety_strips_the_length_grade(self):
        for raw, want in [("ATHENA-60CM", "ATHENA"), ("Ever-Red-70cm", "Ever-Red"),
                          ("Athena", "Athena"), ("Proud-60 cm", "Proud")]:
            self.assertEqual(budget.base_variety(raw), want)

    def test_grade_is_read_back(self):
        self.assertEqual(budget.grade_of("ATHENA-60CM"), 60)
        self.assertIsNone(budget.grade_of("ATHENA"))


class TestWeekSpan(FrappeTestCase):
    def test_span_within_one_year(self):
        self.assertEqual(budget.week_span(2026, 10, 2026, 13),
                         [(2026, 10), (2026, 11), (2026, 12), (2026, 13)])

    def test_span_rolls_over_the_year_end(self):
        # 2025 is a 52-week ISO year, so 52 is followed by 2026 W1.
        self.assertEqual(budget.week_span(2025, 51, 2026, 2),
                         [(2025, 51), (2025, 52), (2026, 1), (2026, 2)])

    def test_span_rolls_over_a_53_week_year(self):
        # 2026 has 53 ISO weeks — W53 must not be skipped.
        self.assertEqual(budget.week_span(2026, 52, 2027, 1),
                         [(2026, 52), (2026, 53), (2027, 1)])

    def test_end_before_start_is_empty(self):
        self.assertEqual(budget.week_span(2026, 10, 2026, 9), [])

    def test_span_is_bounded(self):
        self.assertEqual(len(budget.week_span(2000, 1, 2100, 1)),
                         budget.MAX_SPAN_WEEKS)


class TestFarmMapGeometry(FrappeTestCase):
    def test_house_strips_prefix_and_company(self):
        from upande_agriculture import farm_map
        for raw, want in [("Main GH 02 - TFC", "GH 02"), ("Main GH 21 - MFL", "GH 21"),
                          ("GH04 - KR", "GH04"), ("Block K1 - TFC", "Block K1")]:
            self.assertEqual(farm_map.house(raw), want)

    def test_ring_area_matches_a_known_rectangle(self):
        # ~100 m east-west by ~100 m north-south on the equator.
        from upande_agriculture import farm_map
        d = 100 / 111_320.0
        ring = [[0, 0], [d, 0], [d, d], [0, d], [0, 0]]
        self.assertAlmostEqual(farm_map._ring_area_m2(ring), 10_000, delta=50)

    def test_ring_area_ignores_degenerate_rings(self):
        from upande_agriculture import farm_map
        self.assertEqual(farm_map._ring_area_m2([[0, 0], [1, 1]]), 0.0)

    def test_polygons_survive_a_double_encoded_field(self):
        import json
        from upande_agriculture import farm_map
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon",
                                             "coordinates": [[[1, 2], [3, 4], [5, 6], [1, 2]]]}}]}
        inner = json.dumps(fc)
        self.assertEqual(len(farm_map._polygons_from_geojson(inner)), 1)
        # a value that was JSON-encoded a second time on the way into the field
        self.assertEqual(len(farm_map._polygons_from_geojson(json.dumps(inner))), 1)

    def test_polygons_skip_non_polygon_geometry(self):
        from upande_agriculture import farm_map
        fc = {"features": [{"geometry": {"type": "LineString", "coordinates": [[1, 2], [3, 4]]}}]}
        self.assertEqual(farm_map._polygons_from_geojson(fc), [])
        self.assertEqual(farm_map._polygons_from_geojson(None), [])
        self.assertEqual(farm_map._polygons_from_geojson("not json"), [])

    def test_bundled_survey_is_present_and_well_formed(self):
        from upande_agriculture import farm_map
        geom = farm_map._bundled_geometry()
        self.assertTrue(geom, "bundled greenhouse geometry is missing")
        for name, polys in geom.items():
            for poly in polys:
                for ring in poly:
                    self.assertGreaterEqual(len(ring), 4, f"{name} ring is not closeable")
                    for lon, lat in ring:
                        # Kenya: the survey must not have been written [lat, lon].
                        self.assertTrue(30 < lon < 42, f"{name} longitude {lon} out of range")
                        self.assertTrue(-5 < lat < 5, f"{name} latitude {lat} out of range")


class TestForecastRoundTrip(FrappeTestCase):
    """The grid used to write forecasts and never read them back."""

    def test_grade_is_normalised(self):
        self.assertEqual(budget.normalise_grade("__all__"), "all")
        self.assertEqual(budget.normalise_grade(""), "all")
        self.assertEqual(budget.normalise_grade(None), "all")
        self.assertEqual(budget.normalise_grade(" 60 cm "), "60 cm")

    def test_active_forecasts_is_empty_without_pairs(self):
        self.assertEqual(budget.active_forecasts([]), {})

    def test_seeded_rows_are_not_reported_as_revisions(self):
        # Opening a window writes forecast == budget for every week. Those are
        # scaffolding; only a week somebody changed counts as a revision.
        house = frappe.db.get_value("Crop Cycle", {"status": ("!=", "Ended")}, "greenhouse")
        if not house:
            self.skipTest("no crop cycles on this site")
        cycle = frappe.db.get_value(
            "Crop Cycle", {"greenhouse": house, "status": ("!=", "Ended")},
            ["name", "variety"], as_dict=True)
        year = 2031  # a year with no other fixtures in play
        r = budget.revise_forecast(house, cycle.variety, year, 10, 4)
        try:
            self.assertFalse(budget.active_forecasts([(house, cycle.variety, year)]),
                             "a freshly seeded window must report no revisions")

            budget.set_forecast_cell(cycle.name, "__all__", 11, 4321, year, reason="Weather")
            got = budget.active_forecasts([(house, cycle.variety, year)])
            self.assertEqual(got[(house, cycle.variety)][(year, 11, "all")], 4321)
            # untouched weeks in the same window stay absent
            self.assertNotIn((year, 12, "all"), got[(house, cycle.variety)])

            # a grade revision is independent of the week total
            budget.set_forecast_cell(cycle.name, "60 cm", 11, 777, year)
            got = budget.active_forecasts([(house, cycle.variety, year)])
            block = got[(house, cycle.variety)]
            self.assertEqual(block[(year, 11, "all")], 4321)
            self.assertEqual(block[(year, 11, "60 cm")], 777)
        finally:
            for n in frappe.db.get_all("Production Forecast",
                                       {"forecast_year": year}, pluck="name"):
                frappe.delete_doc("Production Forecast", n, force=1, ignore_permissions=True)

    def test_narrowing_keeps_grade_rows_for_surviving_weeks(self):
        house = frappe.db.get_value("Crop Cycle", {"status": ("!=", "Ended")}, "greenhouse")
        if not house:
            self.skipTest("no crop cycles on this site")
        cycle = frappe.db.get_value(
            "Crop Cycle", {"greenhouse": house, "status": ("!=", "Ended")},
            ["name", "variety"], as_dict=True)
        year = 2032
        budget.revise_forecast(house, cycle.variety, year, 10, 8)
        try:
            budget.set_forecast_cell(cycle.name, "60 cm", 11, 500, year)   # inside
            budget.set_forecast_cell(cycle.name, "60 cm", 16, 600, year)   # will be dropped
            name = frappe.db.get_value("Production Forecast", {
                "greenhouse": house, "variety": cycle.variety,
                "forecast_year": year, "status": "Active"}, "name")
            doc = frappe.get_doc("Production Forecast", name)
            doc.window_weeks = 3          # keep weeks 10-12 only
            doc.save(ignore_permissions=True)
            keys = {(int(w.week_number), w.grade) for w in doc.weeks}
            self.assertIn((11, "60 cm"), keys, "a grade row inside the window must survive")
            self.assertNotIn((16, "60 cm"), keys, "a week outside the window is dropped")
            self.assertEqual(sorted({int(w.week_number) for w in doc.weeks}), [10, 11, 12])
        finally:
            for n in frappe.db.get_all("Production Forecast",
                                       {"forecast_year": year}, pluck="name"):
                frappe.delete_doc("Production Forecast", n, force=1, ignore_permissions=True)


class TestBudgetOverrides(FrappeTestCase):
    """A month typed on the Budget view must read back as the same number,
    in the same month. It used to round-drift and straddle two months."""

    def setUp(self):
        # Pick a block that already has a budget — set_budget_cell overrides a
        # Production Projection, so one has to exist. Which cycle sorts first
        # depends on the site's data, so do not rely on it.
        name = frappe.db.sql("""
            SELECT cc.name FROM `tabCrop Cycle` cc
            JOIN `tabProduction Projection` pp
              ON pp.greenhouse = cc.greenhouse AND pp.variety = cc.variety
            WHERE cc.status != 'Ended' LIMIT 1""")
        if not name:
            self.skipTest("no crop cycle with a generated budget on this site")
        self.cycle = frappe.db.get_value(
            "Crop Cycle", name[0][0], ["name", "greenhouse", "variety"], as_dict=True)
        frappe.db.sql("UPDATE `tabProjection Week` SET manual_override = 0")

    def tearDown(self):
        frappe.db.sql("UPDATE `tabProjection Week` SET manual_override = 0")
        frappe.db.commit()

    def _read(self):
        got = budget.manual_month_overrides(
            [(self.cycle.greenhouse, self.cycle.variety, 2026)])
        return got.get((self.cycle.greenhouse, self.cycle.variety), {})

    def test_month_reads_back_exactly(self):
        # 54321 over 5 weeks is not divisible; naive rounding returned 54325.
        for month, value in (("Dec", 54321), ("Feb", 99999), ("Sep", 7)):
            with self.subTest(month=month):
                budget.set_budget_cell(self.cycle.name, month, value)
                self.assertEqual(self._read().get(month), value)
                frappe.db.sql("UPDATE `tabProjection Week` SET manual_override = 0")

    def test_month_does_not_bleed_into_its_neighbour(self):
        budget.set_budget_cell(self.cycle.name, "Dec", 54321)
        got = self._read()
        self.assertEqual(list(got), ["Dec"], f"override leaked across months: {got}")

    def test_weeks_are_assigned_by_their_thursday(self):
        # The write picks weeks and the read maps them back; if the two rules
        # disagree the total lands in two months.
        import datetime as dt
        from upande_agriculture.projection_calc import iso_weeks_in_year
        for cal_month in range(1, 13):
            weeks = [w for w in range(1, iso_weeks_in_year(2026) + 1)
                     if dt.date.fromisocalendar(2026, w, 4).month == cal_month]
            self.assertTrue(weeks, f"month {cal_month} owns no week")
            for w in weeks:
                self.assertEqual(dt.date.fromisocalendar(2026, w, 4).month, cal_month)


class TestForecastHistory(FrappeTestCase):
    """A planner must be able to see what a cell used to say, and why."""

    def setUp(self):
        self.cycle = frappe.db.get_value(
            "Crop Cycle", {"status": ("!=", "Ended")},
            ["name", "greenhouse", "variety"], as_dict=True)
        if not self.cycle:
            self.skipTest("no crop cycles on this site")
        self.year = 2033
        budget.revise_forecast(self.cycle.greenhouse, self.cycle.variety, self.year, 20, 4)

    def tearDown(self):
        for n in frappe.db.get_all("Production Forecast",
                                   {"forecast_year": self.year}, pluck="name"):
            frappe.delete_doc("Production Forecast", n, force=1, ignore_permissions=True)
        frappe.db.commit()

    def test_history_reports_the_live_revision_and_reason(self):
        budget.set_forecast_cell(self.cycle.name, "__all__", 21, 5000,
                                 self.year, reason="Disease")
        h = budget.cell_history(self.cycle.name, 21, "__all__", self.year)
        live = [r for r in h["revisions"] if r["status"] == "Active"]
        self.assertEqual(len(live), 1)
        self.assertEqual(live[0]["value"], 5000)
        self.assertEqual(live[0]["reason"], "Disease")
        self.assertTrue(live[0]["changed"])

    def test_superseded_revision_stays_readable(self):
        budget.set_forecast_cell(self.cycle.name, "__all__", 21, 5000, self.year)
        budget.revise_blocks([self.cycle.name], self.year, 20, 4)
        h = budget.cell_history(self.cycle.name, 21, "__all__", self.year)
        by_status = {r["status"]: r for r in h["revisions"]}
        self.assertIn("Superseded", by_status, "the previous revision must survive")
        self.assertIn("Active", by_status)
        # judgement carries forward into the new revision
        self.assertEqual(by_status["Active"]["value"], 5000)
        self.assertEqual(by_status["Superseded"]["value"], 5000)
        self.assertEqual(by_status["Active"]["revision"],
                         by_status["Superseded"]["revision"] + 1)

    def test_history_of_an_untouched_cell_is_empty_of_changes(self):
        h = budget.cell_history(self.cycle.name, 22, "__all__", self.year)
        self.assertFalse([r for r in h["revisions"] if r["changed"]])

    def test_revise_blocks_dedupes_and_reports(self):
        r = budget.revise_blocks([self.cycle.name, self.cycle.name], self.year, 20, 4)
        self.assertEqual(r["revised"], 1, "the same block twice is still one revision")
        self.assertFalse(r["failed"])


class TestWeekPlan(FrappeTestCase):
    """The week's work board and the map's live-operation feed."""

    def setUp(self):
        from upande_agriculture import plan
        self.plan = plan
        self.house = frappe.db.get_value("Crop Cycle", {"status": ("!=", "Ended")}, "greenhouse")
        if not self.house:
            self.skipTest("no crop cycles on this site")
        self.year, self.week = 2035, 12
        self._clean()

    def tearDown(self):
        self._clean()
        frappe.db.commit()

    def _clean(self):
        for n in frappe.db.get_all("Production Plan Form",
                                   {"plan_year": self.year, "plan_week": self.week},
                                   pluck="name"):
            frappe.delete_doc("Production Plan Form", n, force=1, ignore_permissions=True)

    def test_add_task_creates_the_plan_then_reuses_it(self):
        a = self.plan.add_task(self.house, "Cut block A", "Harvest", "Monday",
                               year=self.year, week=self.week)
        b = self.plan.add_task(self.house, "Spray round", "Spray", "Tuesday",
                               year=self.year, week=self.week)
        self.assertEqual(a["plan"], b["plan"], "a second task must not open a second plan")
        self.assertEqual(b["tasks"], 2)

    def test_unknown_operation_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            self.plan.add_task(self.house, "Nonsense", "Teleport",
                               year=self.year, week=self.week)

    def test_status_transitions_stamp_the_clock(self):
        self.plan.add_task(self.house, "Cut block A", "Harvest", "Monday",
                           year=self.year, week=self.week)
        w = self.plan.week_plan(self.year, self.week)
        task = w["by_day"]["Monday"][0]["task"]

        self.plan.set_task_status(task, "In Progress")
        row = frappe.db.get_value("Production Plan Task", task,
                                  ["status", "started_at", "completed_at"], as_dict=True)
        self.assertEqual(row.status, "In Progress")
        self.assertIsNotNone(row.started_at)
        self.assertIsNone(row.completed_at)

        self.plan.set_task_status(task, "Done", note="cut early")
        row = frappe.db.get_value("Production Plan Task", task,
                                  ["status", "completed_at", "completion_note"], as_dict=True)
        self.assertEqual(row.status, "Done")
        self.assertIsNotNone(row.completed_at)
        self.assertEqual(row.completion_note, "cut early")

        # Reopening must clear the clock, or a re-run looks already finished.
        self.plan.set_task_status(task, "Open")
        row = frappe.db.get_value("Production Plan Task", task,
                                  ["started_at", "completed_at"], as_dict=True)
        self.assertIsNone(row.started_at)
        self.assertIsNone(row.completed_at)

    def test_progress_counts_done_and_skipped(self):
        for i, day in enumerate(("Monday", "Tuesday", "Wednesday", "Thursday")):
            self.plan.add_task(self.house, f"Job {i}", "Scout", day,
                               year=self.year, week=self.week)
        w = self.plan.week_plan(self.year, self.week)
        self.assertEqual(w["total"], 4)
        self.assertEqual(w["progress"], 0)
        tasks = [t["task"] for d in w["days"] for t in w["by_day"][d]]
        self.plan.set_task_status(tasks[0], "Done")
        self.plan.set_task_status(tasks[1], "Skipped")
        w = self.plan.week_plan(self.year, self.week)
        self.assertEqual(w["progress"], 50, "a skipped task is dealt with, not outstanding")

    def test_house_tasks_of_an_unplanned_house_is_empty_not_an_error(self):
        r = self.plan.house_tasks(self.house, self.year, self.week)
        self.assertEqual(r["tasks"], [])
        self.assertEqual(r["open"], 0)

    def test_live_operations_ignores_another_week(self):
        # 2035-W12 is not the current week, so nothing in it is "under way".
        self.plan.add_task(self.house, "Cut block A", "Harvest", "Monday",
                           year=self.year, week=self.week)
        w = self.plan.week_plan(self.year, self.week)
        self.plan.set_task_status(w["by_day"]["Monday"][0]["task"], "In Progress")
        live = self.plan.live_operations(self.year, self.week)
        self.assertEqual(live["houses"], {},
                         "only the current week can have work under way")
