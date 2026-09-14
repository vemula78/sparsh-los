# Copyright (c) 2026, SSSIHMS and contributors
# For license information, please see license.txt

"""What the record says a threshold should be, for a person to decide whether it is.

Section 16 is specific: "Mastery thresholds should be empirically calibrated using
performance of currently certified volunteers rather than copied from AI-generated
percentages." The engine shipped with a threshold of two distinct activities, which
nobody chose -- it was the number the first implementation happened to use.

This module reads what learners actually did and reports the distribution. It **never
writes a threshold**. Setting one is the programme owner's act, for the same reason the
engine will not score against an unvalidated rule: a number derived by a script and
applied by the same script has been approved by nobody, and would be indistinguishable
in the database from one he chose.

It also does not declare the data sufficient. Any cut-off for "enough learners" would
itself be an invented threshold -- the exact thing this module exists to avoid. It
reports how many learners it saw and what they did; whether that is enough to calibrate
on is a judgement, and it belongs to the person reading it.
"""

import statistics

import frappe

from sparsh_los.mastery import DEFAULT_ACTIVITIES_FOR_MASTERY, REJECTED
from sparsh_los.permissions import require_reviewer


def _unaided_activity_counts(competency):
	"""Per learner, how many distinct activities they passed unaided in this competency.

	Unaided, not merely passed: the whole assistance model rests on the difference, and
	a threshold calibrated on assisted passes would be calibrated on something else.
	Rejected evidence is excluded in Python -- `db.count` does not ifnull-wrap `!=`, so
	a query would silently drop every row nobody has reviewed.
	"""
	rows = frappe.get_all(
		"Sparsh Evidence",
		filters={
			"competency": competency,
			"outcome": "Pass",
			"assistance_level": 0,
			"critical_error": 0,
			"docstatus": 1,
		},
		fields=["learner", "activity", "human_review_status"],
	)
	per_learner = {}
	for row in rows:
		if row.human_review_status == REJECTED or not row.activity:
			continue
		per_learner.setdefault(row.learner, set()).add(row.activity)
	return {learner: len(activities) for learner, activities in per_learner.items()}


@frappe.whitelist()
def observed(competency=None):
	"""The distribution behind each competency's mastery threshold."""
	require_reviewer()

	competencies = (
		[competency]
		if competency
		else frappe.get_all("Sparsh Competency", pluck="name", order_by="name asc")
	)

	report = []
	for name in competencies:
		counts = _unaided_activity_counts(name)
		values = sorted(counts.values())
		current, source = frappe.db.get_value(
			"Sparsh Competency", name, ["activities_for_mastery", "threshold_source"]
		) or (None, None)

		row = {
			"competency": name,
			"threshold_in_force": current or DEFAULT_ACTIVITIES_FOR_MASTERY,
			"threshold_was_chosen_by": source or None,
			"on_the_uncalibrated_default": not current,
			"learners_with_an_unaided_pass": len(values),
			"distinct_activities_per_learner": values,
			"activities_available": frappe.db.count("Sparsh Activity", {"competency": name}),
		}

		if values:
			row["median"] = statistics.median(values)
			row["range"] = [values[0], values[-1]]
			# What the threshold in force would mean for these learners, which is the
			# question a calibrator is actually asking.
			threshold = row["threshold_in_force"]
			row["would_reach_mastered_at_the_threshold_in_force"] = sum(
				1 for v in values if v >= threshold
			)
		else:
			row["median"] = None
			row["range"] = None
			row["would_reach_mastered_at_the_threshold_in_force"] = 0

		report.append(row)

	return {
		"competencies": report,
		"nothing_is_written": (
			"This report proposes nothing and changes nothing. Section 16 asks that "
			"thresholds be calibrated on what certified volunteers actually did; a number "
			"a script derived and a script applied has been approved by nobody. Set "
			"activities_for_mastery on the competency, and record who chose it and why "
			"in threshold_source."
		),
		"whether_this_is_enough_data": (
			"Not answered here. Any cut-off for 'enough learners' would itself be an "
			"invented threshold. The learner count and the spread are above; whether they "
			"support a change is a programme judgement."
		),
	}
