import frappe


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/budget-forecast"
		raise frappe.Redirect
	context.no_cache = 1
	context.csrf_token = frappe.sessions.get_csrf_token()
	# never let the browser serve a stale page — a normal reload always gets latest
	if getattr(frappe.local, "response_headers", None) is not None:
		frappe.local.response_headers["Cache-Control"] = "no-store, must-revalidate"
