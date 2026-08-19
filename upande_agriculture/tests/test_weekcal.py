import datetime
import unittest

from upande_agriculture import weekcal as wc


class TestWeekCal(unittest.TestCase):
	def test_generic_math_reproduces_iso_exactly(self):
		"""The Monday/4-day rule IS ISO, so the generic path must agree with stdlib.

		This is the load-bearing test: every week number written before the rule
		became configurable means ISO, so if the generic path drifts from
		isocalendar() by even a day the stamped rules reinterpret history.
		"""
		d = datetime.date(2020, 1, 1)
		end = datetime.date(2032, 12, 31)
		while d <= end:
			self.assertEqual(
				wc.week_key(d, "4day:mon"), d.isocalendar()[:2],
				f"generic week_key disagrees with ISO on {d}",
			)
			d += datetime.timedelta(days=1)

	def test_weeks_in_year_matches_iso(self):
		for y in range(2015, 2041):
			self.assertEqual(
				wc.weeks_in_year(y, "4day:mon"), wc.weeks_in_year(y, wc.ISO), f"year {y}"
			)

	def test_2026_has_53_weeks(self):
		# A budget loop stopping at 52 silently drops a week of stems.
		self.assertEqual(wc.weeks_in_year(2026, wc.ISO), 53)

	def test_known_iso_ranges(self):
		self.assertEqual(
			wc.week_range(2026, 35, wc.ISO),
			(datetime.date(2026, 8, 24), datetime.date(2026, 8, 30)),
		)
		# 7 Aug 2026 is week 32, not 35 — the reason weeks need date labels.
		self.assertEqual(wc.week_key(datetime.date(2026, 8, 7), wc.ISO), (2026, 32))

	def test_sunday_start_diverges_from_iso(self):
		# 4 Jan 2026 is a Sunday: ISO still calls it week 1, a Sunday-start
		# calendar has already rolled into week 2.
		d = datetime.date(2026, 1, 4)
		self.assertEqual(wc.week_key(d, wc.ISO), (2026, 1))
		self.assertEqual(wc.week_key(d, "jan1:sun"), (2026, 2))

	def test_week_key_and_week_start_round_trip(self):
		for rule in (wc.ISO, "jan1:mon", "jan1:sun", "4day:sun"):
			for y in (2025, 2026, 2027):
				for w in range(1, wc.weeks_in_year(y, rule) + 1):
					start = wc.week_start(y, w, rule)
					self.assertEqual(
						wc.week_key(start, rule), (y, w),
						f"round trip failed for {rule} {y}-W{w}",
					)

	def test_every_day_of_a_week_shares_one_key(self):
		for rule in (wc.ISO, "jan1:sun"):
			start = wc.week_start(2026, 10, rule)
			keys = {wc.week_key(start + datetime.timedelta(days=i), rule) for i in range(7)}
			self.assertEqual(keys, {(2026, 10)}, rule)

	def test_year_boundary_days_belong_to_neighbouring_week_year(self):
		# 30 Dec 2025 falls in a week owned by 2026 under "week 1 contains Jan 1".
		self.assertEqual(wc.week_key(datetime.date(2025, 12, 30), "jan1:mon"), (2026, 1))

	def test_parse_and_format_round_trip(self):
		self.assertEqual(wc.parse_rule("jan1:sun"), ("jan1", 6))
		self.assertEqual(wc.parse_rule("4day:mon"), ("4day", 0))
		self.assertEqual(wc.format_rule("jan1", "Sunday"), "jan1:sun")
		self.assertEqual(wc.format_rule(wc.ISO), wc.ISO)

	def test_unknown_or_missing_rule_falls_back_to_iso(self):
		for bad in (None, "", "nonsense", "weird:xyz"):
			self.assertEqual(wc.parse_rule(bad)[0], wc.ISO, repr(bad))
		# An unknown day is Monday, not a crash.
		self.assertEqual(wc.parse_rule("jan1:zzz"), ("jan1", 0))

	def test_week_label_reads_naturally(self):
		self.assertEqual(wc.week_label(2026, 35, wc.ISO), "W35 · 24–30 Aug")
		# A week that straddles two months names both.
		self.assertEqual(wc.week_label(2026, 31, wc.ISO), "W31 · 27 Jul – 2 Aug")
