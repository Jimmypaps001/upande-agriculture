import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.tests import default_company, default_employee, default_uom, make_warehouse
from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import parse_bed_range


class TestCropCycle(FrappeTestCase):
    def _item(self, name="TEST-VARIETY-A"):
        if not frappe.db.exists("Item", name):
            frappe.get_doc({
                "doctype": "Item", "item_code": name, "item_name": name,
                "item_group": "All Item Groups", "stock_uom": default_uom(),
            }).insert(ignore_permissions=True, ignore_mandatory=True)
        return name

    def _protocol(self, name="TEST-PROTO-1"):
        """Idempotent: refresh values so a stale row can't skew a test."""
        values = {
            "variety_item": self._item(),
            "weeks_to_first_bending": 5,
            "weeks_to_second_bending": 5,
            "weeks_between_cuts": 7,
            "stems_per_plant_first_harvest": 0.7,
            "stems_per_cut": 1.5,
            "max_stems_per_plant_per_cut": 1.61,
            "productive_life_weeks": 260,
        }
        if frappe.db.exists("Crop Protocol", name):
            doc = frappe.get_doc("Crop Protocol", name)
            doc.update(values)
            doc.save(ignore_permissions=True)
            return doc.name
        return frappe.get_doc({
            "doctype": "Crop Protocol", "protocol_name": name, **values,
        }).insert(ignore_permissions=True).name

    def _cycle(self, house, **kw):
        payload = {
            "doctype": "Crop Cycle",
            "greenhouse": house,
            "variety": self._item(),
            "crop_protocol": self._protocol(),
            "planting_date": datetime.date(2026, 1, 5),
            "qty_planted": 10000,
            "status": "Active",
        }
        payload.update(kw)
        return frappe.get_doc(payload).insert(ignore_permissions=True)

    # -- uprooting and replanting ---------------------------------------

    def test_end_date_ends_the_cycle(self):
        c = self._cycle(make_warehouse("TEST GH UPROOT"),
                        cycle_end_date=datetime.date(2027, 3, 1))
        self.assertEqual(c.status, "Ended",
                         "an uprooting date means the block is out of the ground")

    def test_ending_without_a_date_is_refused(self):
        house = make_warehouse("TEST GH UPROOT2")
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, status="Ended")

    def test_replant_must_stay_in_the_same_house(self):
        old = self._cycle(make_warehouse("TEST GH REPLANT"),
                          cycle_end_date=datetime.date(2027, 3, 1))
        with self.assertRaises(frappe.ValidationError):
            self._cycle(make_warehouse("TEST GH ELSEWHERE"), replaces=old.name)

    def test_replant_records_the_block_it_replaced(self):
        house = make_warehouse("TEST GH REPLANT2")
        old = self._cycle(house, cycle_end_date=datetime.date(2027, 3, 1))
        new = self._cycle(house, replaces=old.name,
                          planting_date=datetime.date(2027, 4, 1))
        self.assertEqual(new.replaces, old.name)
        self.assertEqual(new.status, "Active")

    # -- derivation ----------------------------------------------------

    def test_bending_dates_derived_from_protocol(self):
        c = self._cycle(make_warehouse("TEST GH BEND"))
        planting = datetime.date(2026, 1, 5)
        self.assertEqual(frappe.utils.getdate(c.first_bending_date),
                         planting + datetime.timedelta(weeks=5))
        self.assertEqual(frappe.utils.getdate(c.second_bending_date),
                         planting + datetime.timedelta(weeks=10))
        self.assertEqual(frappe.utils.getdate(c.planned_uprooting_date),
                         planting + datetime.timedelta(weeks=260))

    def test_recorded_bending_date_is_not_overwritten(self):
        actual = datetime.date(2026, 3, 1)
        c = self._cycle(make_warehouse("TEST GH BEND2"), first_bending_date=actual)
        self.assertEqual(frappe.utils.getdate(c.first_bending_date), actual)
        self.assertEqual(frappe.utils.getdate(c.second_bending_date),
                         actual + datetime.timedelta(weeks=5))

    def test_actual_bending_date_overrides_the_planned_one(self):
        """A hand-recorded actual wins, and every other field keeps reading first_bending_date."""
        actual = datetime.date(2026, 4, 20)
        c = self._cycle(make_warehouse("TEST GH BEND3"),
                        actual_first_bending_date=actual)
        self.assertEqual(frappe.utils.getdate(c.first_bending_date), actual)

    def test_farm_is_fetched_from_the_greenhouse(self):
        house = make_warehouse("TEST GH FARM")
        farm = frappe.db.get_value("Warehouse", house, "custom_farm")
        c = self._cycle(house)
        self.assertEqual(c.farm, farm)

    # -- the density guard ---------------------------------------------

    def _bed(self, house, n, length, width):
        name = f"{house} - Bed {n}"
        if frappe.db.exists("Bed", name):
            frappe.delete_doc("Bed", name, force=True, ignore_permissions=True)
        return frappe.get_doc({
            "doctype": "Bed", "greenhouse": house, "unit_type": "Bed", "bed": n,
            "bed_length": length, "bed_width": width, "bed_area": length * width,
        }).insert(ignore_permissions=True).name

    def test_beds_roll_up_into_area_and_implied_plants(self):
        house = make_warehouse("TEST GH BEDS")
        beds = [self._bed(house, i, 20, 0.85) for i in (1, 2, 3)]
        c = self._cycle(house, qty_planted=300, plants_per_sqm=6,
                        beds=[{"bed": b} for b in beds])
        self.assertAlmostEqual(c.planted_area, 3 * 20 * 0.85, places=2)
        self.assertEqual(c.implied_plants, round(51 * 6))

    def test_wrong_bed_length_is_rejected(self):
        """The 4m-vs-20m error: 50 beds of 4x0.85 cannot hold 10,000 plants."""
        house = make_warehouse("TEST GH BADBEDS")
        beds = [self._bed(house, i, 4, 0.85) for i in range(1, 6)]
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, qty_planted=10000, plants_per_sqm=7,
                        beds=[{"bed": b} for b in beds])

    def test_plausible_density_passes(self):
        house = make_warehouse("TEST GH GOODBEDS")
        beds = [self._bed(house, i, 20, 0.85) for i in range(1, 6)]
        area = 5 * 20 * 0.85
        c = self._cycle(house, qty_planted=round(area * 7), plants_per_sqm=7,
                        beds=[{"bed": b} for b in beds])
        self.assertGreater(c.implied_plants, 0)

    # -- bed range ------------------------------------------------------

    def test_range_fills_fifty_beds_from_one_field(self):
        """Nobody types 50 child rows."""
        house = make_warehouse("TEST GH RANGE")
        for i in range(1, 51):
            self._bed(house, i, 20, 0.85)
        area = 50 * 20 * 0.85
        c = self._cycle(house, bed_range="1-50", plants_per_sqm=7,
                        qty_planted=round(area * 7))
        self.assertEqual(len(c.beds), 50)
        self.assertAlmostEqual(c.planted_area, area, places=2)

    def test_split_block_range(self):
        house = make_warehouse("TEST GH SPLIT")
        for i in range(1, 41):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-20, 31-40", plants_per_sqm=7,
                        qty_planted=round(30 * 17 * 7))
        self.assertEqual(len(c.beds), 30)

    def test_malformed_range_throws_a_readable_message(self):
        with self.assertRaises(frappe.ValidationError):
            parse_bed_range("abc-5")

    def test_range_rejects_beds_that_do_not_exist(self):
        house = make_warehouse("TEST GH MISSING")
        for i in range(1, 6):
            self._bed(house, i, 20, 0.85)
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, bed_range="1-10", plants_per_sqm=7, qty_planted=595)

    def test_second_cycle_cannot_claim_a_bed_already_planted(self):
        house = make_warehouse("TEST GH BEDCLAIM")
        for i in range(1, 11):
            self._bed(house, i, 20, 0.85)
        self._cycle(house, bed_range="1-5", plants_per_sqm=7, qty_planted=round(5 * 17 * 7))
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, bed_range="5-10", plants_per_sqm=7, qty_planted=round(6 * 17 * 7),
                       variety=self._item("TEST-OTHER-VARIETY"))

    def test_a_bed_freed_by_ending_the_cycle_can_be_replanted(self):
        house = make_warehouse("TEST GH BEDFREED")
        for i in range(1, 6):
            self._bed(house, i, 20, 0.85)
        old = self._cycle(house, bed_range="1-5", plants_per_sqm=7,
                          qty_planted=round(5 * 17 * 7),
                          cycle_end_date=datetime.date(2026, 1, 1))
        # old is Ended -- its beds no longer block a fresh cycle over the same ground.
        new = self._cycle(house, bed_range="1-5", plants_per_sqm=7,
                          qty_planted=round(5 * 17 * 7),
                          variety=self._item("TEST-OTHER-VARIETY-2"), replaces=old.name)
        self.assertEqual(len(new.beds), 5)

    def test_a_bed_partially_uprooted_can_be_claimed_without_ending_the_old_cycle(self):
        """A bed logged as removed on Crop Cycle Uproot is free even though
        the old (still-Active) cycle keeps listing it in its own beds table
        -- that table is the original planting, kept intact for history."""
        house = make_warehouse("TEST GH PARTIALFREE")
        for i in range(1, 6):
            self._bed(house, i, 20, 0.85)
        old = self._cycle(house, bed_range="1-5", plants_per_sqm=7,
                          qty_planted=round(5 * 17 * 7))
        old.append("uproot_log", {
            "uproot_date": datetime.date(2026, 3, 1), "bed_range": "1-2", "plants": 238,
        })
        old.save(ignore_permissions=True)
        self.assertEqual(old.status, "Active", "3 of 5 beds still stand -- this cycle isn't over")

        new = self._cycle(house, bed_range="1-2", plants_per_sqm=7, qty_planted=238,
                          variety=self._item("TEST-OTHER-VARIETY-3"), replaces=old.name)
        self.assertEqual(len(new.beds), 2)

    def test_editing_the_range_replaces_the_table(self):
        house = make_warehouse("TEST GH REFILL")
        for i in range(1, 21):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-20", plants_per_sqm=7,
                        qty_planted=round(20 * 17 * 7))
        self.assertEqual(len(c.beds), 20)
        c.bed_range = "1-10"
        c.qty_planted = round(10 * 17 * 7)
        c.save(ignore_permissions=True)
        self.assertEqual(len(c.beds), 10)

    def test_no_beds_means_no_density_check(self):
        c = self._cycle(make_warehouse("TEST GH NOBEDS"), qty_planted=10000)
        self.assertEqual(c.planted_area, 0)

    def test_decimal_range_gives_a_partial_last_bed(self):
        numbers, partial = parse_bed_range("1-3.5")
        self.assertEqual(numbers, [1, 2, 3])
        self.assertEqual(partial, {3: 0.5})

    def test_partial_bed_fraction_shrinks_its_area(self):
        house = make_warehouse("TEST GH PARTIAL")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3.5", plants_per_sqm=7,
                        qty_planted=round((2 * 17 + 0.5 * 17) * 7))
        self.assertEqual(len(c.beds), 3)
        self.assertAlmostEqual(c.beds[2].fraction_planted, 0.5, places=4)
        self.assertAlmostEqual(c.beds[2].bed_area, 20 * 0.85 * 0.5, places=2)

    def test_manual_plant_count_on_a_bed_survives_a_resave(self):
        """A grower's typed count for an odd partial bed must not be wiped by
        the next save just because bed_range gets re-parsed every time."""
        house = make_warehouse("TEST GH MANUALPLANTS")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3.5", plants_per_sqm=7,
                        qty_planted=round((2 * 17 + 0.5 * 17) * 7))
        c.beds[2].plants = 123
        c.qty_planted = 2 * 17 * 7 + 123  # keep check_density happy about the new total
        c.save(ignore_permissions=True)
        self.assertEqual(c.beds[2].plants, 123)

        c.reload()
        c.save(ignore_permissions=True)  # bed_range is unchanged but still re-parsed
        self.assertEqual(c.beds[2].plants, 123,
                         "typed plant count must survive a resave, not silently revert")

    # -- greenhouse capacity ---------------------------------------------

    def test_greenhouse_capacity_is_enforced(self):
        """Two cycles that together outgrow the greenhouse: the second is refused."""
        house = make_warehouse("TEST GH CAP")
        frappe.get_doc({
            "doctype": "Greenhouse", "greenhouse": house, "company": default_company(),
            "gross_area": 15,
        }).insert(ignore_permissions=True)
        self._bed(house, 1, 10, 1)
        self._bed(house, 2, 10, 1)
        self._cycle(house, bed_range="1", plants_per_sqm=7, qty_planted=70)
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, bed_range="2", plants_per_sqm=7, qty_planted=70)

    # -- uprooting log -----------------------------------------------------

    def test_uproot_log_refuses_more_plants_than_standing(self):
        house = make_warehouse("TEST GH UPROOTLOG")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                        qty_planted=round(3 * 20 * 0.85 * 7))
        c.append("uproot_log", {
            "uproot_date": datetime.date(2026, 6, 1), "bed_range": "1-3", "plants": 999999,
        })
        with self.assertRaises(frappe.ValidationError):
            c.save(ignore_permissions=True)

    def test_uproot_log_refuses_a_bed_already_uprooted(self):
        house = make_warehouse("TEST GH DOUBLEUPROOT")
        for i in (1, 2):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-2", plants_per_sqm=7,
                        qty_planted=round(2 * 20 * 0.85 * 7))
        c.append("uproot_log", {
            "uproot_date": datetime.date(2026, 6, 1), "bed_range": "1", "plants": 119,
        })
        c.save(ignore_permissions=True)

        c.append("uproot_log", {
            "uproot_date": datetime.date(2026, 6, 8), "bed_range": "1-2", "plants": 119,
        })
        with self.assertRaises(frappe.ValidationError):
            c.save(ignore_permissions=True)

    def test_uprooted_beds_show_status_on_the_table_itself(self):
        house = make_warehouse("TEST GH BEDSTATUS")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                        qty_planted=round(3 * 20 * 0.85 * 7))
        c.append("uproot_log", {
            "uproot_date": datetime.date(2026, 6, 1), "bed_range": "1-2", "plants": 238,
        })
        c.save(ignore_permissions=True)

        by_bed = {frappe.db.get_value("Bed", r.bed, "bed"): r.status for r in c.beds}
        self.assertEqual(by_bed[1], "Uprooted")
        self.assertEqual(by_bed[2], "Uprooted")
        self.assertEqual(by_bed[3], "Standing", "bed 3 was never logged as removed")

    def test_removing_the_uproot_log_row_puts_the_bed_back_to_standing(self):
        house = make_warehouse("TEST GH BEDSTATUS2")
        self._bed(house, 1, 20, 0.85)
        c = self._cycle(house, bed_range="1", plants_per_sqm=7,
                        qty_planted=round(20 * 0.85 * 7))
        c.append("uproot_log", {
            "uproot_date": datetime.date(2026, 6, 1), "bed_range": "1", "plants": 119,
        })
        c.save(ignore_permissions=True)
        self.assertEqual(c.beds[0].status, "Uprooted")

        c.uproot_log = []
        c.save(ignore_permissions=True)
        self.assertEqual(c.beds[0].status, "Standing",
                         "status is recomputed fresh every save, not left stuck from before")

    # -- source & cost ---------------------------------------------------

    def _supplier(self, label="TEST BREEDER"):
        """Supplier is named by series here, so look it up by supplier_name."""
        existing = frappe.db.get_value("Supplier", {"supplier_name": label}, "name")
        if existing:
            return existing
        group = frappe.db.get_value("Supplier Group", {}, "name")
        if not group:
            group = frappe.get_doc({
                "doctype": "Supplier Group", "supplier_group_name": "All Supplier Groups",
            }).insert(ignore_permissions=True).name
        return frappe.get_doc({
            "doctype": "Supplier", "supplier_name": label, "supplier_group": group,
        }).insert(ignore_permissions=True).name

    def _invoice(self, item, qty, rate, supplier=None):
        supplier = supplier or self._supplier()
        pi = frappe.get_doc({
            "doctype": "Purchase Invoice", "supplier": supplier,
            "company": default_company(), "update_stock": 0,
            "items": [{"item_code": item, "qty": qty, "rate": rate}],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        pi.submit()
        return pi

    def test_invoice_fills_breeder_and_unit_cost(self):
        house = make_warehouse("TEST GH INVOICE")
        pi = self._invoice(self._item(), qty=1000, rate=12.5)
        c = self._cycle(house, qty_planted=1000, purchase_invoice=pi.name)
        self.assertEqual(c.breeder, pi.supplier)
        self.assertEqual(c.cost_per_plant, 12.5)
        self.assertEqual(c.invoiced_qty, 1000)
        self.assertEqual(c.seedling_source, "Purchased from Breeder")

    def test_typed_cost_survives_without_an_invoice(self):
        house = make_warehouse("TEST GH TYPED")
        c = self._cycle(house, cost_per_plant=9.75, breeder=None,
                        seedling_source="In-house Propagation")
        self.assertEqual(c.cost_per_plant, 9.75)
        self.assertEqual(c.seedling_source, "In-house Propagation")
        self.assertEqual(c.invoiced_qty, 0)

    def test_unit_cost_is_amount_over_qty_not_the_rate_field(self):
        """A discounted or multi-line invoice must still divide correctly."""
        house = make_warehouse("TEST GH RATE")
        pi = self._invoice(self._item(), qty=800, rate=10)
        c = self._cycle(house, qty_planted=800, purchase_invoice=pi.name)
        self.assertAlmostEqual(c.cost_per_plant, pi.items[0].amount / 800, places=4)

    def test_draft_invoice_is_refused(self):
        house = make_warehouse("TEST GH DRAFT")
        pi = frappe.get_doc({
            "doctype": "Purchase Invoice", "supplier": self._supplier(),
            "company": default_company(),
            "items": [{"item_code": self._item(), "qty": 10, "rate": 1}],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, purchase_invoice=pi.name)

    # -- milestones -----------------------------------------------------

    def test_milestone_todos_created_for_supervisor(self):
        house = make_warehouse("TEST GH TODOS", supervisor="Administrator")
        c = self._cycle(house)
        todos = frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle", "reference_name": c.name})
        self.assertEqual(len(todos), 3)  # two bendings + uproot

    def test_on_trash_removes_todos(self):
        house = make_warehouse("TEST GH TRASH", supervisor="Administrator")
        c = self._cycle(house)
        c.delete(ignore_permissions=True)
        self.assertFalse(frappe.db.get_all("ToDo", filters={
            "reference_type": "Crop Cycle", "reference_name": c.name}))


class TestGreenhouse(TestCropCycle):
    """Greenhouse is its own bed-by-bed ledger, independent of Crop Cycle --
    only reuses TestCropCycle's _item() to create variety Items."""

    def _greenhouse(self, house, **kw):
        payload = {"doctype": "Greenhouse", "greenhouse": house, "company": default_company()}
        payload.update(kw)
        return frappe.get_doc(payload).insert(ignore_permissions=True)

    def test_bed_range_expands_into_individual_beds(self):
        house = make_warehouse("TEST GH BEDEXPAND")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 3, "variety": self._item(),
            "bed_length": 2, "bed_width": 4, "planting_date": datetime.date(2026, 1, 1),
        }])
        self.assertEqual([b.bed_number for b in gh.individual_beds], [1, 2, 3])
        self.assertEqual(gh.individual_beds[0].area_m2, 8)
        self.assertEqual(gh.bed_range[0].total_beds_area, 24)

    def test_bed_master_mirrors_status_variety_and_plant_count(self):
        house = make_warehouse("TEST GH BEDMASTER")
        variety = self._item()
        for i in (1, 2):
            self._bed(house, i, 20, 0.85)
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 2, "variety": variety,
            "bed_length": 20, "bed_width": 0.85,
            "planting_date": datetime.date(2026, 1, 1),
        }])
        for b in gh.individual_beds:
            b.plant_count = 119
        gh.save(ignore_permissions=True)

        bed1 = frappe.db.get_value(
            "Bed", {"greenhouse": house, "bed": 1},
            ["status", "variety", "plant_count"], as_dict=True)
        self.assertEqual(bed1.status, "Planted")
        self.assertEqual(bed1.variety, variety)
        self.assertEqual(bed1.plant_count, 119)

        # Uprooting clears the ledger's OCCUPIED status -- variety and plant
        # count should clear on the Bed master right along with it.
        gh.individual_beds[0].status = "Uprooted"
        gh.individual_beds[0].plant_count = 0
        gh.save(ignore_permissions=True)

        bed1 = frappe.db.get_value(
            "Bed", {"greenhouse": house, "bed": 1},
            ["status", "variety", "plant_count"], as_dict=True)
        self.assertEqual(bed1.status, "Uprooted")
        self.assertFalse(bed1.variety)
        self.assertEqual(bed1.plant_count, 0)

    def test_replanting_a_bed_does_not_touch_its_neighbour(self):
        house = make_warehouse("TEST GH REPLANTLEDGER")
        v1, v2 = self._item("TEST-V1"), self._item("TEST-V2")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 2, "variety": v1, "bed_length": 2, "bed_width": 4,
        }])
        for b in gh.individual_beds:
            b.plant_count = 32
        gh.save(ignore_permissions=True)

        # A replant needs its bed already uprooted; logging both on the same
        # date does both at once (uproot sorts first on a tie).
        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 3, 1), "from_bed": 1, "to_bed": 1,
            "reason": "Variety Change", "qty_uprooted": 32,
        })
        gh.append("replanting_logs", {
            "replant_date": datetime.date(2026, 3, 1), "from_bed": 1, "to_bed": 1,
            "qty_replanted": 32, "new_variety": v2,
        })
        gh.save(ignore_permissions=True)
        gh.reload()

        by_number = {b.bed_number: b for b in gh.individual_beds}
        self.assertEqual(by_number[1].variety, v2)
        self.assertEqual(by_number[2].variety, v1,
                          "a log aimed at bed 1 must not touch bed 2")

    def test_replanting_log_refuses_more_than_standing(self):
        house = make_warehouse("TEST GH REPLANTOVER")
        v1, v2 = self._item("TEST-V3"), self._item("TEST-V4")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 1, "variety": v1, "bed_length": 2, "bed_width": 4,
        }])
        gh.individual_beds[0].plant_count = 32
        gh.append("replanting_logs", {
            "replant_date": datetime.date(2026, 3, 1), "from_bed": 1, "to_bed": 1,
            "qty_replanted": 999, "new_variety": v2,
        })
        with self.assertRaises(frappe.ValidationError):
            gh.save(ignore_permissions=True)

    def test_uprooting_log_marks_the_bed_uprooted_and_out_of_the_rollup(self):
        house = make_warehouse("TEST GH UPROOTLEDGER")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 2, "variety": self._item(), "bed_length": 2, "bed_width": 4,
        }])
        for b in gh.individual_beds:
            b.plant_count = 32
        gh.save(ignore_permissions=True)

        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 4, 1), "from_bed": 1, "to_bed": 1,
            "reason": "Disease", "qty_uprooted": 32,
        })
        gh.save(ignore_permissions=True)
        gh.reload()

        by_number = {b.bed_number: b for b in gh.individual_beds}
        self.assertEqual(by_number[1].status, "Uprooted")
        self.assertEqual(by_number[2].status, "Planted")
        self.assertEqual(gh.number_of_beds, 1, "the uprooted bed drops out of the rollup")
        self.assertEqual(gh.number_of_plants, 32)

    def test_resaving_does_not_replay_an_already_applied_log_row(self):
        """The exact live bug: an uproot+replant pair replanted a bed with a
        higher-density variety; ANY later, unrelated save must not
        re-validate that old uproot row against the new (already-changed)
        plant count and wrongly refuse it."""
        house = make_warehouse("TEST GH NOREPLAY")
        v1, v2 = self._item("TEST-NOREPLAY-1"), self._item("TEST-NOREPLAY-2")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 1, "variety": v1, "bed_length": 2, "bed_width": 4,
        }])
        gh.individual_beds[0].plant_count = 32
        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 1,
            "reason": "Variety Change", "qty_uprooted": 32,
        })
        gh.append("replanting_logs", {
            "replant_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 1,
            "qty_replanted": 10, "new_variety": v2,  # far fewer plants than before
        })
        gh.save(ignore_permissions=True)
        self.assertEqual(gh.individual_beds[0].plant_count, 10)

        # An unrelated resave must not re-check the old uproot (32) against
        # the bed's new count (10) and throw "only 10 are standing".
        gh.gross_area = 999
        gh.save(ignore_permissions=True)
        self.assertEqual(gh.individual_beds[0].variety, v2)
        self.assertEqual(gh.individual_beds[0].plant_count, 10)

    def test_rollup_sums_area_and_plants_by_variety(self):
        house = make_warehouse("TEST GH ROLLUP")
        v = self._item("TEST-V5")
        gh = self._greenhouse(house, bed_range=[{
            "from_bed": 1, "to_bed": 2, "variety": v, "bed_length": 2, "bed_width": 4,
        }])
        for b in gh.individual_beds:
            b.plant_count = 32
        gh.save(ignore_permissions=True)

        self.assertEqual(gh.varieties, 1)
        self.assertEqual(gh.area_planted, 16)
        self.assertEqual(gh.number_of_plants, 64)
        self.assertEqual(gh.plants_per_sqm, 4)
        self.assertEqual(gh.varieties_grown[0].variety, v)
        self.assertEqual(gh.varieties_grown[0].beds, 2)

    def test_over_capacity_is_refused(self):
        house = make_warehouse("TEST GH BEDCAP")
        gh = frappe.get_doc({
            "doctype": "Greenhouse", "greenhouse": house, "company": default_company(),
            "gross_area": 10,
            "bed_range": [{"from_bed": 1, "to_bed": 3, "variety": self._item(),
                          "bed_length": 2, "bed_width": 4}],  # 24 m2 of beds, 10 m2 cap
        })
        with self.assertRaises(frappe.ValidationError):
            gh.insert(ignore_permissions=True)

    def test_prefill_reads_the_bed_span_of_existing_crop_cycles(self):
        from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
            bed_ranges_from_crop_cycles,
        )
        house = make_warehouse("TEST GH PREFILL")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                    qty_planted=round(3 * 20 * 0.85 * 7))

        rows = bed_ranges_from_crop_cycles(house)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["from_bed"], rows[0]["to_bed"]), (1, 3))
        self.assertEqual(rows[0]["variety"], self._item())

    def test_prefill_splits_a_gapped_cycle_into_separate_runs(self):
        """A cycle on beds '1-3, 7-8' must prefill as two rows, not one
        1-8 span that would wrongly claim beds 4-6 too."""
        from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
            bed_ranges_from_crop_cycles,
        )
        house = make_warehouse("TEST GH PREFILLGAP")
        for i in range(1, 9):
            self._bed(house, i, 20, 0.85)
        self._cycle(house, bed_range="1-3, 7-8", plants_per_sqm=7,
                    qty_planted=round(5 * 17 * 7))

        rows = sorted(bed_ranges_from_crop_cycles(house), key=lambda r: r["from_bed"])
        self.assertEqual([(r["from_bed"], r["to_bed"]) for r in rows], [(1, 3), (7, 8)])

    def test_prefill_skips_ended_cycles(self):
        from upande_agriculture.upande_agriculture.doctype.greenhouse.greenhouse import (
            bed_ranges_from_crop_cycles,
        )
        house = make_warehouse("TEST GH PREFILLENDED")
        self._bed(house, 1, 20, 0.85)
        self._cycle(house, bed_range="1", plants_per_sqm=7,
                    qty_planted=round(20 * 0.85 * 7),
                    cycle_end_date=datetime.date(2025, 6, 1))
        self.assertEqual(bed_ranges_from_crop_cycles(house), [])

    # -- sync from Crop Cycle ---------------------------------------------

    def test_saving_a_crop_cycle_creates_its_greenhouse_ledger(self):
        house = make_warehouse("TEST GH AUTOSYNC")
        for i in (1, 2):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-2", plants_per_sqm=7,
                        qty_planted=round(2 * 17 * 7))

        gh = frappe.get_doc("Greenhouse", house)
        rows = [r for r in gh.bed_range if r.crop_cycle == c.name]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0].from_bed, rows[0].to_bed), (1, 2))
        self.assertEqual(rows[0].variety, c.variety)

    def test_resaving_the_crop_cycle_updates_its_own_row_not_a_duplicate(self):
        house = make_warehouse("TEST GH AUTOSYNC2")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-2", plants_per_sqm=7,
                        qty_planted=round(2 * 17 * 7))
        c.bed_range = "1-3"
        c.qty_planted = round(3 * 17 * 7)
        c.save(ignore_permissions=True)

        gh = frappe.get_doc("Greenhouse", house)
        rows = [r for r in gh.bed_range if r.crop_cycle == c.name]
        self.assertEqual(len(rows), 1, "re-saving must update the tagged row, not add another")
        self.assertEqual((rows[0].from_bed, rows[0].to_bed), (1, 3))

    def test_resaving_a_partially_uprooted_cycle_does_not_reclaim_its_freed_beds(self):
        """The exact live bug: beds 1-2 of a 1-3 cycle get replanted as
        something else; later, ANY resave of the original (still-Active,
        beds 4-3 standing... i.e. bed 3) cycle must not push its full
        original 1-3 span back onto the Greenhouse and collide with bed 1-2's
        new occupant."""
        house = make_warehouse("TEST GH RESYNCFREED")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        old = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                          qty_planted=round(3 * 17 * 7))
        old.append("uproot_log", {
            "uproot_date": datetime.date(2026, 3, 1), "bed_range": "1-2", "plants": 238,
        })
        old.save(ignore_permissions=True)

        new_variety = self._item("TEST-RESYNC-VARIETY")
        new = self._cycle(house, bed_range="1-2", plants_per_sqm=7, qty_planted=238,
                          variety=new_variety, replaces=old.name)

        # Now resave the OLD cycle again (e.g. an unrelated field edit) --
        # this must not try to reclaim beds 1-2 for the old variety.
        old.notes = "unrelated edit"
        old.save(ignore_permissions=True)

        gh = frappe.get_doc("Greenhouse", house)
        old_rows = [r for r in gh.bed_range if r.crop_cycle == old.name]
        self.assertEqual([(r.from_bed, r.to_bed) for r in old_rows], [(3, 3)],
                         "the old cycle's synced range must exclude the beds it uprooted")

    # -- reverse sync: Greenhouse logs reaching back onto Crop Cycle ------

    def test_uproot_logged_on_greenhouse_updates_the_owning_crop_cycle(self):
        house = make_warehouse("TEST GH REVSYNC")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                        qty_planted=round(3 * 17 * 7))

        gh = frappe.get_doc("Greenhouse", house)
        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 3,
            "reason": "Age (End of Life)", "qty_uprooted": round(3 * 17 * 7),
        })
        gh.save(ignore_permissions=True)

        c.reload()
        self.assertEqual(c.status, "Ended",
                         "losing every plant standing should end the cycle")
        self.assertEqual(len(c.uproot_log), 1)

        gh.reload()
        self.assertEqual(gh.uprooting_logs[0].variety, c.variety,
                         "the log should record what was actually standing on the beds")

    def test_untagged_uproot_retries_once_the_bed_range_gets_linked_later(self):
        """The exact live bug: a Bed Range with no crop_cycle tag (typed by
        hand, or predating the sync feature) means an uproot logged against
        it has no owner to reach -- it must NOT be marked synced and
        forgotten; once something tags that range, the same row should sync
        on the very next save."""
        house = make_warehouse("TEST GH LATETAG")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        c = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                        qty_planted=round(3 * 17 * 7))

        gh = frappe.get_doc("Greenhouse", house)
        gh.bed_range = [r for r in gh.bed_range if r.crop_cycle != c.name]  # simulate no tag yet
        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 3,
            "reason": "Age (End of Life)", "qty_uprooted": round(3 * 17 * 7),
        })
        gh.save(ignore_permissions=True)
        row_name = gh.uprooting_logs[0].name
        self.assertFalse(frappe.db.get_value("Greenhouse Uprooting Log", row_name, "synced"),
                         "nothing to sync to yet -- must not be marked done")

        c.reload()
        self.assertEqual(len(c.uproot_log), 0, "no tag existed yet, so nothing should have synced")

        # The tag shows up later (e.g. the cycle gets resaved).
        c.save(ignore_permissions=True)
        gh.reload()
        gh.save(ignore_permissions=True)  # re-trigger the sync now that a tag exists

        self.assertTrue(frappe.db.get_value("Greenhouse Uprooting Log", row_name, "synced"))
        c.reload()
        self.assertEqual(len(c.uproot_log), 1)
        self.assertEqual(c.uproot_log[0].plants, round(3 * 17 * 7))

    def test_replant_and_uproot_logged_together_does_not_hang_or_error(self):
        """The exact live scenario: an uproot and a replant on the same date,
        same beds, saved together -- must not recurse between the two sync
        directions (Crop Cycle <-> Greenhouse)."""
        house = make_warehouse("TEST GH REVSYNC2")
        for i in (1, 2, 3):
            self._bed(house, i, 20, 0.85)
        old = self._cycle(house, bed_range="1-3", plants_per_sqm=7,
                          qty_planted=round(3 * 17 * 7))
        new_variety = self._item("TEST-REPLANT-VARIETY")

        gh = frappe.get_doc("Greenhouse", house)
        gh.append("uprooting_logs", {
            "uproot_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 3,
            "reason": "Variety Change", "qty_uprooted": round(3 * 17 * 7),
        })
        gh.append("replanting_logs", {
            "replant_date": datetime.date(2026, 5, 1), "from_bed": 1, "to_bed": 3,
            "qty_replanted": 1200, "new_variety": new_variety,
        })
        gh.save(ignore_permissions=True)

        old.reload()
        self.assertEqual(old.status, "Ended")
        new_cycles = frappe.get_all("Crop Cycle", filters={
            "greenhouse": house, "variety": new_variety,
        }, fields=["name", "replaces", "qty_planted"])
        self.assertEqual(len(new_cycles), 1,
                         "the replant must create exactly one new Crop Cycle, not zero or several")
        self.assertEqual(new_cycles[0].replaces, old.name)
        self.assertEqual(new_cycles[0].qty_planted, 1200)


