"""The held-out set, --dataset/--group selection, and compare refusing mixed question sets."""

import json
from types import SimpleNamespace as NS

import pytest

from evals import compare, preflight
from evals.datasets import build_golden
from evals.datasets.holdout_specs import HOLDOUT_SPECS
from evals.run import DATASETS, golden_sha, select_examples


def test_holdout_file_matches_its_derivation():
    assert build_golden.main(["--dataset", "holdout", "--check"]) == 0


def test_golden_file_is_unchanged_by_the_holdout():
    assert build_golden.main(["--check"]) == 0


def test_holdout_shape_and_spread():
    rows = [json.loads(line) for line in build_golden.HOLDOUT_PATH.read_text().splitlines()]
    assert 15 <= len(rows) <= 18
    assert [r["id"] for r in rows] == [f"h{n:02d}" for n in range(1, len(rows) + 1)]
    kinds = {k: sum(r["kind"] == k for r in rows) for k in build_golden.KINDS}
    assert all(n >= 3 for n in kinds.values()), kinds
    golden = [json.loads(line) for line in build_golden.GOLDEN_PATH.read_text().splitlines()]
    assert set(rows[0]) == set(golden[0]), "same schema as the golden set"


def test_holdout_questions_are_new():
    # Compare the questions as asked: a call-lookup template may repeat ("Summarize {call_id}.") but names another call.
    def asked(path):
        return {json.loads(line)["question"].lower() for line in path.read_text().splitlines()}
    assert not asked(build_golden.HOLDOUT_PATH) & asked(build_golden.GOLDEN_PATH)
    assert len(HOLDOUT_SPECS) == len(asked(build_golden.HOLDOUT_PATH))


def test_each_dataset_has_its_own_hash_and_langsmith_name():
    (gp, gprefix), (hp, hprefix) = DATASETS["golden"], DATASETS["holdout"]
    assert golden_sha(gp) != golden_sha(hp)
    assert gprefix == "ccai-agent-golden" and hprefix == "ccai-agent-holdout"


def ex(gid, kind):
    return NS(metadata={"golden_id": gid, "kind": kind})


def test_select_examples_filters_groups_in_id_order_then_limits():
    pool = [ex("g40", "open"), ex("g02", "ambiguous"), ex("g39", "open"), ex("g30", "call_lookup")]
    assert [e.metadata["golden_id"] for e in select_examples(pool, ["open"], None)] == ["g39", "g40"]
    assert [e.metadata["golden_id"] for e in select_examples(pool, ["open", "call_lookup"], 2)] == ["g30", "g39"]


def test_estimate_counts_only_the_chosen_set_and_groups():
    golden = build_golden.GOLDEN_PATH
    assert preflight.golden_count(golden) == 51
    assert preflight.golden_count(golden, ["open"]) == 12
    assert preflight.golden_count(build_golden.HOLDOUT_PATH, ["open"]) == 4


def record(dataset=None, groups=None, judge=0.5):
    return {"date": "2026-10-05", "git_sha": "abc1234", "area": "evals", "run": "r", "hardware": "t",
            "versions": {"python": "3.12", "agent_model": "a", "judge_model": "j"},
            "params": {"eval_dataset": dataset, "groups": groups, "judge_prompt": "v1",
                       "golden_sha256": "x" * 64, "limit": None},
            "metrics": {"open": {"n": 12, "judge": judge}}, "notes": ""}


def test_compare_refuses_golden_against_holdout():
    with pytest.raises(compare.DifferentDatasets, match="refusing: A ran the golden set and B the holdout set"):
        compare.compare(record(), record("holdout"))


def test_compare_can_print_mixed_sets_with_a_loud_banner():
    out = compare.compare(record(), record("holdout"), allow_datasets=True)
    assert out.startswith("!!! DIFFERENT QUESTION SETS (golden vs holdout)")


def test_runs_before_the_dataset_option_count_as_golden():
    assert "refusing" not in compare.compare(record(None), record("golden", judge=0.7))


def test_compare_warns_when_groups_differ():
    out = compare.compare(record(), record("golden", ["open"], 0.7))
    assert ("WARNING: groups differs (all -> open): compare only the tables of groups both runs scored; "
            "'all' is not comparable") in out
    assert "+0.20" in out
