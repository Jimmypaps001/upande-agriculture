"""Week numbering, with a configurable week 1.

ISO 8601 is the default and is what every week number written before this module
existed means. Farms that number weeks differently ("week 1 is whatever week
contains 1 January", "our week runs Sunday to Saturday") can change it in
Agriculture Settings.

Changing that setting must never silently reinterpret history, so the rule in
force is stamped onto each Production Projection / Production Forecast as it is
generated, and readers use the stamped rule rather than today's setting.

A rule is a short string so it can live in a Data field:

    "iso"       ISO 8601 — Monday start, week 1 contains 4 January
    "jan1:mon"  week 1 contains 1 January, weeks start Monday
    "jan1:sun"  week 1 contains 1 January, weeks start Sunday
    "4day:sun"  week 1 contains 4 January, weeks start Sunday

`week_key` and friends take an optional rule and fall back to ISO, so callers
that have no opinion keep their existing behaviour exactly.
"""

import datetime

import frappe

ISO = "iso"
SETTINGS = "Agriculture Settings"

DOW = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
POLICIES = {
	"4day": "week 1 contains 4 January",
	"jan1": "week 1 contains 1 January",
}

# What the Agriculture Settings dropdown shows -> the policy token stored in rules.
# The dropdown stays readable; the stamped rule stays short enough for a Data field.
RULE_OPTIONS = {
	"ISO 8601": ISO,
	"Week 1 contains 1 January": "jan1",
	"Week 1 contains 4 January": "4day",
}


def parse_rule(rule: str | None) -> tuple[str, int]:
	"""'jan1:sun' -> ('jan1', 6). Anything unrecognised falls back to ISO."""
	if not rule or rule == ISO:
		return ISO, 0
	policy, _, dow = str(rule).partition(":")
	if policy not in POLICIES:
		return ISO, 0
	return policy, _dow_index(dow)


def _dow_index(name: str) -> int:
	"""'sun' / 'Sunday' -> 6. Unknown day means Monday."""
	want = (name or "").strip().lower()[:3]
	for i, full in enumerate(DOW):
		if want and full.lower().startswith(want):
			return i
	return 0


def format_rule(policy: str, start_day: str | None = None) -> str:
	"""('jan1', 'Sunday') -> 'jan1:sun'. ISO has no start day — it is Monday."""
	if policy == ISO or policy not in POLICIES:
		return ISO
	return f"{policy}:{(start_day or 'Monday')[:3].lower()}"


def rule_label(rule: str | None) -> str:
	policy, start_dow = parse_rule(rule)
	if policy == ISO:
		return "ISO 8601 (Monday start, week 1 contains 4 January)"
	return f"{DOW[start_dow]} start, {POLICIES[policy]}"


def get_week_rule() -> str:
	"""The rule currently configured for the site. ISO unless changed."""
	try:
		chosen = frappe.db.get_single_value(SETTINGS, "week_one_rule")
		policy = RULE_OPTIONS.get(chosen, ISO)
		if policy == ISO:
			return ISO
		return format_rule(policy, frappe.db.get_single_value(SETTINGS, "week_start_day"))
	except Exception:
		# Settings not installed yet (fresh site, or mid-migrate) — ISO is correct.
		return ISO


def _anchor(year: int, policy: str, start_dow: int) -> datetime.date:
	"""First day of week 1 of `year` under a non-ISO rule."""
	seed = datetime.date(int(year), 1, 4 if policy == "4day" else 1)
	return seed - datetime.timedelta(days=(seed.weekday() - start_dow) % 7)


def week_key(d: datetime.date, rule: str | None = None) -> tuple[int, int]:
	"""(week_year, week_number) for a date.

	The week year is not always the calendar year: under most rules the days
	either side of New Year belong to a week owned by the neighbouring year.
	"""
	policy, start_dow = parse_rule(rule)
	if policy == ISO:
		cal = d.isocalendar()
		return cal[0], cal[1]

	y = d.year
	if d >= _anchor(y + 1, policy, start_dow):
		y += 1
	elif d < _anchor(y, policy, start_dow):
		y -= 1
	return y, (d - _anchor(y, policy, start_dow)).days // 7 + 1


def week_start(year: int, week: int, rule: str | None = None) -> datetime.date:
	"""First day of the given week."""
	policy, start_dow = parse_rule(rule)
	if policy == ISO:
		return datetime.date.fromisocalendar(int(year), int(week), 1)
	return _anchor(year, policy, start_dow) + datetime.timedelta(weeks=int(week) - 1)


def week_range(year: int, week: int, rule: str | None = None) -> tuple[datetime.date, datetime.date]:
	start = week_start(year, week, rule)
	return start, start + datetime.timedelta(days=6)


def weeks_in_year(year: int, rule: str | None = None) -> int:
	"""52 or 53. A budget that assumes 52 loses a week of stems in a 53-week year."""
	policy, start_dow = parse_rule(rule)
	if policy == ISO:
		# 28 December always falls in the final ISO week of its year.
		return datetime.date(int(year), 12, 28).isocalendar()[1]
	a = _anchor(year, policy, start_dow)
	b = _anchor(int(year) + 1, policy, start_dow)
	return (b - a).days // 7


def week_label(year: int, week: int, rule: str | None = None) -> str:
	"""'W35 · 24–30 Aug' — what a planner actually needs to see."""
	start, end = week_range(year, week, rule)
	if start.month == end.month:
		span = f"{start.day}–{end.day} {start:%b}"
	else:
		span = f"{start.day} {start:%b} – {end.day} {end:%b}"
	return f"W{week} · {span}"
