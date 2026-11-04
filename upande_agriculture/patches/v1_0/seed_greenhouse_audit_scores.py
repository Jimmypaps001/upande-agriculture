"""Seed the 0-4 Greenhouse Audit Score records the audit Score link points at. Idempotent."""

from upande_agriculture.greenhouse_audit_templates import seed


def execute():
    seed()
