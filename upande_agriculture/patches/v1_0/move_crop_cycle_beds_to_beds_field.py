"""Crop Cycle Bed rows saved by the old greenhouse-shaped Crop Cycle sit under
parentfield 'individual_beds'; the table is 'beds' now, so those cycles load
with no beds -- any later save rebuilds them at 0 plants (every uproot reads
"already uprooted") while SQL that joins on parent alone still sees them.
Point the rows at the current field."""

import frappe


def execute():
	if frappe.get_meta("Crop Cycle").has_field("individual_beds"):
		return
	frappe.db.sql(
		"""UPDATE `tabCrop Cycle Bed` SET parentfield = 'beds'
		   WHERE parenttype = 'Crop Cycle' AND parentfield = 'individual_beds'"""
	)
