"""Bilashaka's Deployment Request workflow, put back after every migrate.

upande_dev_tools force-imports its shared "Deployment Review" workflow on every
deploy, and activating it switches every other Deployment Request workflow off.
This runs after fixtures are synced, so Bilashaka keeps its own flow:
Requested -> Start Deployment -> In Progress -> Mark Deployed -> Deployed,
moved only by Deployment Approver (Teddy), with bell notifications both ways
(the site has no outgoing email account).
"""
import frappe
from frappe.permissions import add_permission, update_permission_property

DOCTYPE = "Deployment Request"
ROLE = "Deployment Approver"
WORKFLOW = "Bilashaka Deployment"
APPROVERS = ("teddy@upande.com",)
STATES = (("Requested", "Dev Team"), ("In Progress", ROLE), ("Deployed", ROLE))
TRANSITIONS = (("Requested", "Start Deployment", "In Progress"), ("In Progress", "Mark Deployed", "Deployed"))
NOTIFICATIONS = (
	{
		"name": "Deployment Requested",
		"event": "New",
		"subject": "Deployment requested: {{ doc.app }} on {{ doc.instance }} ({{ doc.branch }})",
		"recipients": [{"receiver_by_role": ROLE}],
	},
	{
		"name": "Deployment Done",
		"event": "Value Change",
		"value_changed": "workflow_state",
		"condition": 'doc.workflow_state == "Deployed"',
		"subject": "Deployed: {{ doc.app }} on {{ doc.instance }} ({{ doc.branch }})",
		"recipients": [{"receiver_by_document_field": "requested_by_user"}],
	},
)


def apply():
	if not frappe.db.exists("DocType", DOCTYPE):
		return
	if not frappe.db.exists("Role", ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": ROLE, "desk_access": 1}).insert(ignore_permissions=True)
	for user in APPROVERS:
		if frappe.db.exists("User", user) and ROLE not in frappe.get_roles(user):
			frappe.get_doc("User", user).add_roles(ROLE)
	add_permission(DOCTYPE, ROLE, 0)  # copies the standard rows to Custom DocPerm first
	for right in ("read", "write"):
		update_permission_property(DOCTYPE, ROLE, 0, right, 1)

	for state, _role in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(ignore_permissions=True)
	for _s, action, _n in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(ignore_permissions=True)

	wf = frappe.get_doc("Workflow", WORKFLOW) if frappe.db.exists("Workflow", WORKFLOW) else frappe.new_doc("Workflow")
	wf.update({"workflow_name": WORKFLOW, "document_type": DOCTYPE, "workflow_state_field": "workflow_state",
		"is_active": 1, "send_email_alert": 0, "override_status": 0})
	wf.set("states", [{"state": s, "doc_status": "0", "allow_edit": r} for s, r in STATES])
	wf.set("transitions", [{"state": s, "action": a, "next_state": n, "allowed": ROLE, "allow_self_approval": 1}
		for s, a, n in TRANSITIONS])
	wf.save(ignore_permissions=True)  # active: switches the shared "Deployment Review" off

	for spec in NOTIFICATIONS:
		n = frappe.get_doc("Notification", spec["name"]) if frappe.db.exists("Notification", spec["name"]) \
			else frappe.get_doc({"doctype": "Notification", "__newname": spec["name"]})
		n.update({"document_type": DOCTYPE, "channel": "System Notification", "enabled": 1,
			"event": spec["event"], "value_changed": spec.get("value_changed"), "condition": spec.get("condition"),
			"subject": spec["subject"], "message": spec["subject"]})
		n.set("recipients", spec["recipients"])
		n.save(ignore_permissions=True)
	frappe.db.commit()
