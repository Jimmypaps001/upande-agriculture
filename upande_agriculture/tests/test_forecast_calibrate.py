"""A simulated rose crop with a known timing: buds appear in sharp 6-week flushes, each takes K_TRUE x ~28 days (normal spread) from rice stage to
the knife, plot counts report each bud's stage from its remaining days, and
picks are the buds reaching harvest. Calibration must recover the timing and
forecast the picks well. (No deaths and no regrowth in the simulation, so
only timing and forecast error are asserted.)"""
import json
import math

from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, getdate, nowdate

from upande_agriculture.forecast import calibrate
from upande_agriculture.forecast.curves import day_mass
from upande_agriculture.tests.forecast_fixtures import (
	make_count, make_greenhouse, make_harvest, make_plot, make_variety)

K_TRUE = 0.9
PLANTS, PLOT = 1000, 10
# Remaining days (at timing 1.0) at which a bud moves into each stage: the
# midpoints between the default stages' days to harvest (3, 8, 15, 22, 28).
EDGES = [("Opening", 5.5), ("Showing colour", 11.5), ("Chickpea", 18.5), ("Pea", 25.0), ("Rice", 1e9)]
DURATIONS = {d: day_mass(d, K_TRUE * 28, K_TRUE * 5.6) for d in range(5, 60)}


def births(day):  # new rice-stage buds per plant per day: sharp 6-week flushes (mean 0.1)
	return 0.1 * (1 + math.sin(2 * math.pi * day / 42)) ** 4 / 4.375


def stage(remaining):
	for name, edge in EDGES:
		if remaining / K_TRUE <= edge:
			return name


def counts_on(c):
	"""Expected buds per plant by stage on day c (days are ints)."""
	out = {}
	for b in range(c - 60, c + 1):
		for d, m in DURATIONS.items():
			if b + d > c:
				name = stage(b + d - c)
				out[name] = out.get(name, 0) + births(b) * m
	return out


def picks_on(t):
	return PLANTS * sum(births(t - d) * m for d, m in DURATIONS.items())


class TestForecastCalibrate(FrappeTestCase):
	def test_too_little_data_keeps_defaults(self):
		make_variety("FC-THIN")
		make_greenhouse("FC THIN", (("S1", 1, 9, "FC-THIN", 1000),))
		doc = calibrate.calibrate("FC-THIN")
		self.assertFalse(doc.fitted_on)
		self.assertIn("Not enough data", doc.status)

	def test_recovers_timing_from_a_simulated_crop(self):
		make_variety("FC-CAL")
		gh = make_greenhouse("FC CAL", (("S1", 1, 9, "FC-CAL", PLANTS),))
		plot = make_plot(gh, "S1")
		today = getdate(nowdate())
		for i in range(-90, 0):  # day index relative to today
			day = add_days(today, i)
			if i % 3 == 0:
				make_count(plot, day, {k: round(v * PLOT) for k, v in counts_on(i).items()})
			make_harvest(gh, "S1", "FC-CAL", day, max(1, round(picks_on(i))))
		doc = calibrate.calibrate("FC-CAL", today)
		self.assertTrue(doc.fitted_on, doc.status)
		self.assertAlmostEqual(doc.time_scale, K_TRUE, delta=0.051)
		bands = json.loads(doc.error_bands)
		self.assertLess(bands["3-6"][2], 0.2)  # weighted abs % error (WAPE), 3-day sums, 3-6 days ahead
