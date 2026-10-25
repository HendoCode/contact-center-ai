"""Held-out change-analysis questions: the H rows of the question contract table in
docs/design/change-analysis.md (A.6), copied verbatim, about the two events with
`split: heldout` in data/synthetic/events.yml.

These events measure whether change analysis generalizes to event types it was never tuned
on. They have no golden rows, no prompt examples and no fixtures, and nothing is tuned
against them: a fix prompted by a held-out failure must apply unchanged to the golden events
and be justified by golden or unit evidence. A failing held-out row is reported, not patched.

Same spec format as CHANGE_SPECS in change_specs.py. Do not edit a question after its scores
are seen; a factual error may be fixed, recorded here with its date and reason.

Fixes: none.
"""

from evals.datasets.change_specs import DRIVER_EVALUATORS, _frame

HOLDOUT_SPECS: list[dict] = [
    {"id": "H1", "event": "Mobile app version 5 release",
     "question": "Why did online banking calls go up in late July?",
     "follow_up_of": None, "metric": "call_volume",
     "filters": {"category": ["online_banking_support"]},
     "change_point_source": "period", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame("calls", "online banking calls", when="late July")},
    {"id": "H2", "event": "Mobile app version 5 release",
     "question": "What changed for members after the app update?",
     # No subject: the tool keeps the categories whose volume rose, so no filter is fixed.
     "follow_up_of": None, "metric": "call_volume", "filters": None,
     "change_point_source": "logged", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame(logged_change="Mobile app version 5 release")},
    {"id": "H3", "event": "Westside branch closure",
     "question": "Why is satisfaction down for Westside members?",
     "follow_up_of": None, "metric": "csat_average_score",
     "filters": {"home_branch": ["Westside"]},
     "change_point_source": "detected", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame("satisfaction", "Westside members")},
    {"id": "H4", "event": "Westside branch closure",
     "question": "What do members need since the Westside branch closed?",
     "follow_up_of": None, "metric": "call_volume",
     "filters": {"home_branch": ["Westside"]},
     "change_point_source": "logged", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame(subject="Westside branch", logged_change="Westside branch closure")},
]
