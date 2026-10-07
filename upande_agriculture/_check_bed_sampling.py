"""Throwaway check for bed_sampling.py: bench --site bhcloud.local execute upande_agriculture._check_bed_sampling.run"""
import base64
import io

import frappe
from PIL import Image

from upande_agriculture import bed_sampling as bs


def run():
	frappe.set_user("Administrator")
	gh = frappe.db.get_value("Warehouse", {"warehouse_name": "GH01"})
	if not frappe.db.exists("Crop Protocol", {"variety_item": "Athena"}):
		frappe.get_doc({"doctype": "Crop Protocol", "protocol_name": "Athena", "variety_item": "Athena",
			"growth_stages": [{"stage_name": "Pea", "days_to_harvest": 21}, {"stage_name": "Colour showing", "days_to_harvest": 3}]}).insert()
	protocol = frappe.db.get_value("Crop Protocol", {"variety_item": "Athena"})
	cc = frappe.db.get_value("Crop Cycle", {"greenhouse": gh, "variety": "Athena"})
	if not cc:
		cc = frappe.get_doc({"doctype": "Crop Cycle", "greenhouse": gh, "variety": "Athena", "crop_protocol": protocol,
			"status": "Active", "planting_date": "2026-01-05", "bed_range": "1-10"}).insert().name
	frappe.db.commit()

	plan = bs.get_sampling_plan()
	c = next(x for x in plan["cycles"] if x["crop_cycle"] == cc)
	print("PLAN", {k: c[k] for k in ("beds", "suggested", "stages", "sampled_this_week")})
	assert c["beds"] == list(range(1, 11)) and len(c["suggested"]) == 3 and c["stages"][0]["stage_name"] == "Pea"

	b = io.BytesIO(); Image.new("RGB", (64, 64), (200, 40, 60)).save(b, "JPEG")
	photo = base64.b64encode(b.getvalue()).decode()
	uid = "check-bs-1"
	args = dict(client_uuid=uid, crop_cycle=cc, bed_number=c["suggested"][0],
		counts='[{"stage_name": "Pea", "count": 14}, {"stage_name": "Colour showing", "count": 5}]',
		plants_counted=10, suggested=1, photos=[photo, photo], captured_at="2026-10-07 13:30:00")
	r1 = bs.submit_bed_sample(**args); r2 = bs.submit_bed_sample(**args)
	frappe.db.commit()
	assert r1 == r2, (r1, r2)
	d = frappe.get_doc("Bed Sample", r1["name"])
	print("SAMPLE", d.name, d.title, d.total_count, d.bed, d.photo_1, d.photo_2, [(s.stage_name, s.count, s.days_to_harvest) for s in d.stages])
	assert d.total_count == 19 and d.photo_1 and d.photo_2 and d.stages[0].days_to_harvest == 21 and d.bed
	assert c["suggested"][0] in bs.get_sampling_plan()["cycles"][0]["sampled_this_week"]
	print("ALL OK")


def forecast_check():
	"""Weekly rounds, harvests at 80% of what the round 3 weeks earlier
	predicted: the 3-weeks-ahead rows must learn a 0.8 factor."""
	from frappe.utils import add_days, getdate, nowdate

	from upande_agriculture import bed_forecast as bf
	from upande_agriculture.weekcal import get_week_rule, week_key, week_start

	frappe.set_user("Administrator")
	gh = frappe.db.get_value("Warehouse", {"warehouse_name": "GH01"})
	cc = frappe.db.get_value("Crop Cycle", {"greenhouse": gh, "variety": "Athena"})
	frappe.db.set_value("Crop Cycle", cc, {"qty_planted": 1000, "plants_standing": 1000})
	frappe.db.delete("Bed Sample", {"crop_cycle": cc})
	frappe.db.sql("delete from `tabStock Entry` where stock_entry_type='Harvesting' and remarks='forecast_check'")
	rule = get_week_rule()
	monday = week_start(*week_key(getdate(nowdate()), rule), rule)
	for i in range(8, -1, -1):
		day = add_days(monday, -7 * i)
		for bed, pea in ((3, 10), (6, 14)):
			bs.submit_bed_sample(client_uuid=f"fc-{i}-{bed}", crop_cycle=cc, bed_number=bed,
				counts=f'[{{"stage_name": "Pea", "count": {pea}}}, {{"stage_name": "Colour showing", "count": 2}}]',
				plants_counted=10, captured_at=f"{day} 08:00:00")
	# Harvest 80% of what was predicted for each finished week.
	samples = bf._samples([cc], add_days(monday, -200), monday)
	for i in range(1, 8):
		start = add_days(monday, -7 * i)
		wk = week_key(start, rule)
		pred = bf._predict(bf._round(samples[cc], add_days(start, -21 + 6)), 1000, rule).get(wk, (0, 0))[0]
		if pred:
			se = frappe.get_doc({"doctype": "Stock Entry", "stock_entry_type": "Harvesting", "remarks": "forecast_check",
				"posting_date": start, "set_posting_time": 1, "custom_greenhouse": gh,
				"items": [{"item_code": "Athena-60", "qty": round(pred * 0.8), "t_warehouse": gh,
					"basic_rate": 1, "allow_zero_valuation_rate": 1}]})
			se.insert(); se.submit()
	frappe.db.commit()

	rows = [r for r in bf.forecast(weeks_ahead=6) if r.crop_cycle == cc]
	for r in rows:
		print("FC", r.week, round(r.stems), "+-", round(r.margin), {k: round(v) for k, v in r.lengths.items()}, r.beds, r.factor_basis, "|", r.length_basis)
	lead3 = [r for r in rows if "3 weeks ahead" in r.factor_basis]
	assert lead3 and abs(lead3[0].factor - 0.8) < 0.02, [(r.week, r.factor, r.factor_basis) for r in rows]
	assert any(r.stems > 0 and r.margin > 0 for r in rows)
	from upande_agriculture.upande_agriculture.report.bed_sample_forecast.bed_sample_forecast import execute
	cols, data = execute({"weeks_ahead": 6})
	print("REPORT", [c["label"] for c in cols]); print("REPORT ROW", data[1])
	print("FORECAST OK")
