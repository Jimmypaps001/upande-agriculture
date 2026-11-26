"""A harvest's forecast refresh is queued after its commit, never run inside the submit.

Inside the submit it held the Stock Entry naming lock for seconds while it rebuilt the greenhouse's open
forecasts, and every harvest, grading, receiving and shelving save queued behind it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe.tests import UnitTestCase

from upande_agriculture import controllers


def _entry(kind="Harvesting", house="GH 07 - KR", items=("Odilia", "Athena", "Odilia")):
	doc = SimpleNamespace(
		stock_entry_type=kind,
		custom_greenhouse=house,
		to_warehouse=None,
		items=[SimpleNamespace(item_code=i) for i in items],
	)
	doc.get = lambda key, default=None: getattr(doc, key, default)
	return doc


class UnitTestLiveForecastQueue(UnitTestCase):
	def test_a_harvest_queues_the_refresh_after_commit(self):
		with (
			patch.object(controllers.frappe, "enqueue") as enqueue,
			patch.object(controllers.frappe, "get_doc") as get_doc,
		):
			controllers.stock_entry_on_submit(_entry())
		get_doc.assert_not_called()
		enqueue.assert_called_once()
		kw = enqueue.call_args.kwargs
		self.assertTrue(kw["enqueue_after_commit"])
		self.assertTrue(kw["deduplicate"])
		self.assertEqual(kw["house"], "GH 07 - KR")
		self.assertEqual(kw["varieties"], ["Athena", "Odilia"])
		self.assertEqual(kw["job_id"], "pf_live_refresh:GH 07 - KR:Athena,Odilia")

	def test_other_entries_queue_nothing(self):
		with patch.object(controllers.frappe, "enqueue") as enqueue:
			controllers.stock_entry_on_submit(_entry(kind="Grading"))
			controllers.stock_entry_on_submit(_entry(house=None))
			controllers.stock_entry_on_submit(_entry(items=()))
		enqueue.assert_not_called()

	def test_the_job_refreshes_each_open_forecast(self):
		fc = MagicMock()
		with (
			patch.object(controllers.frappe, "get_all", return_value=["PF-1", "PF-2"]),
			patch.object(controllers.frappe, "get_doc", return_value=fc) as get_doc,
		):
			controllers.refresh_live_forecasts("GH 07 - KR", ["Odilia"])
		self.assertEqual(get_doc.call_count, 2)
		self.assertEqual(fc.pull_actuals.call_count, 2)
		fc.pull_actuals.assert_called_with(persist=True)
