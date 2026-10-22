"""The forecast moved from greenhouse sections to greenhouse x variety units.
Switch off the old section spots nobody ever counted, drop snapshots of units
that no longer exist, and lay out every unit's spots. Safe to re-run."""
import frappe

from upande_agriculture.forecast import api, data


def execute():
	units = data.sections()
	keys = {(r.greenhouse, r.section) for r in units}
	counted_plots = set(frappe.db.sql_list("SELECT DISTINCT sample_plot FROM `tabBed Sample` WHERE sample_plot IS NOT NULL"))
	for p in frappe.get_all("Sample Plot", filters={"active": 1}, fields=["name", "greenhouse", "section"]):
		if (p.greenhouse, p.section) not in keys and p.name not in counted_plots:
			frappe.db.set_value("Sample Plot", p.name, "active", 0, update_modified=False)
	for s in frappe.db.sql("SELECT DISTINCT greenhouse, section FROM `tabHarvest Forecast Snapshot`", as_dict=True):
		if (s.greenhouse, s.section) not in keys:
			frappe.db.delete("Harvest Forecast Snapshot", {"greenhouse": s.greenhouse, "section": s.section})
	for row in units:
		api.ensure_plots(row)
