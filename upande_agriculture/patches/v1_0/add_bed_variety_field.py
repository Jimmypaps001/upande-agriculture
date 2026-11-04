"""Give the Bed master a Variety field where its app doesn't ship one.

upande_scp's Bed has `variety`; upande_core's doesn't, yet sync_bed_master
mirrors variety alongside status. Adds whichever mirrored Bed fields are still
missing — idempotent, safe on sites without Bed at all.
"""

from upande_agriculture.bed_fields import ensure_bed_fields


def execute():
	ensure_bed_fields()
