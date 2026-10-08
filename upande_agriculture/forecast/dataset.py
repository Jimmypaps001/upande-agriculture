"""Plot-count photos with their stage counts, zipped for annotation and
training a bud-stage detector. Image-level counts are weak labels: an
annotator draws the boxes, and the counts check that none were missed."""
import csv
import io
import zipfile

import frappe
from frappe.utils import nowdate


@frappe.whitelist()
def export_stage_dataset(from_date=None):
	frappe.only_for("System Manager")
	filters = {"photo_1": ["is", "set"]}
	if from_date:
		filters["sampling_date"] = [">=", from_date]
	samples = frappe.get_all("Bed Sample", filters=filters, order_by="sampling_date",
		fields=["name", "sample_plot", "greenhouse", "section", "variety", "sampling_date",
			"plants_counted", "photo_1", "photo_2"])
	counts = {}
	for r in frappe.get_all("Bed Sample Stage", fields=["parent", "stage_name", "count"],
			filters={"parenttype": "Bed Sample", "parent": ["in", [s.name for s in samples] or [""]]}):
		counts.setdefault(r.parent, {})[r.stage_name] = r.count or 0
	stage_names = sorted({k for c in counts.values() for k in c})

	buf = io.BytesIO()
	rows = []
	with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
		for s in samples:
			for i, url in enumerate((s.photo_1, s.photo_2), start=1):
				if not url:
					continue
				name = frappe.db.get_value("File", {"file_url": url})
				if not name:
					continue
				arc = f"images/{s.name}-{i}.jpg"
				z.writestr(arc, frappe.get_doc("File", name).get_content())
				rows.append({"image": arc, "sample": s.name, "plot": s.sample_plot or "",
					"greenhouse": s.greenhouse, "section": s.section or "", "variety": s.variety,
					"date": str(s.sampling_date), "plants_counted": s.plants_counted,
					**{k: counts.get(s.name, {}).get(k, 0) for k in stage_names}})
		out = io.StringIO()
		w = csv.DictWriter(out, fieldnames=["image", "sample", "plot", "greenhouse", "section", "variety",
			"date", "plants_counted", *stage_names])
		w.writeheader()
		w.writerows(rows)
		z.writestr("labels.csv", out.getvalue())
	f = frappe.get_doc({"doctype": "File", "file_name": f"stage-dataset-{nowdate()}.zip",
		"content": buf.getvalue(), "is_private": 1}).insert(ignore_permissions=True)
	return {"file_url": f.file_url, "images": len(rows)}
