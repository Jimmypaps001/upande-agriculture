def after_install():
	from upande_agriculture.bed_fields import ensure_bed_fields
	from upande_agriculture.greenhouse_audit_templates import seed

	seed()
	ensure_bed_fields()
