import datetime

import frappe
from frappe.tests.utils import FrappeTestCase

from upande_agriculture.tests import default_company, default_uom, make_warehouse


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

    def test_range_rejects_beds_that_do_not_exist(self):
        house = make_warehouse("TEST GH MISSING")
        for i in range(1, 6):
            self._bed(house, i, 20, 0.85)
        with self.assertRaises(frappe.ValidationError):
            self._cycle(house, bed_range="1-10", plants_per_sqm=7, qty_planted=595)

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


class TestPlanForm(FrappeTestCase):
    def test_plan_form_creates_todos_from_tasks(self):
        gh = make_warehouse("TEST GH PLAN")
        plan = frappe.get_doc({
            "doctype": "Production Plan Form",
            "company": default_company(), "greenhouse": gh,
            "plan_year": 2026, "plan_week": 27, "plan_period": "2026-W27",
            "tasks": [
                {"task_name": "Bend blind shoots", "due_day": "Tuesday",
                 "assigned_to": "Administrator", "status": "Open"},
                {"task_name": "Spray fungicide", "due_day": "Friday",
                 "assigned_to": "Administrator", "status": "Open"},
            ],
        }).insert(ignore_permissions=True, ignore_mandatory=True)
        todos = frappe.db.get_all("ToDo", filters={
            "reference_type": "Production Plan Form", "reference_name": plan.name})
        self.assertGreaterEqual(len(todos), 2)
