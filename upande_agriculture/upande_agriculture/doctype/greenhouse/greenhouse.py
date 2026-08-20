import frappe
from frappe import _
from frappe.model.document import Document

# A bed counts as standing production, not a hole in the ground or gone.
OCCUPIED_STATUSES = ("Planted", "Producing", "Harvesting", "Transplanted")


def _contiguous_runs(numbers: list[int]) -> list[tuple[int, int]]:
    """[1,2,3,7,8,10] -> [(1,3),(7,8),(10,10)]."""
    runs, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:] + [None]:
        if n == prev + 1:
            prev = n
            continue
        runs.append((start, prev))
        start = prev = n
    return runs


def cycle_bed_ranges(cycles: list[dict]) -> list[dict]:
    """One Bed Range dict per contiguous run of beds each cycle CURRENTLY
    occupies -- shared by the prefill (a fresh Greenhouse ledger reading in
    what Crop Cycle already knows) and the forward sync (a saved Crop Cycle
    pushing its own footprint onto the Greenhouse it belongs to).

    Two things a cycle's own `beds` table alone would get wrong: it isn't
    necessarily contiguous (a cycle on "1-50, 67-90" would otherwise claim
    51-66 too), and it never shrinks when part of it is uprooted (an
    Uproot Log entry doesn't remove the row, by design, to keep the
    original planting on record) -- so beds it's logged as no longer
    standing on have to be excluded here, or a resync re-claims ground
    that's since moved on to something else.
    """
    from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import (
        uprooted_bed_numbers,
    )

    if not cycles:
        return []

    beds = frappe.db.sql(
        """
        SELECT ccb.parent, ccb.bed_length, ccb.bed_width, b.bed AS bed_number
        FROM `tabCrop Cycle Bed` ccb
        JOIN `tabBed` b ON b.name = ccb.bed
        WHERE ccb.parent IN %(names)s
        ORDER BY ccb.parent, b.bed
        """,
        {"names": [c["name"] for c in cycles]},
        as_dict=True,
    )
    by_cycle: dict[str, list] = {}
    for row in beds:
        # upande_scp's Bed stores the number as Data — normalise so the
        # set arithmetic against uprooted_bed_numbers() (ints) holds.
        row.bed_number = int(row.bed_number)
        by_cycle.setdefault(row.parent, []).append(row)

    out = []
    for c in cycles:
        rows = by_cycle.get(c["name"])
        if not rows:
            continue
        removed = uprooted_bed_numbers(c["name"])
        standing = sorted(r.bed_number for r in rows if r.bed_number not in removed)
        if not standing:
            continue
        for lo, hi in _contiguous_runs(standing):
            out.append({
                "from_bed": lo,
                "to_bed": hi,
                "variety": c["variety"],
                "crop_protocol": c["crop_protocol"],
                "planting_date": c["planting_date"],
                "bed_length": rows[0].bed_length,
                "bed_width": rows[0].bed_width,
                "plants_per_sqm": c["plants_per_sqm"],
            })
    return out


@frappe.whitelist()
def bed_ranges_from_crop_cycles(warehouse: str) -> list[dict]:
    """Existing Crop Cycle plantings in this house, shaped as Bed Range rows
    -- see cycle_bed_ranges() for what "shaped" accounts for."""
    cycles = frappe.get_all(
        "Crop Cycle",
        filters={"greenhouse": warehouse, "status": ("!=", "Ended")},
        fields=["name", "variety", "crop_protocol", "planting_date", "plants_per_sqm"],
    )
    return cycle_bed_ranges(cycles)


