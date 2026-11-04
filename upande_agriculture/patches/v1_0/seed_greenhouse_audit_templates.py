"""Seed the 5S and DMS Greenhouse Audit Templates. Idempotent."""

from upande_agriculture.greenhouse_audit_templates import seed


def execute():
	seed()
