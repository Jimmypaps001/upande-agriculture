import datetime

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate


# How far apart area-implied plants and the entered count may be before we
# refuse the record. Wide enough for gapping-up and edge beds, tight enough to
# catch a density that is out by an order of magnitude.
DENSITY_TOLERANCE = 0.20

# Absolute sanity band for planting density, wide enough to cover every cut
# flower crop (roses sit at 6-8) but tight enough that a bed measured in the
# wrong unit cannot pass. Anything outside is a data-entry error.
MIN_DENSITY = 0.5
MAX_DENSITY = 30.0


def parse_bed_range(spec: str | None) -> tuple[list[int], dict[int, float]]:
    """'1-50' or '1-20, 31-40, 45' -> a sorted list of bed numbers, plus any
    partial-bed fractions.

    Nobody is typing fifty child rows by hand, so the range is what the grower
    actually enters and the table is derived from it. A decimal in a chunk's
    upper bound (or a bare decimal on its own) means that one bed is only
    partly there: '1-22.5' is beds 1-21 whole and bed 22 at fraction 0.5.
    """
    if not spec:
        return [], {}
    numbers: set[int] = set()
    partial: dict[int, float] = {}
    # en/em dashes creep in from pasted spreadsheets
    for chunk in spec.replace("\u2013", "-").replace("\u2014", "-").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            # Not "_" -- that shadows frappe's gettext alias for the rest of
            # this function, breaking the _("Cannot read...") throw below.
            lo, _sep, hi = chunk.partition("-")
            try:
                lo, hi_val = int(lo.strip()), float(hi.strip())
            except ValueError:
                frappe.throw(_("Cannot read bed range {0}.").format(chunk))
            hi = int(hi_val)
            if lo > hi:
                lo, hi = hi, lo
            if hi - lo > 1000:
                frappe.throw(_("Bed range {0} is too wide.").format(chunk))
            numbers.update(range(lo, hi + 1))
            frac = round(hi_val - int(hi_val), 4)
            if frac:
                partial[hi] = frac
        else:
            try:
                val = float(chunk)
            except ValueError:
                frappe.throw(_("Cannot read bed number {0}.").format(chunk))
            n = int(val)
            numbers.add(n)
            frac = round(val - n, 4)
            if frac:
                partial[n] = frac
    return sorted(numbers), partial


def uprooted_bed_numbers(cycle_name: str) -> set[int]:
    """Bed numbers already logged as removed from this cycle via its own
    Uproot Log.

    `beds` is the cycle's full original planting and never shrinks -- this
    is the correction factor everywhere something needs the cycle's CURRENT
    footprint instead (a cross-cycle conflict check, or a sync that pushes
    this cycle's beds onto the Greenhouse ledger). Skipping it anywhere one
    of those reads `beds` directly re-claims ground that's since moved on.
    """
    rows = frappe.get_all("Crop Cycle Uproot", filters={"parent": cycle_name}, fields=["bed_range"])
    beds: set[int] = set()
    for r in rows:
        nums, _partial_frac = parse_bed_range(r.bed_range)
        beds.update(nums)
    return beds


def _compact(numbers: list[int]) -> str:
    """[1,2,3,7] -> '1-3, 7' so a long gap list stays readable."""
    out, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:] + [None]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    return ", ".join(out)