class Greenhouse(Document):
    def validate(self):
        self.expand_bed_ranges()
        self.apply_logs()
        self.roll_up()
        self.check_capacity()

    # ------------------------------------------------------------- planting
    def expand_bed_ranges(self):
        """Bed Range is the bulk-entry form; Individual Beds is the ledger.

        Only bed numbers not already tracked get a new row — a range you
        already planted keeps whatever a Replanting/Uprooting Log has since
        done to it, rather than that history being wiped on every save. A
        range that targets a bed still standing under a DIFFERENT variety is
        refused outright — two varieties can't grow on the same ground, and
        silently ignoring the new range (the old behaviour) hid that from
        whoever just typed it.

        A range already `applied` is skipped entirely: its job (adding its
        beds) is done, and reprocessing it would conflict with its OWN
        history the moment any of its beds get replanted to something else
        via a log -- the same bed, still correctly tracked, would suddenly
        read as "occupied by a different variety" against the row that
        planted it originally.
        """
        by_number = {int(b.bed_number): b for b in (self.individual_beds or []) if b.bed_number}
        for r in (self.bed_range or []):
            if r.applied:
                continue
            if not (r.from_bed and r.to_bed and r.bed_length and r.bed_width):
                continue
            lo, hi = int(r.from_bed), int(r.to_bed)
            if lo > hi:
                lo, hi = hi, lo
            r.total_beds_area = round((hi - lo + 1) * r.bed_length * r.bed_width, 2)
            area = round(r.bed_length * r.bed_width, 2)
            plants = int(round(area * r.plants_per_sqm)) if r.plants_per_sqm else 0
            for n in range(lo, hi + 1):
                existing = by_number.get(n)
                if existing:
                    if existing.status in OCCUPIED_STATUSES and existing.variety != r.variety:
                        frappe.throw(
                            _("Bed {0} already has {1} growing on it ({2}). Uproot it "
                              "before planting {3} there.").format(
                                n, existing.variety, existing.status, r.variety),
                            title=_("Bed already occupied"),
                        )
                    if existing.status not in OCCUPIED_STATUSES:
                        # An uprooted bed reappearing under a range is a
                        # replant -- a new Crop Cycle planted on freed ground
                        # syncs in through here, so the ledger row comes back
                        # to life instead of staying Uprooted forever.
                        existing.variety = r.variety
                        existing.status = "Planted"
                        existing.plant_date = r.planting_date
                        existing.length_m = r.bed_length
                        existing.width_m = r.bed_width
                        existing.area_m2 = area
                        existing.plant_count = plants
                    continue
                new_row = self.append("individual_beds", {
                    "bed_number": n,
                    "variety": r.variety,
                    "length_m": r.bed_length,
                    "width_m": r.bed_width,
                    "area_m2": area,
                    "plant_count": plants,
                    "status": "Planted",
                    "plant_date": r.planting_date,
                })
                by_number[n] = new_row
            r.applied = 1

    # ----------------------------------------------------------------- logs
    def apply_logs(self):
        """Replanting and Uprooting Logs are a timeline, not independent edits.

        New rows are replayed in date order so the LATEST thing that
        happened to a bed is what it shows today, regardless of which table
        it's logged in or what order the rows were typed. A row already
        `applied` is skipped entirely, not just its mutation -- its own
        validation ("only so many were standing") read individual_beds as it
        stood right before that event, and individual_beds has since moved
        on; re-checking an old row against today's state compares it to the
        wrong baseline and fails for beds that were legitimately replanted
        since. Log a correcting entry instead of editing history.
        """
        by_number = {int(b.bed_number): b for b in (self.individual_beds or []) if b.bed_number}

        events = []
        for r in (self.replanting_logs or []):
            if not r.applied:
                events.append((r.replant_date, 1, "replant", r))
        for r in (self.uprooting_logs or []):
            if not r.applied:
                events.append((r.uproot_date, 0, "uproot", r))
        events.sort(key=lambda e: (e[0] or "", e[1]))

        for date, _tie, kind, row in events:
            lo, hi = int(row.from_bed), int(row.to_bed)
            if lo > hi:
                lo, hi = hi, lo
            missing = [n for n in range(lo, hi + 1) if n not in by_number]
            if missing:
                frappe.throw(
                    _("Beds {0}-{1} aren't tracked here yet — add them under Bed "
                      "Configuration first.").format(lo, hi),
                    title=_("Bed not found"),
                )
            beds = [by_number[n] for n in range(lo, hi + 1)]
            standing = sum(int(b.plant_count or 0) for b in beds)

            if kind == "replant":
                # An uproot on the same date sorts first (tie=0 before tie=1), so
                # "uproot and replant on the same day" already works -- this only
                # catches a replant with no uproot anywhere in its own history.
                still_occupied = [b.bed_number for b in beds if b.status in OCCUPIED_STATUSES]
                if still_occupied:
                    frappe.throw(
                        _("Bed(s) {0} are still standing under something else — uproot "
                          "them before replanting. Log the uproot on the same date to "
                          "do both at once.").format(", ".join(str(n) for n in still_occupied)),
                        title=_("Not uprooted yet"),
                    )
                # No "not more than standing" check here -- a bed must already
                # be uprooted (checked above) before a replant can touch it,
                # so what was standing on it is always ~0 by this point. A
                # replant's quantity is bounded by bed area/density, not by
                # whatever used to grow there.
                per_bed = int(round(row.qty_replanted / len(beds))) if row.qty_replanted else None
                for b in beds:
                    b.variety = row.new_variety
                    b.status = "Planted"
                    b.plant_date = date
                    if per_bed is not None:
                        b.plant_count = per_bed
            else:  # uproot
                if row.qty_uprooted and row.qty_uprooted > standing:
                    frappe.throw(
                        _("Uprooting {0} plants but only {1:,} are standing on beds "
                          "{2}-{3}.").format(row.qty_uprooted, standing, lo, hi),
                        title=_("More than what's standing"),
                    )
                # Record what was actually growing there, not whatever the
                # dialog happened to have typed -- the beds themselves are
                # the source of truth.
                row.variety = beds[0].variety
                for b in beds:
                    b.status = "Uprooted"
                    b.plant_count = 0

            row.applied = 1

        if self.replanting_logs:
            self.last_replanting_date = max(
                r.replant_date for r in self.replanting_logs if r.replant_date
            )

    # ------------------------------------------------------------- roll-ups
    def roll_up(self):
        """Everything on the Overview tab is read from Individual Beds — it
        is the one place a bed's state lives, so nothing here can drift from it.
        """
        occupied = [b for b in (self.individual_beds or []) if b.status in OCCUPIED_STATUSES]

        varieties: dict[str, dict] = {}
        for b in occupied:
            v = varieties.setdefault(b.variety, {"beds": 0, "area_m2": 0.0, "plants": 0})
            v["beds"] += 1
            v["area_m2"] += float(b.area_m2 or 0)
            v["plants"] += int(b.plant_count or 0)

        self.varieties_grown = []
        for variety, v in sorted(varieties.items()):
            self.append("varieties_grown", {
                "variety": variety, "beds": v["beds"],
                "area_m2": round(v["area_m2"], 2), "plants": v["plants"],
            })

        self.area_planted = round(sum(v["area_m2"] for v in varieties.values()), 2)
        self.varieties = len(varieties)
        self.number_of_plants = sum(v["plants"] for v in varieties.values())
        self.number_of_beds = len(occupied)
        self.plants_per_sqm = (
            int(round(self.number_of_plants / self.area_planted)) if self.area_planted else 0
        )

    # ------------------------------------------------------------ capacity
    def check_capacity(self):
        """A greenhouse only has so much ground; beds can't add up to more."""
        if not (self.gross_area and self.area_planted):
            return
        if self.area_planted <= self.gross_area:
            return
        frappe.throw(
            _(
                "{0} is {1:,.0f} m2, but the beds tracked here now cover {2:,.0f} m2 "
                "— {3:,.0f} m2 over."
            ).format(self.name, self.gross_area, self.area_planted,
                     self.area_planted - self.gross_area),
            title=_("Greenhouse is full"),
        )

    # ------------------------------------------------------ reverse sync
    def owning_cycles(self) -> dict[int, str]:
        """bed_number -> Crop Cycle name, from Bed Range rows tagged by the
        forward sync. A bed with no tag (typed here by hand, never synced
        from a cycle) has nothing on the other side to update."""
        out: dict[int, str] = {}
        for r in (self.bed_range or []):
            if not (r.crop_cycle and r.from_bed and r.to_bed):
                continue
            lo, hi = sorted((int(r.from_bed), int(r.to_bed)))
            for n in range(lo, hi + 1):
                out[n] = r.crop_cycle
        return out


