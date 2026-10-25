"""Golden change-analysis questions: the G rows of the question contract table in
docs/design/change-analysis.md (A.6), copied verbatim. Only events with `split: golden` in
data/synthetic/events.yml may appear here; `build_change` refuses anything else.

Each spec holds what the contract fixes by hand:

- `event`: the change-log name of the event the question is about (never an answer-key id);
- `metric`, `filters`, `change_point_source` ("logged", "period" or "detected"): the contract.
  A follow-up (`follow_up_of`) takes all three from the row it follows, so they are None here;
- `evaluators`: the checks beyond the ones every change row gets (BASE_EVALUATORS);
- `frame`: what the `frame` node's model should read from the question. Offline,
  `evals.offline.ScriptedClassifier` answers with it; a live run lets the model decide.

`python -m evals.datasets.build_change` derives the rest (the change point, the reference and
its derivation) from the ground-truth file and writes change_golden.jsonl.
"""

BASE_EVALUATORS = ["route", "interrupt", "metric", "sql", "citations", "no_agent_ids"]
DRIVER_EVALUATORS = ["change_point", "before_after", "driver_recall", "driver_share"]

FEE_CALLS = {"category": ["fee_dispute"]}
CARD_CALLS = {"category": ["card_services", "fraud_dispute"]}


def _frame(measure=None, subject=None, when=None, logged_change=None, follow_up=False) -> dict:
    return {"measure": measure, "subject": subject, "when": when,
            "logged_change": logged_change, "follow_up": follow_up}


CHANGE_SPECS: list[dict] = [
    {"id": "G1", "event": "Fee schedule change",
     "question": "Why did satisfaction on fee calls drop after the fee schedule change?",
     "follow_up_of": None, "metric": "csat_average_score", "filters": FEE_CALLS,
     "change_point_source": "logged", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame("satisfaction", "fee calls", logged_change="Fee schedule change")},
    {"id": "G2", "event": "Fee schedule change",
     "question": "What are members saying about fees since April?",
     "follow_up_of": None, "metric": "call_volume", "filters": FEE_CALLS,
     "change_point_source": "period", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame(subject="fees", when="since April")},
    {"id": "G3", "event": "Fee schedule change",
     "question": "What should supervisors coach on for fee calls?",
     "follow_up_of": "G1", "metric": None, "filters": None,
     "change_point_source": None, "expect_interrupt": False,
     "evaluators": ["pattern_recall"],
     "frame": _frame(follow_up=True)},
    {"id": "G4", "event": "Card fraud rule change",
     "question": "Why did card calls spike in June?",
     "follow_up_of": None, "metric": "call_volume", "filters": CARD_CALLS,
     "change_point_source": "period", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame("calls", "card calls", when="in June")},
    {"id": "G5", "event": "Card fraud rule change",
     "question": "What are members upset about with declined cards since the fraud rule change?",
     "follow_up_of": None, "metric": "call_volume", "filters": CARD_CALLS,
     "change_point_source": "logged", "expect_interrupt": False,
     "evaluators": DRIVER_EVALUATORS,
     "frame": _frame(subject="declined cards", logged_change="Card fraud rule change")},
    {"id": "G6", "event": "Card fraud rule change",
     "question": "Did satisfaction on card calls recover after the fraud rule change?",
     "follow_up_of": None, "metric": "csat_average_score", "filters": CARD_CALLS,
     "change_point_source": "logged", "expect_interrupt": False,
     "evaluators": ["change_point", "before_after", "weekly", "driver_recall"],
     "frame": _frame("satisfaction", "card calls", logged_change="Card fraud rule change")},
]
