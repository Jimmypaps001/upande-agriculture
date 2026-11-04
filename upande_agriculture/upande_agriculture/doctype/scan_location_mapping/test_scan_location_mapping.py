# Copyright (c) 2026, Upande and Contributors
# See license.txt

# import frappe
from frappe.tests import IntegrationTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
# Warehouse pulls in ERPNext's _Test fixtures, which packhouse's mandatory fields reject.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Farm", "Warehouse"]


class IntegrationTestScanLocationMapping(IntegrationTestCase):
	"""
	Integration tests for ScanLocationMapping.
	Use this class for testing interactions between multiple components.
	"""

	pass