def sync_bed_master(doc, method=None):
    """Mirror each bed's status and variety from the ledger onto its Bed record.

    Individual Beds stays the source of truth; the Bed master just shows it,
    so a grower filtering the Bed list sees what's standing without opening
    the Greenhouse. Only runs where the site's Bed has the custom status
    field (added by the add_bed_status_field patch); variety is cleared when
    nothing is growing. db.set_value keeps this out of the modified-stamp
    churn -- these are derived values, not edits.
    """
    meta = frappe.get_meta("Bed")
    if not meta.has_field("status"):
        return
    house = doc.get("greenhouse") or doc.name
    ledger = {int(b.bed_number): b for b in (doc.individual_beds or []) if b.bed_number}
    if not ledger:
        return

    for bed in frappe.get_all("Bed", filters={"greenhouse": house},
                              fields=["name", "bed", "status", "variety"]):
        row = ledger.get(int(bed.bed))
        if not row:
            continue
        variety = row.variety if row.status in OCCUPIED_STATUSES else None
        updates = {}
        if (row.status or "") != (bed.status or ""):
            updates["status"] = row.status
        if (variety or "") != (bed.variety or ""):
            updates["variety"] = variety
        if updates:
            frappe.db.set_value("Bed", bed.name, updates, update_modified=False)


