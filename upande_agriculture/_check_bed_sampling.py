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
		sample_length_m=1, suggested=1, photos=[photo, photo], captured_at="2026-10-07 13:30:00")
	r1 = bs.submit_bed_sample(**args); r2 = bs.submit_bed_sample(**args)
	frappe.db.commit()
	assert r1 == r2, (r1, r2)
	d = frappe.get_doc("Bed Sample", r1["name"])
	print("SAMPLE", d.name, d.title, d.total_count, d.bed, d.photo_1, d.photo_2, [(s.stage_name, s.count, s.days_to_harvest) for s in d.stages])
	assert d.total_count == 19 and d.photo_1 and d.photo_2 and d.stages[0].days_to_harvest == 21 and d.bed
	assert c["suggested"][0] in bs.get_sampling_plan()["cycles"][0]["sampled_this_week"]
	print("ALL OK")
