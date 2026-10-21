"""Recover variety and year on Production Projection from the document name.

Every Production Projection on the migrated sites carries variety NULL and
projection_year 0, even though both are mandatory on the doctype -- the records
were loaded around validation. The values were not lost, only misplaced: the
document NAME holds them, as "{year}-{greenhouse}-{variety}", e.g.
"2025-Chepsito GH 01 - KR-Athena".

Without them nothing can be matched to a harvest: actuals are read per
greenhouse x variety, so a NULL variety means a projection can never show an
actual, whatever the stock entries say.

The greenhouse field is already populated, which is what makes the name
splittable: everything before it is the year, everything after is the variety.
A trailing planting year on the variety ("Snowstorm-2019", "Ever Red-2023-2")
is stripped, and the remainder is matched against Item exactly, then by
case and punctuation. Anything still unmatched is left alone and listed, so a
bad guess is never written.
"""

import re

import frappe


def _norm(s):
	return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def execute():
	items = frappe.get_all("Item", pluck="name")
	by_norm = {}
	for n in items:
		by_norm.setdefault(_norm(n), n)
	exact = set(items)

	rows = frappe.get_all(
		"Production Projection",
		filters={"variety": ("in", (None, ""))},
		fields=["name", "greenhouse", "projection_year"],
		limit_page_length=0,
	)
	fixed, unresolved = 0, []

	for r in rows:
		name, gh = r["name"], r["greenhouse"] or ""
		variety, year = None, None

		if gh and gh in name:
			pre, _, post = name.partition(gh)
			variety = post.strip("-").strip() or None
			m = re.match(r"^(\d{4})", pre.strip("-"))
			year = int(m.group(1)) if m else None
		if not variety:
			tail = name.strip("-").split("-")[-1].strip()
			variety = tail or None

		# "Snowstorm-2019" and "Ever Red-2023-2" are a variety plus the year it
		# was planted; the projection already has its own year column.
		if variety:
			variety = re.sub(r"[-\s]+\d{4}(?:[-\s]+\d+)?$", "", variety).strip()

		resolved = None
		if variety:
			if variety in exact:
				resolved = variety
			elif _norm(variety) in by_norm:
				resolved = by_norm[_norm(variety)]

		if not resolved:
			unresolved.append((name, variety))
			continue

		update = {"variety": resolved}
		if year and not int(r.get("projection_year") or 0):
			update["projection_year"] = year
		frappe.db.set_value("Production Projection", name, update, update_modified=False)
		fixed += 1

	# Production Projection is not a submittable doctype, yet the imported rows
	# carry docstatus 1. Frappe then refuses every field change on them ("Not
	# allowed to change ... after submission"), so the projections are frozen:
	# no revision can be typed and no actual can be written. docstatus has no
	# meaning on a doctype without submit, so normalise it back to draft.
	frozen = 0
	if not frappe.get_meta("Production Projection").is_submittable:
		frozen = frappe.db.sql("""
			UPDATE `tabProduction Projection` SET docstatus = 0 WHERE docstatus = 1
		""").__class__ and frappe.db.sql(
			"SELECT ROW_COUNT()")[0][0]

	frappe.db.commit()
	print("Production Projection: variety recovered on %d of %d" % (fixed, len(rows)))
	if frozen:
		print("Production Projection: %d rows released from docstatus 1 "
		      "(the doctype is not submittable)" % frozen)
	if unresolved:
		print("still unresolved (%d) - left untouched:" % len(unresolved))
		for n, guess in unresolved[:25]:
			print("   %-52s guessed %r" % (n[:52], guess))
