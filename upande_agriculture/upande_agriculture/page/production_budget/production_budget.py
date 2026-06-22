"""Production Budget desk page — see budget.js for the UI."""

import frappe


def get_context(context):
    context.no_cache = 1
    return context