def sync_logs_to_crop_cycles(doc, method=None):
    """Uprooting/Replanting logged here reaches back to whichever Crop Cycle
    actually owns those beds -- the yield model finds out without anyone
    opening that cycle and repeating the action there.

    Runs on_update, after the Greenhouse save that logged the row is already
    committed -- creating/saving a Crop Cycle from inside this doc's own
    validate() would mean two saves of the same document racing each other.
    Each row is synced exactly once (`synced`), so an unrelated later save
    of this Greenhouse doesn't replay old history.

    Guarded against its own echo: saving a Crop Cycle here can trigger that
    cycle's own sync back onto this same Greenhouse (crop_cycle_on_update),
    which would otherwise land right back in this function before it's done
    -- round and round. The flag makes that echo a no-op; only the sync that
    set it does any work.
    """
    if frappe.flags.get("in_greenhouse_cycle_sync"):
        return
    frappe.flags.in_greenhouse_cycle_sync = True
    try:
        owners = doc.owning_cycles()
        for row in (doc.uprooting_logs or []):
            if row.synced:
                continue
            if _sync_uproot_row(doc, row, owners):
                frappe.db.set_value("Greenhouse Uprooting Log", row.name, "synced", 1, update_modified=False)
        for row in (doc.replanting_logs or []):
            if row.synced:
                continue
            if _sync_replant_row(doc, row, owners):
                frappe.db.set_value("Greenhouse Replanting Log", row.name, "synced", 1, update_modified=False)
    finally:
        frappe.flags.in_greenhouse_cycle_sync = False


def _bed_groups_by_owner(lo: int, hi: int, owners: dict[int, str]) -> dict[str | None, list[int]]:
    groups: dict[str | None, list[int]] = {}
    for n in range(lo, hi + 1):
        groups.setdefault(owners.get(n), []).append(n)
    return groups