class CropCycle(Document):
    def validate(self):
        self.set_title()
        self.sync_beds_from_range()
        self.check_bed_conflicts()
        self.roll_up_beds()
        self.check_uproot_log()
        self.derive_bending_dates()
        self.pull_from_invoice()
        self.check_density()
        self.check_greenhouse_capacity()
        self.check_lifecycle()

    def set_title(self):
        self.title = f"{self.variety} @ {self.greenhouse}"

    def sync_beds_from_range(self):
        """Rebuild the beds table from the typed range.

        Left alone when no range is given, so a hand-picked set of beds is
        still possible for odd layouts.
        """
        wanted, partial = parse_bed_range(self.bed_range)
        if not wanted:
            return
        if not self.greenhouse:
            frappe.throw(_("Set the Greenhouse before entering a bed range."))

        found = {
            int(b.bed): b
            for b in frappe.get_all(
                "Bed",
                filters={"greenhouse": self.greenhouse, "bed": ("in", wanted)},
                fields=["name", "bed", "bed_length", "bed_width", "bed_area"],
            )
        }
        missing = [n for n in wanted if n not in found]
        if missing:
            frappe.throw(
                _("{0} has no Bed record for: {1}.<br><br>"
                  "Create the beds first — a budget built on beds that don't "
                  "exist would be measuring nothing.").format(
                    self.greenhouse, _compact(missing)),
                title=_("Beds not found"),
            )

        # A grower's own tally on a row (the way to record an odd partial bed)
        # must survive a re-typed range, or every save quietly throws it away
        # and roll_up_beds() re-derives a guess in its place.
        existing_plants = {row.bed: row.plants for row in (self.beds or []) if row.bed and row.plants}

        self.beds = []
        for n in wanted:
            b = found[n]
            # fetch_from does not fire for rows appended during validate, so
            # the dimensions are carried over explicitly.
            self.append("beds", {
                "bed": b.name,
                "bed_length": b.bed_length,
                "bed_width": b.bed_width,
                "bed_area": b.bed_area,
                "fraction_planted": partial.get(n, 1.0),
                "plants": existing_plants.get(b.name),
            })

    def check_bed_conflicts(self):
        """Two varieties can't grow on the same ground.

        A bed already standing under another active Crop Cycle in this same
        greenhouse must be uprooted (or replanted, via a proper Replant)
        before a different cycle can claim it — not silently double-booked.

        A cycle's own `beds` table is its full original planting and never
        shrinks — a partial uproot is recorded on `uproot_log` instead, so
        the history stays intact. That means a bed logged there as removed
        still shows up in the OTHER cycle's `beds` table, and must not read
        as a conflict here: it's free, just not un-listed. Just as much: a
        bed THIS cycle has already given up (its own uproot_log) isn't
        something it needs conflict protection for either, even though
        bed_range/beds here still names it for the same historical reason.
        """
        wanted, _partial = parse_bed_range(self.bed_range)
        wanted = [n for n in wanted if n not in uprooted_bed_numbers(self.name or "")]
        if not wanted or not self.greenhouse:
            return

        conflicts = frappe.db.sql(
            """
            SELECT cc.name, cc.variety, b.bed
            FROM `tabCrop Cycle Bed` ccb
            JOIN `tabBed` b ON b.name = ccb.bed
            JOIN `tabCrop Cycle` cc ON cc.name = ccb.parent
            WHERE cc.greenhouse = %(greenhouse)s
              AND cc.status != 'Ended'
              AND cc.name != %(self)s
              AND b.bed IN %(wanted)s
            ORDER BY b.bed
            """,
            {"greenhouse": self.greenhouse, "self": self.name or "", "wanted": wanted},
            as_dict=True,
        )
        if not conflicts:
            return

        uprooted_by_cycle = {
            cycle_name: uprooted_bed_numbers(cycle_name)
            for cycle_name in {row.name for row in conflicts}
        }
        real = [row for row in conflicts if row.bed not in uprooted_by_cycle.get(row.name, set())]
        if not real:
            return
        c = real[0]
        frappe.throw(
            _("Bed {0} already belongs to {1} ({2}) in {3}.<br><br>"
              "Uproot or replant it there first — a bed can't carry two "
              "varieties at once.").format(
                c.bed, c.name, c.variety, self.greenhouse),
            title=_("Bed already occupied"),
        )

    def roll_up_beds(self):
        """Turn each bed row into an area and a plant count.

        A grower's own tally on a row beats the fraction/density guess (real
        counts happen for odd partial beds); either way every row ends up
        with both, because the uproot log checks removed plants against them.
        """
        bed_names = [row.bed for row in (self.beds or []) if row.bed]
        full_areas = {}
        if bed_names:
            full_areas = {
                b.name: float(b.bed_area or 0)
                for b in frappe.get_all(
                    "Bed", filters={"name": ("in", bed_names)}, fields=["name", "bed_area"]
                )
            }

        density = float(self.plants_per_sqm or 0)
        for row in (self.beds or []):
            full_area = full_areas.get(row.bed, float(row.bed_area or 0))
            if row.plants:
                row.bed_area = round(row.plants / density, 4) if density else full_area
            else:
                row.bed_area = round(full_area * float(row.fraction_planted or 1.0), 4)
                if density:
                    row.plants = int(round(row.bed_area * density))
        self.planted_area = sum(float(b.bed_area or 0) for b in (self.beds or []))

        # Derive whichever of (count, density) is missing. A grower may know
        # either "44,531 plants went in" or "142 beds at 7/m2"; both are valid
        # ways to describe the same planting.
        if self.planted_area and self.qty_planted and not self.plants_per_sqm:
            self.plants_per_sqm = round(self.qty_planted / self.planted_area, 2)
        elif self.planted_area and self.plants_per_sqm and not self.qty_planted:
            self.qty_planted = int(round(self.planted_area * float(self.plants_per_sqm)))

        density = float(self.plants_per_sqm or 0)
        self.implied_plants = int(round(self.planted_area * density))

    def check_uproot_log(self):
        """Every logged uprooting must be plants that are actually still standing.

        plants_standing is what a grower should be shown today; qty_planted
        stays the original investment, untouched, for costing. The `beds`
        table itself stays the original planting for the same reason -- but
        each row's own status is recomputed every save, so looking at the
        table doesn't require cross-checking it against Uproot Log by hand.
        """
        bed_names = [row.bed for row in (self.beds or []) if row.bed]
        numbers = {}
        if bed_names:
            numbers = {
                b.name: int(b.bed)
                for b in frappe.get_all(
                    "Bed", filters={"name": ("in", bed_names)}, fields=["name", "bed"]
                )
            }
        by_number = {numbers[row.bed]: row for row in (self.beds or []) if row.bed in numbers}

        for row in by_number.values():
            row.status = "Standing"

        for u in (self.uproot_log or []):
            wanted, partial = parse_bed_range(u.bed_range)
            if not wanted:
                continue
            missing = [n for n in wanted if n not in by_number]
            if missing:
                frappe.throw(
                    _("{0} isn't among this cycle's beds: {1}.").format(
                        u.bed_range, _compact(missing)),
                    title=_("Bed not in this cycle"),
                )
            u.area_sqm = round(
                sum(float(by_number[n].bed_area or 0) * partial.get(n, 1.0) for n in wanted), 2
            )
            standing = sum(by_number[n].plants or 0 for n in wanted)
            if u.plants and u.plants > standing:
                frappe.throw(
                    _("{0} plants removed but only {1:,} are standing on beds {2}.").format(
                        u.plants, standing, u.bed_range),
                    title=_("More than what's standing"),
                )
            for n in wanted:
                by_number[n].status = "Uprooted"

        self.plants_standing = int(self.qty_planted or 0) - sum(
            int(u.plants or 0) for u in (self.uproot_log or [])
        )

    def derive_bending_dates(self):
        """Fill bending dates from the protocol when the grower hasn't recorded them."""
        if self.crop_protocol and self.planting_date:
            p = frappe.get_cached_doc("Crop Protocol", self.crop_protocol)
            planting = getdate(self.planting_date)
            if not self.first_bending_date:
                self.first_bending_date = planting + datetime.timedelta(
                    weeks=int(p.weeks_to_first_bending or 0)
                )
            if not self.second_bending_date:
                self.second_bending_date = getdate(
                    self.first_bending_date
                ) + datetime.timedelta(weeks=int(p.weeks_to_second_bending or 0))
            if not self.planned_uprooting_date and p.productive_life_weeks:
                self.planned_uprooting_date = planting + datetime.timedelta(
                    weeks=int(p.productive_life_weeks)
                )

        # A recorded actual beats anything derived above — every other
        # consumer (projection_calc.py, the budget grid, the farm map) keeps
        # reading first/second_bending_date and never needs to know which.
        if self.actual_first_bending_date:
            self.first_bending_date = self.actual_first_bending_date
        if self.actual_second_bending_date:
            self.second_bending_date = self.actual_second_bending_date

    def check_lifecycle(self):
        """Uprooting and replanting must leave the record able to stop producing.

        production_end() reads cycle_end_date first, so a block that came out
        of the ground but never got the date keeps making stems in every budget
        built after it. Recording the date is the whole point of ending a cycle.
        """
        if self.cycle_end_date and self.status != "Ended":
            self.status = "Ended"
        if self.status == "Ended" and not self.cycle_end_date:
            frappe.throw(
                _("An ended cycle needs the date it was uprooted.<br><br>"
                  "Production is counted right up to that date — without it "
                  "every budget keeps cutting off a block that is gone."),
                title=_("When was it uprooted?"),
            )

        if not self.replaces:
            return
        if self.replaces == self.name:
            frappe.throw(_("A cycle cannot replace itself."))
        prev = frappe.db.get_value(
            "Crop Cycle", self.replaces,
            ["greenhouse", "cycle_end_date"], as_dict=True,
        )
        if not prev:
            return
        if prev.greenhouse != self.greenhouse:
            frappe.throw(
                _("{0} is in {1}, but this planting is in {2}. "
                  "A replant stays in the same house.").format(
                    self.replaces, prev.greenhouse, self.greenhouse),
                title=_("Different greenhouse"),
            )
        if not prev.cycle_end_date:
            # Not fatal — a changeover overlaps — but two live blocks on the
            # same beds is exactly how a budget ends up double-counting.
            frappe.msgprint(
                _("{0} has no uprooting date, so both blocks will be budgeted "
                  "in {1} until it gets one.").format(self.replaces, self.greenhouse),
                title=_("Old block still running"), indicator="orange",
            )

    def pull_from_invoice(self):
        """Read breeder and unit cost off the seedling invoice.

        Typed values stay typed — this only runs when an invoice is linked, and
        the fields it fills go read-only in the form so the two can't drift.
        """
        if not self.purchase_invoice:
            self.invoiced_qty = 0
            return

        pi = frappe.get_cached_doc("Purchase Invoice", self.purchase_invoice)
        if pi.docstatus != 1:
            frappe.throw(_("Purchase Invoice {0} is not submitted.").format(pi.name))

        rows = [i for i in pi.items if i.item_code == self.variety]
        if not rows:
            # Seedlings are often billed under a generic item rather than the
            # variety; a single-line invoice is unambiguous either way.
            if len(pi.items) == 1:
                rows = list(pi.items)
            else:
                frappe.throw(
                    _("{0} has no line for {1}, and {2} other lines to choose from."
                      "<br><br>Add the variety as the item code on the invoice, or "
                      "clear the link and type the cost.").format(
                        pi.name, self.variety, len(pi.items)),
                    title=_("Which invoice line?"),
                )

        qty = sum(float(r.qty or 0) for r in rows)
        amount = sum(float(r.amount or 0) for r in rows)

        self.seedling_source = "Purchased from Breeder"
        self.breeder = pi.supplier
        self.invoiced_qty = int(round(qty))
        self.cost_per_plant = round(amount / qty, 4) if qty else 0

    def check_density(self):
        """Beds, density and plant count must tell the same story.

        This is the guard that catches a bed length recorded as 4m when it is
        really 20m - the kind of error that silently makes a budget 5x wrong.
        """
        if not (self.planted_area and self.qty_planted):
            return

        # Only meaningful when the grower typed a density; when it was left
        # blank roll_up_beds derived it, so the two agree by construction.
        drift = abs(self.implied_plants - self.qty_planted) / float(self.qty_planted)
        if drift > DENSITY_TOLERANCE:
            frappe.throw(
                _(
                    "Plants Planted ({0:,}) and the beds disagree: {1:,.0f} m2 x "
                    "{2:g} plants/m2 implies {3:,} plants ({4:.0%} out).<br><br>"
                    "Check the bed dimensions or the density before budgeting off this."
                ).format(
                    int(self.qty_planted), self.planted_area,
                    float(self.plants_per_sqm), int(self.implied_plants), drift,
                ),
                title=_("Bed area and plant count disagree"),
            )

        density = float(self.plants_per_sqm)
        if not (MIN_DENSITY <= density <= MAX_DENSITY):
            frappe.throw(
                _(
                    "{0:,} plants over {1:,.0f} m2 of beds is <b>{2:,.1f} plants/m2</b>. "
                    "Cut flowers run about 6-8.<br><br>"
                    "Either the beds are mis-measured ({3} bed(s) averaging "
                    "{4:,.1f} m2 each), or the plant count is wrong."
                ).format(
                    int(self.qty_planted), self.planted_area, density,
                    len(self.beds or []),
                    self.planted_area / max(len(self.beds or []), 1),
                ),
                title=_("Implausible planting density"),
            )

    def check_greenhouse_capacity(self):
        """A greenhouse only has so many square meters; plantings can't overcommit it.

        No ceiling recorded means the greenhouse hasn't been measured yet —
        skip rather than block on data that doesn't exist.
        """
        if not (self.greenhouse and self.planted_area):
            return
        area_cap = frappe.db.get_value("Greenhouse", self.greenhouse, "gross_area")
        if not area_cap:
            return

        others = frappe.get_all(
            "Crop Cycle",
            filters={
                "greenhouse": self.greenhouse,
                "status": ("in", ("Planned", "Active")),
                "name": ("!=", self.name),
            },
            fields=["planted_area"],
        )
        other_total = sum(float(o.planted_area or 0) for o in others)
        if other_total + float(self.planted_area) <= float(area_cap):
            return

        remaining = float(area_cap) - other_total
        density = float(self.plants_per_sqm or 0)
        plants_possible = int(remaining * density) if remaining > 0 and density else 0
        frappe.throw(
            _(
                "{0} is {1:,.0f} m2. Other active plantings already use {2:,.0f} m2, "
                "leaving {3:,.0f} m2 — room for about {4:,} plants at {5:g}/m2.<br><br>"
                "This planting needs {6:,.0f} m2."
            ).format(
                self.greenhouse, float(area_cap), other_total, remaining,
                plants_possible, density, float(self.planted_area),
            ),
            title=_("Greenhouse is full"),
        )
