# Copyright (c) 2026, Upande Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ProductionPlanTask(Document):
    def check_beds_exist(self):
        """A bed named here must actually be a Bed record in this greenhouse
        -- a job planned against ground that isn't there schedules nothing.

        Called from Production Plan Form.validate() -- a child table row's
        own validate() is never invoked automatically when saved as part of
        its parent, only the framework-level field checks are.
        """
        if not (self.greenhouse and self.beds):
            return
        from upande_agriculture.upande_agriculture.doctype.crop_cycle.crop_cycle import (
            parse_bed_range,
        )

        wanted, _partial = parse_bed_range(self.beds)
        if not wanted:
            return
        # int-cast: upande_scp's Bed stores the number as Data, and 'wanted'
        # holds ints — without the cast every bed reads as missing there.
        existing = {int(b) for b in frappe.get_all(
            "Bed", filters={"greenhouse": self.greenhouse, "bed": ("in", wanted)}, pluck="bed",
        )}
        missing = sorted(n for n in wanted if n not in existing)
        if missing:
            frappe.throw(
                _("{0} has no Bed record for: {1}.").format(
                    self.greenhouse, ", ".join(str(n) for n in missing)),
                title=_("Beds not found"),
            )
