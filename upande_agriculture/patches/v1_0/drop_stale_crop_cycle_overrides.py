"""Remove Property Setters left over from the greenhouse-shaped Crop Cycle.

Crop Cycle used to BE the greenhouse: one record per house, autonamed after it.
The model since moved to one record per planting, so a house holds a succession
of cycles -- but two Property Setters from the old shape survive on migrated
sites and actively break the new one:

  autoname -> "format:{greenhouse}"
      The cycle's name becomes the greenhouse name, so the second planting in
      any house dies on a duplicate-key error. A replant after uprooting is
      exactly that second planting, so the ground could never be re-used.

  field_order -> the old greenhouse layout
      Lists gross_area, number_of_bays, varieties_grown, individual_beds,
      replanting_logs and last_replanting_date -- none of which are fields on
      Crop Cycle any more -- while omitting variety, planting_date, beds and
      uproot_log, which are.

Both are deletions of overrides, so the doctype simply goes back to what its own
JSON says: naming_series CC-.YYYY.-.#### and the field order shipped with the
app. Nothing on the records themselves is touched.
"""

import frappe

STALE = ("Crop Cycle-main-autoname", "Crop Cycle-main-field_order")


def execute():
	for name in STALE:
		if frappe.db.exists("Property Setter", name):
			frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
	frappe.clear_cache(doctype="Crop Cycle")
