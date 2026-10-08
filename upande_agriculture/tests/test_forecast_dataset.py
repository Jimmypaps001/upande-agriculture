import base64
import csv
import io
import zipfile

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate
from PIL import Image

from upande_agriculture.forecast import api, dataset
from upande_agriculture.tests.forecast_fixtures import make_greenhouse, make_variety


class TestForecastDataset(FrappeTestCase):
	def test_export_has_images_and_labels(self):
		make_variety()
		gh = make_greenhouse("FC DS", (("S1", 1, 9, "FC-ROSE", 1000),))
		plot = [p for p in api.get_plot_plan()["plots"] if p["greenhouse"] == gh][0]["plot"]
		im = io.BytesIO(); Image.new("RGB", (32, 32), (0, 200, 0)).save(im, "JPEG")
		api.submit_plot_count("fc-ds-1", plot, '[{"stage_name": "Pea", "count": 4}]', 10,
			captured_at=f"{nowdate()} 07:00:00", photos=[base64.b64encode(im.getvalue()).decode()])
		out = dataset.export_stage_dataset(nowdate())
		self.assertGreaterEqual(out["images"], 1)
		f = frappe.get_doc("File", {"file_url": out["file_url"]})
		z = zipfile.ZipFile(io.BytesIO(f.get_content()))
		rows = list(csv.DictReader(io.StringIO(z.read("labels.csv").decode())))
		mine = [r for r in rows if r["greenhouse"] == gh]
		self.assertEqual(mine[0]["Pea"], "4")
		self.assertIn(mine[0]["image"], z.namelist())
