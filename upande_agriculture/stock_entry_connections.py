"""Dashboard (Connections) override for Stock Entry.

Roses move through the packhouse as a chain of Stock Entries: one or more
Harvesting / Grading entries feed a single Receiving entry. Each harvest/grading
entry stores its Receiving entry in ``custom_receiving_entry`` (a self-referential
Link on Stock Entry). This override surfaces that relationship in the Connections
tab so that from a Receiving entry you can see every harvest/grading entry that
fed it — the reverse direction of the ``custom_receiving_entry`` link field.

Lives here rather than upande_packhouse: harvesting is this app's own domain
(createHarvestStockEntry / createGradingStockEntry both live in
upande_agriculture.mobile.api), so the doctype override belongs with the app
that actually owns the Harvesting/Grading side of this relationship.
"""

import frappe


def get_dashboard_data(data):
	data = data or frappe._dict()
	data.setdefault("transactions", [])
	data.setdefault("non_standard_fieldnames", {})

	# The related Stock Entries link back through custom_receiving_entry, not the
	# default parent field name.
	data["non_standard_fieldnames"]["Stock Entry"] = "custom_receiving_entry"

	labels = [group.get("label") for group in data["transactions"]]
	if "Roses Production" not in labels:
		data["transactions"].append({"label": "Roses Production", "items": ["Stock Entry"]})

	return data
