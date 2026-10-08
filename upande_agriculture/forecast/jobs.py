"""Scheduled work: a daily snapshot of every section's forecast (audit trail,
scored by the Harvest Forecast Accuracy report) and a weekly calibration."""
import frappe
from frappe.utils import getdate, nowdate

from upande_agriculture.forecast import calibrate, data, service

FIELDS = ["name", "made_on", "target_date", "greenhouse", "section", "variety", "stems", "low", "high",
	"model_version", "creation", "modified", "owner", "modified_by", "docstatus"]


def daily_snapshot(today=None):
	today = getdate(today or nowdate())
	frappe.db.delete("Harvest Forecast Snapshot", {"made_on": today})
	now = frappe.utils.now()
	values = [
		(frappe.generate_hash(length=12), today, d["date"], f["greenhouse"], f["section"], f["variety"],
			round(d["stems"], 2), round(d["low"], 2), round(d["high"], 2), f["model_version"],
			now, now, "Administrator", "Administrator", 0)
		for f in service.forecast_all(start=today) for d in f["daily"]]
	if values:
		frappe.db.bulk_insert("Harvest Forecast Snapshot", FIELDS, values)
	frappe.db.commit()
	return len(values)


def weekly_calibrate():
	done = []
	for variety in sorted({r.variety for r in data.sections() if r.variety}):
		try:
			calibrate.calibrate(variety)
			done.append(variety)
			frappe.db.commit()  # keep this fit if a later variety fails or the job times out
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Harvest forecast calibration failed for {variety}",
				message=frappe.get_traceback())
	return done
