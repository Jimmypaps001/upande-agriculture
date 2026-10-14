"""Sampling v2: create every section's 8 spots at deploy, so the first plan
loads on the phones do not race to create them."""
from upande_agriculture.forecast import api, data


def execute():
	for row in data.sections():
		if row.variety:
			api.ensure_plots(row)
