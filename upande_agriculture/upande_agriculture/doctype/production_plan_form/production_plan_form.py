# Copyright (c) 2026, Upande Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, nowdate


def ensure_plan_editable(doc):
    """A submitted plan is locked: its tasks can only change through an amendment."""
    if doc.docstatus == 1:
        frappe.throw(_("Production Plan {0} is submitted. Amend it to change its tasks.").format(doc.name))
    if doc.docstatus == 2:
        frappe.throw(_("Production Plan {0} is cancelled.").format(doc.name))


class ProductionPlanForm(Document):
    def before_validate(self):
        # The desk form only asks for Farm / Week / Supervisor / Greenhouse;
        # the planning board, mobile app and ToDo sync still key on the rest.
        if not self.farm and self.greenhouse:
            self.farm = frappe.db.get_value("Warehouse", self.greenhouse, "custom_farm")
        if not self.plan_year:
            self.plan_year = getdate(nowdate()).isocalendar()[0]
        if not self.plan_period and self.plan_week:
            self.plan_period = f"{self.plan_year}-W{cint(self.plan_week):02d}"
        if not self.company:
            self.company = (
                (self.farm and frappe.db.get_value("Farm", self.farm, "company"))
                or (self.greenhouse and frappe.db.get_value("Warehouse", self.greenhouse, "company"))
                or frappe.defaults.get_user_default("Company")
                or frappe.db.get_single_value("Global Defaults", "default_company")
            )
        before = self.get_doc_before_save()
        week_changed = bool(before) and (cint(before.plan_year), cint(before.plan_week)) != (cint(self.plan_year), cint(self.plan_week))
        old_days = {r.name: r.day for r in (before.tasks if before else [])}
        for task in (self.tasks or []):
            day_changed = week_changed or old_days.get(task.name) != task.day
            self.sync_task(task, day_changed)

    def validate(self):
        if not self.company:
            frappe.throw(_("Could not work out the Company for this plan."))
        for task in (self.tasks or []):
            if not task.task_name:
                frappe.throw(_("Row {0}: set the Assigned Task.").format(task.idx))
            task.check_beds_exist()

    def sync_task(self, task, day_changed):
        from upande_agriculture.plan import DAYS, _date_for_day

        if not task.greenhouse:
            task.greenhouse = self.greenhouse
        if task.assigned_task and not task.task_name:
            task.task_name = frappe.db.get_value("Task", task.assigned_task, "subject")
        # Day drives Due Date when it was the thing edited here; the board
        # and mobile app move Due Date directly, and Day follows.
        if task.day and self.plan_week and (day_changed or not task.due_date):
            task.due_date = _date_for_day(self.plan_year, self.plan_week, task.day)
        elif task.due_date:
            task.day = DAYS[getdate(task.due_date).weekday()]
        task.achievement = flt(cint(task.actual) * 100 / cint(task.target), 2) if cint(task.target) else 0
