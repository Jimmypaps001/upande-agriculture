"""Give every greenhouse variety listed in Varieties per GH an Active Crop Cycle
(and the variety a default Crop Protocol), so Setup and the forecast read one
source. Rose varieties only; rows with no plants are skipped. Placeholder
planting date, flagged in the notes. Safe to re-run."""
import frappe

from upande_agriculture import setup_api
from upande_agriculture.forecast import data


def execute():
	if not frappe.db.table_exists("Varieties per GH"):
		return
	rows = frappe.db.sql(
		"""SELECT v.parent, v.variety, v.no_of_plants FROM `tabVarieties per GH` v
		   JOIN `tabWarehouse` w ON w.name = v.parent
		   JOIN `tabItem` i ON i.name = v.variety
		   WHERE v.parenttype = 'Warehouse' AND v.parentfield = 'custom_varieties_grown'
		     AND w.warehouse_type = 'Greenhouse' AND w.is_group = 0 AND i.has_variants = 1
		   ORDER BY v.parent, v.idx""", as_dict=True)
	houses = {}
	for r in rows:
		v = houses.setdefault(r.parent, {})
		v[r.variety] = v.get(r.variety, 0) + data._int(r.no_of_plants)
	made = skipped_empty = existing = failed = 0
	for gh, varieties in houses.items():
		frappe.db.savepoint("cycles_from_vpg")
		try:
			for variety, plants in varieties.items():
				if frappe.db.exists("Crop Cycle", {"greenhouse": gh, "variety": variety, "status": "Active"}):
					existing += 1
				elif plants <= 0:
					skipped_empty += 1
				else:
					setup_api._upsert_cycle(gh, variety, plants)
					made += 1
		except Exception:  # one bad greenhouse must not abort migrate
			frappe.db.rollback(save_point="cycles_from_vpg")
			failed += 1
			frappe.log_error(title=f"Crop cycles from Varieties per GH failed for {gh}", message=frappe.get_traceback())
	print(f"Crop cycles from Varieties per GH: {made} created, {existing} already active, "
		f"{skipped_empty} skipped (no plants), {failed} greenhouse(s) failed (see Error Log).")
