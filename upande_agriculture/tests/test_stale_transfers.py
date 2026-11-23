"""A bucket harvested again while an order still waits to move its previous fill: that transfer never
happened. The rows are marked not found and a Bucket Reuse Anomaly records it per order. The database is
mocked."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import UnitTestCase

from upande_agriculture.mobile import api


def _row(name, opl, docstatus, in_transit=0, stems=100):
	return frappe._dict(
		name=name,
		opl=opl,
		docstatus=docstatus,
		item_code="Amina",
		stock_qty=stems,
		shelf="KPK-I33T",
		source_warehouse="Kapkolia Receiving Cold Store - KR",
		in_transit=in_transit,
	)


class UnitTestStaleTransfers(UnitTestCase):
	def _run(self, rows, packhouse_ok=True):
		docs = []

		def get_doc(arg, *a, **kw):
			doc = MagicMock()
			docs.append((arg, doc))
			return doc

		with (
			patch.object(api, "stale_transfer_rows", return_value=rows),
			patch.object(api, "_not_found_via_packhouse", return_value=packhouse_ok) as via,
			patch.object(api.frappe, "get_doc", side_effect=get_doc),
			patch.object(api.frappe.db, "set_value") as set_value,
			patch.object(api.frappe.db, "exists", return_value=True),
			patch.object(api.frappe.db, "commit"),
			patch.object(api.frappe.utils, "now", return_value="2026-11-23 22:00:00"),
		):
			api.close_stale_transfers("LL2ZG7", "Simotwo GH 06 - KR", "MAT-STE-1")
		anomalies = [a for a, _ in docs if isinstance(a, dict)]
		return via, set_value, anomalies

	def test_nothing_waiting_does_nothing(self):
		via, set_value, anomalies = self._run([])
		via.assert_not_called()
		set_value.assert_not_called()
		self.assertEqual(anomalies, [])

	def test_a_draft_order_goes_through_the_packhouse_not_found_step(self):
		via, set_value, anomalies = self._run([_row("R1", "OPL-1", 0, stems=140)])
		via.assert_called_once()
		set_value.assert_not_called()
		self.assertEqual(len(anomalies), 1)
		a = anomalies[0]
		self.assertEqual(a["skipped_step"], "Reused Before Transfer")
		self.assertEqual((a["order_pick_list"], a["stems"], a["variety"]), ("OPL-1", 140, "Amina"))
		self.assertEqual((a["farm"], a["harvest_stock_entry"]), ("Kapkolia", "MAT-STE-1"))

	def test_a_submitted_order_has_its_flags_cleared_and_marked_not_found(self):
		via, set_value, anomalies = self._run(
			[_row("R1", "OPL-2", 1), _row("R2", "OPL-2", 1)], packhouse_ok=False
		)
		self.assertEqual(set_value.call_count, 2)
		values = set_value.call_args_list[0].args[2]
		self.assertEqual(
			{k: values[k] for k in ("awaiting_transfer", "loaded_in_trolley", "in_transit", "not_found")},
			{"awaiting_transfer": 0, "loaded_in_trolley": 0, "in_transit": 0, "not_found": 1},
		)
		self.assertEqual(len(anomalies), 1)
		self.assertEqual(anomalies[0]["stems"], 200)

	def test_one_anomaly_per_order(self):
		_via, _set, anomalies = self._run(
			[_row("R1", "OPL-1", 0), _row("R2", "OPL-2", 1)], packhouse_ok=False
		)
		self.assertEqual(sorted(a["order_pick_list"] for a in anomalies), ["OPL-1", "OPL-2"])

	def test_a_failure_never_blocks_the_harvest(self):
		with (
			patch.object(api, "stale_transfer_rows", side_effect=Exception("boom")),
			patch.object(api.frappe.db, "rollback"),
			patch.object(api.frappe, "log_error") as log,
		):
			api.close_stale_transfers("LL2ZG7", None, None)
		log.assert_called_once()

	def test_only_a_draft_not_on_a_truck_uses_the_packhouse_step(self):
		self.assertFalse(api._not_found_via_packhouse([_row("R1", "OPL-1", 1)]))
		self.assertFalse(api._not_found_via_packhouse([_row("R1", "OPL-1", 0, in_transit=1)]))
