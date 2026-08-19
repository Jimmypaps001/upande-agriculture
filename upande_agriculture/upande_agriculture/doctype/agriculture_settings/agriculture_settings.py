import frappe
from frappe import _
from frappe.model.document import Document

from upande_agriculture import weekcal


class AgricultureSettings(Document):
	def validate(self):
		# ISO is Monday by definition; don't let the two fields imply otherwise.
		if weekcal.RULE_OPTIONS.get(self.week_one_rule, weekcal.ISO) == weekcal.ISO:
			self.week_start_day = "Monday"

	def on_update(self):
		"""Say out loud what changed, and what it does not change.

		Week numbers already written keep the rule they were generated under, so
		a planner who flips this setting needs to know their existing budgets did
		not move — otherwise they will trust a number that means something else.
		"""
		if not self.has_value_changed("week_one_rule") and not self.has_value_changed(
			"week_start_day"
		):
			return

		rule = weekcal.get_week_rule()
		# Counted in python, not SQL: projections written before this field existed
		# have week_rule NULL, and `NULL != 'iso'` is NULL in SQL, so a db filter
		# would quietly miss the very documents the planner needs warning about.
		stamped = sum(
			1
			for r in frappe.get_all("Production Projection", pluck="week_rule")
			if (r or weekcal.ISO) != rule
		)
		msg = _("New projections and forecasts will use {0}.").format(
			f"<b>{weekcal.rule_label(rule)}</b>"
		)
		if stamped:
			msg += " " + _(
				"{0} existing projection(s) stay on the rule they were generated with. "
				"Regenerate them to move them onto this one."
			).format(stamped)
		frappe.msgprint(msg, title=_("Week numbering updated"), indicator="orange")