def _sync_uproot_row(doc, row, owners: dict[int, str]) -> bool:
    """Returns True only once every owned bed in this row's range has been
    pushed onto its Crop Cycle -- the caller marks the row `synced` on that,
    and only that, so a range with no owner yet (or one that failed) keeps
    getting retried on the next save instead of being marked done when
    nothing actually happened.
    """
    lo, hi = sorted((int(row.from_bed), int(row.to_bed)))
    total = hi - lo + 1
    groups = _bed_groups_by_owner(lo, hi, owners)
    if not any(groups):
        return False  # nothing here is tagged to a cycle at all -- try again later
    all_ok = True
    for cycle_name, beds in groups.items():
        if not cycle_name:
            continue
        # ponytail: a row that straddles two cycles' beds splits qty_uprooted
        # by bed count, not a real per-bed count -- true the moment a grower
        # uproots one block at a time, which is the normal case.
        share = int(round((row.qty_uprooted or 0) * len(beds) / total))
        if share <= 0:
            continue
        try:
            cycle = frappe.get_doc("Crop Cycle", cycle_name)
            if cycle.status == "Ended":
                continue
            bl, bh = min(beds), max(beds)
            cycle.append("uproot_log", {
                "uproot_date": row.uproot_date,
                "bed_range": f"{bl}-{bh}" if bl != bh else str(bl),
                "plants": share,
            })
            cycle.save(ignore_permissions=True)
            if (cycle.plants_standing or 0) <= 0 and cycle.status != "Ended":
                cycle.cycle_end_date = row.uproot_date
                cycle.save(ignore_permissions=True)
        except Exception:
            frappe.log_error(title=f"Greenhouse->Crop Cycle uproot sync failed for {cycle_name}")
            frappe.msgprint(
                _("Beds {0} uprooted here, but {1} couldn't be updated to match — "
                  "check it directly.").format(f"{lo}-{hi}", cycle_name),
                indicator="orange", title=_("Crop Cycle not updated"),
            )
            all_ok = False
            continue
        frappe.msgprint(
            _("{0} updated: {1} plants uprooted on beds {2}.").format(
                cycle_name, share, f"{min(beds)}-{max(beds)}" if len(beds) > 1 else beds[0]),
            indicator="green", alert=True,
        )
    return all_ok


def _sync_replant_row(doc, row, owners: dict[int, str]) -> bool:
    """Returns True once the new Crop Cycle exists -- unlike an uproot, a
    replant doesn't need an owner to do its job (it's always creating a NEW
    cycle), so this only fails, and only gets retried, if the insert itself
    fails."""
    lo, hi = sorted((int(row.from_bed), int(row.to_bed)))
    old_owners = {owners[n] for n in range(lo, hi + 1) if owners.get(n)}
    protocol = frappe.db.get_value("Crop Protocol", {"variety_item": row.new_variety}, "name")
    density = frappe.db.get_value("Crop Protocol", protocol, "plants_per_sqm") if protocol else None

    new_cycle = frappe.get_doc({
        "doctype": "Crop Cycle",
        "greenhouse": doc.greenhouse,
        "variety": row.new_variety,
        "crop_protocol": protocol,
        "planting_date": row.replant_date,
        "bed_range": f"{lo}-{hi}" if lo != hi else str(lo),
        "qty_planted": row.qty_replanted,
        "plants_per_sqm": density,
        # Only meaningful when the whole replanted span came from one cycle;
        # a span straddling two previous cycles links to neither rather than
        # guessing which one the grower meant.
        "replaces": next(iter(old_owners)) if len(old_owners) == 1 else None,
    })
    try:
        new_cycle.insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(title="Greenhouse->Crop Cycle replant sync failed")
        frappe.msgprint(
            _("Beds {0}-{1} replanted here, but a matching Crop Cycle couldn't be "
              "created automatically — create it by hand.").format(lo, hi),
            indicator="orange", title=_("Crop Cycle not created"),
        )
        return False
    frappe.msgprint(
        _("Crop Cycle {0} created for the replant on beds {1}-{2}.").format(
            new_cycle.name, lo, hi),
        indicator="green", alert=True,
    )
    return True