class TestPlanForm(FrappeTestCase):
    def test_plan_form_creates_todos_from_tasks(self):
        gh = make_warehouse("TEST GH PLAN")
        emp = default_employee()
        plan = frappe.get_doc({
            "doctype": "Production Plan Form",
            "company": default_company(), "greenhouse": gh,
            "plan_year": 2026, "plan_week": 27, "plan_period": "2026-W27",
            "tasks": [
                {"task_name": "Bend blind shoots", "due_date": datetime.date(2026, 6, 30),
                 "assigned_to": emp, "status": "Open"},
                {"task_name": "Spray fungicide", "due_date": datetime.date(2026, 7, 3),
                 "assigned_to": emp, "status": "Open"},
            ],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        todos = frappe.db.get_all("ToDo", filters={
            "reference_type": "Production Plan Form", "reference_name": plan.name})
        self.assertGreaterEqual(len(todos), 2)

    def test_task_on_a_bed_the_house_does_not_have_is_refused(self):
        gh = make_warehouse("TEST GH PLAN BEDS")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({
                "doctype": "Production Plan Form",
                "company": default_company(), "greenhouse": gh,
                "plan_year": 2026, "plan_week": 27, "plan_period": "2026-W27",
                "tasks": [
                    {"task_name": "Harvest bed 3", "operation": "Harvest",
                     "greenhouse": gh, "beds": "3", "status": "Open"},
                ],
            }).insert(ignore_permissions=True, ignore_mandatory=True)
