"""The greenhouse harvest lets go of what orders and transfers held of the bucket's last life.

Run with:
    bench --site kaitet.local run-tests --app upande_agriculture \
          --module upande_agriculture.tests.test_release_on_harvest
"""

from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from upande_agriculture.mobile import api

BUCKET = "TAGHV1"


class TestReleaseOnHarvest(UnitTestCase):
	def test_the_harvest_cleanup_releases_the_stale_holds_of_the_bucket(self):
		with patch("upande_packhouse.bucket_reuse.release_on_harvest") as release:
			api.cleanup_bucket_reuse_anomalies(BUCKET, "Kapkolia", "GH 1", "Aqua", "MAT-STE-HARVEST")
		release.assert_called_once_with(BUCKET, "MAT-STE-HARVEST")

	def test_a_failed_release_never_blocks_the_harvest(self):
		with (
			patch("upande_packhouse.bucket_reuse.release_on_harvest", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log,
		):
			api.cleanup_bucket_reuse_anomalies(BUCKET, "Kapkolia", "GH 1", "Aqua", "MAT-STE-HARVEST")
		self.assertTrue(log.called)

	def test_the_release_runs_before_the_shelf_residue_is_cleared(self):
		order = []
		with (
			patch("upande_packhouse.bucket_reuse.release_on_harvest", lambda *a: order.append("release")),
			patch.object(frappe, "get_all", lambda *a, **k: order.append("shelf") or []),
		):
			api.cleanup_bucket_reuse_anomalies(BUCKET, "Kapkolia", "GH 1", "Aqua", "MAT-STE-HARVEST")
		self.assertEqual(order[0], "release")
		self.assertIn("shelf", order)
