"""Approval workflow for the weekly Production Plan Form.

Agriculture User plans the tasks and submits for approval -> an Agriculture
Manager (or Farm Manager) approves or rejects -> the user keys in the actuals
and submits them for verification -> the manager verifies (submits the plan)
or rejects. A rejection needs a comment, and the manager can rate the quality
of the work. Idempotent: safe to run on every migrate.
"""

import frappe

from upande_agriculture.upande_agriculture.doctype.production_plan_form.production_plan_form import (
	APPROVAL_WORKFLOW,
	APPROVED,
	DRAFT,
	PENDING_APPROVAL,
	PENDING_VERIFICATION,
	REJECTED,
	VERIFIED,
)

DOCTYPE = "Production Plan Form"
USER = "Agriculture User"
MANAGER = "Agriculture Manager"
FARM_MANAGER = "Farm Manager"
APPROVERS = (MANAGER, FARM_MANAGER)
PLANNERS = (USER, MANAGER)

SUBMIT_FOR_APPROVAL = "Submit for Approval"
APPROVE = "Approve"
REJECT = "Reject"
SUBMIT_FOR_VERIFICATION = "Submit for Verification"
VERIFY = "Verify"

STATES = [
	# state, docstatus, style, role allowed to edit in desk
	(DRAFT, 0, "", USER),
	(PENDING_APPROVAL, 0, "Warning", MANAGER),
	(APPROVED, 0, "Primary", USER),
	(PENDING_VERIFICATION, 0, "Warning", MANAGER),
	(REJECTED, 0, "Danger", USER),
	(VERIFIED, 1, "Success", MANAGER),
]


def transitions():
	rows = []

	def add(state, action, next_state, roles, condition=None):
		for role in roles:
			rows.append(
				{
					"state": state,
					"action": action,
					"next_state": next_state,
					"allowed": role,
					"allow_self_approval": 1,
					"condition": condition,
				}
			)

	add(DRAFT, SUBMIT_FOR_APPROVAL, PENDING_APPROVAL, PLANNERS)
	add(PENDING_APPROVAL, APPROVE, APPROVED, APPROVERS)
	add(PENDING_APPROVAL, REJECT, REJECTED, APPROVERS)
	add(APPROVED, SUBMIT_FOR_VERIFICATION, PENDING_VERIFICATION, PLANNERS)
	add(APPROVED, REJECT, REJECTED, APPROVERS)
	add(PENDING_VERIFICATION, VERIFY, VERIFIED, APPROVERS)
	add(PENDING_VERIFICATION, REJECT, REJECTED, APPROVERS)
	add(REJECTED, SUBMIT_FOR_APPROVAL, PENDING_APPROVAL, PLANNERS, "not doc.approved_by")
	add(REJECTED, SUBMIT_FOR_VERIFICATION, PENDING_VERIFICATION, PLANNERS, "doc.approved_by")
	return rows


def setup():
	for state, _docstatus, style, _role in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc(
				{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
			).insert(ignore_permissions=True)
	for action in (SUBMIT_FOR_APPROVAL, APPROVE, REJECT, SUBMIT_FOR_VERIFICATION, VERIFY):
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)

	if frappe.db.exists("Workflow", APPROVAL_WORKFLOW):
		workflow = frappe.get_doc("Workflow", APPROVAL_WORKFLOW)
	else:
		workflow = frappe.new_doc("Workflow")
		workflow.workflow_name = APPROVAL_WORKFLOW
	workflow.document_type = DOCTYPE
	workflow.workflow_state_field = "workflow_state"
	workflow.is_active = 1
	workflow.send_email_alert = 0
	workflow.set(
		"states",
		[
			{"state": state, "doc_status": str(docstatus), "allow_edit": role}
			for state, docstatus, _style, role in STATES
		],
	)
	workflow.set("transitions", transitions())
	workflow.save(ignore_permissions=True)

	ensure_permissions()


def ensure_permissions():
	"""The doctype JSON carries these roles, but a site with Custom DocPerm rows
	for this doctype ignores the JSON, so mirror them there too."""
	if not frappe.db.exists("Custom DocPerm", {"parent": DOCTYPE}):
		return
	from frappe.permissions import add_permission, update_permission_property

	wanted = {
		USER: ("read", "write", "create", "report", "print", "email", "share", "export"),
		MANAGER: (
			"read",
			"write",
			"create",
			"submit",
			"cancel",
			"amend",
			"delete",
			"report",
			"print",
			"email",
			"share",
			"export",
		),
		FARM_MANAGER: ("read", "write", "submit", "report", "print", "email", "share", "export"),
	}
	for role, ptypes in wanted.items():
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.exists("Custom DocPerm", {"parent": DOCTYPE, "role": role, "permlevel": 0}):
			add_permission(DOCTYPE, role, 0)
		for ptype in ptypes:
			update_permission_property(DOCTYPE, role, 0, ptype, 1, validate=False)
	frappe.clear_cache(doctype=DOCTYPE)
