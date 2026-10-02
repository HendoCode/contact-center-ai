"""The teacher-labeling loop, run against a fake LLM: resume, retry, no duplicates, and the outputs."""

import json
from collections import Counter

import pytest

from models.finetune import data_gen
from tests.finetune.conftest import FakeTeacher, no_sleep

pytestmark = pytest.mark.filterwarnings("ignore")


def run_label(out_dir, llm, **kw):
    kw.setdefault("workers", 1)
    return data_gen.label(out_dir, llm, sleep=no_sleep, log=lambda _m: None, **kw)


def label_ids(out_dir):
    return [r["call_id"] for r in data_gen.read_jsonl(out_dir / data_gen.TEACHER)]


def test_full_run_labels_train_and_dev_exactly_once(out_dir, teacher):
    stats = run_label(out_dir, teacher, workers=4)
    assert stats["labeled"] == 950 and stats["failed"] == 0 and not stats["aborted"]
    ids = label_ids(out_dir)
    assert len(ids) == len(set(ids)) == 950
    assert len(teacher.calls) == 950


def test_rerun_skips_finished_records_and_adds_no_duplicates(out_dir, teacher):
    run_label(out_dir, teacher, limit=40)
    first = label_ids(out_dir)
    assert len(first) == 40

    again = FakeTeacher(out_dir)
    stats = run_label(out_dir, again, limit=40)  # next 40 pending, not the same 40
    assert stats["skipped"] == 40 and stats["labeled"] == 40
    ids = label_ids(out_dir)
    assert len(ids) == len(set(ids)) == 80 and set(first) <= set(ids)

    final = FakeTeacher(out_dir)
    run_label(out_dir, final)
    assert len(final.calls) == 950 - 80  # only the unfinished calls hit the model
    last = FakeTeacher(out_dir)
    stats = run_label(out_dir, last)
    assert last.calls == [] and stats["labeled"] == 0
    assert len(set(label_ids(out_dir))) == len(label_ids(out_dir)) == 950


def test_interrupted_run_resumes_where_it_stopped(out_dir):
    class Crash(Exception):
        pass

    def script(n, text, gold):
        if n > 30:
            raise Crash("provider down")
        return json.dumps(gold)

    flaky = FakeTeacher(out_dir, script)
    stats = run_label(out_dir, flaky, max_consecutive_failures=5, max_attempts=1)
    assert stats["aborted"] and stats["labeled"] == 30
    assert len(label_ids(out_dir)) == 30

    healthy = FakeTeacher(out_dir)
    run_label(out_dir, healthy)
    assert len(healthy.calls) == 950 - 30
    assert len(set(label_ids(out_dir))) == 950


def test_schema_failure_is_retried_with_the_errors_fed_back(out_dir):
    def script(n, text, gold):
        return "I cannot comply" if n % 2 == 1 else json.dumps(gold)  # every first attempt fails

    llm = FakeTeacher(out_dir, script)
    stats = run_label(out_dir, llm, limit=5)
    assert stats["labeled"] == 5 and stats["failed"] == 0
    assert len(llm.calls) == 10
    assert {r["attempts"] for r in data_gen.read_jsonl(out_dir / data_gen.TEACHER)} == {2}
    retry_messages = llm.calls[1]
    assert "rejected" in retry_messages[-1].content and "no JSON object" in retry_messages[-1].content


def test_invalid_label_is_retried_and_a_hallucinated_value_is_rejected(out_dir):
    def script(n, text, gold):
        if n == 1:  # schema-invalid: bad enum
            return json.dumps({**gold, "resolution": "solved"})
        if n == 2:  # schema-valid but the value is not in the transcript
            bad = json.loads(json.dumps(gold))
            bad["metric_mentioned"][0]["value"] = "$0.01"
            return "```json\n" + json.dumps(bad) + "\n```"
        return json.dumps(gold)

    llm = FakeTeacher(out_dir, script)
    run_label(out_dir, llm, limit=1)
    (record,) = data_gen.read_jsonl(out_dir / data_gen.TEACHER)
    assert record["attempts"] == 3
    assert "verbatim" in llm.calls[2][-1].content


def test_api_errors_are_retried_then_succeed(out_dir):
    def script(n, text, gold):
        if n <= 2:
            raise TimeoutError("slow")
        return json.dumps(gold)

    llm = FakeTeacher(out_dir, script)
    stats = run_label(out_dir, llm, limit=1)
    assert stats["labeled"] == 1
    assert data_gen.read_jsonl(out_dir / data_gen.TEACHER)[0]["attempts"] == 3


def test_exhausted_attempts_are_recorded_and_retried_next_run(out_dir):
    llm = FakeTeacher(out_dir, lambda n, text, gold: "nope")
    stats = run_label(out_dir, llm, limit=3, max_attempts=2, max_consecutive_failures=99)
    assert stats["failed"] == 3 and stats["labeled"] == 0
    assert not (out_dir / data_gen.TEACHER).exists() or label_ids(out_dir) == []
    failures = data_gen.read_jsonl(out_dir / data_gen.FAILURES)
    assert len(failures) == 3 and "2 attempts failed" in failures[0]["error"]

    ok = FakeTeacher(out_dir)
    stats = run_label(out_dir, ok, limit=3)
    assert stats["labeled"] == 3
    assert not (out_dir / data_gen.FAILURES).exists()


def test_run_aborts_after_consecutive_failures(out_dir):
    llm = FakeTeacher(out_dir, lambda n, text, gold: (_ for _ in ()).throw(PermissionError("bad key")))
    stats = run_label(out_dir, llm, max_attempts=1, max_consecutive_failures=3)
    assert stats["aborted"] and stats["failed"] == 3
    assert len(llm.calls) == 3  # it did not burn through the rest of the 950


def test_stale_and_duplicate_rows_are_dropped_on_resume(out_dir, teacher):
    run_label(out_dir, teacher, limit=3)
    path = out_dir / data_gen.TEACHER
    rows = data_gen.read_jsonl(path)
    stale = {**rows[0], "text_sha256": "0" * 64}  # text changed since labeling
    invalid = {**rows[1], "label": {"reason_for_call": "weather"}}
    data_gen.write_jsonl(path, [stale, invalid, rows[2], rows[2]])

    again = FakeTeacher(out_dir)
    run_label(out_dir, again)
    final = data_gen.read_jsonl(path)
    assert len(final) == len({r["call_id"] for r in final}) == 950
    assert len(again.calls) == 950 - 1  # only rows[2] survived as a valid, current label
    texts = {r["call_id"]: r["text"] for r in data_gen.read_jsonl(out_dir / data_gen.GOLD)}
    assert all(r["text_sha256"] == data_gen.sha256_text(texts[r["call_id"]]) for r in final)


def test_finalize_writes_train_dev_review_sample_and_manifest(out_dir, teacher):
    run_label(out_dir, teacher, workers=4)
    manifest = json.loads((out_dir / data_gen.MANIFEST).read_text())
    assert manifest["splits"]["train"]["n"] == 800
    assert manifest["teacher"]["models"] == ["fake-teacher"]
    assert manifest["teacher"]["splits"] == {"train": {"labeled": 800, "missing": 0}, "dev": {"labeled": 150, "missing": 0}}
    agreement = manifest["teacher"]["agreement_with_gold"]
    assert agreement["n"] == 950 and agreement["metric_pair_f1"] == 1.0 and agreement["resolution"] == 1.0
    assert set(manifest["files"]) >= {"gold.jsonl", "splits.json", "train.jsonl", "dev.jsonl", "test.jsonl"}

    train = data_gen.read_jsonl(out_dir / "train.jsonl")
    assert len(train) == 800 and all(r["label"] == r["gold"] for r in train)
    assert all("label" not in r for r in data_gen.read_jsonl(out_dir / "test.jsonl"))

    sample = data_gen.read_jsonl(out_dir / data_gen.REVIEW)
    assert len(sample) == 50 and len({r["call_id"] for r in sample}) == 50
    assert {r["split"] for r in sample} <= {"train", "dev"}
    per_cat = Counter(r["category"] for r in sample)
    assert len(per_cat) == 14 and min(per_cat.values()) >= data_gen.REVIEW_MIN_PER_CATEGORY
    assert sample[0]["review"] == {"teacher_ok": None, "notes": ""}


def test_manifest_reports_teacher_disagreement_with_gold(out_dir):
    def script(n, text, gold):
        wrong = json.loads(json.dumps(gold))
        wrong["resolution"] = "review_needed" if gold["resolution"] != "review_needed" else "action_completed"
        return json.dumps(wrong)

    run_label(out_dir, FakeTeacher(out_dir, script), limit=20)
    agreement = json.loads((out_dir / data_gen.MANIFEST).read_text())["teacher"]["agreement_with_gold"]
    assert agreement["n"] == 20 and agreement["resolution"] == 0.0 and agreement["reason_for_call"] == 1.0


def test_dataset_hash_is_stable_across_reruns_and_changes_with_labels(out_dir, teacher):
    run_label(out_dir, teacher, limit=10)
    first = json.loads((out_dir / data_gen.MANIFEST).read_text())["dataset_sha256"]
    data_gen.finalize(out_dir)
    assert json.loads((out_dir / data_gen.MANIFEST).read_text())["dataset_sha256"] == first
    run_label(out_dir, FakeTeacher(out_dir), limit=10)
    assert json.loads((out_dir / data_gen.MANIFEST).read_text())["dataset_sha256"] != first


def test_review_sample_is_deterministic(out_dir, teacher):
    run_label(out_dir, teacher, workers=4)
    first = (out_dir / data_gen.REVIEW).read_text()
    data_gen.finalize(out_dir)
    assert (out_dir / data_gen.REVIEW).read_text() == first


def test_label_without_build_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="run `build` first"):
        data_gen.label(tmp_path, object())


def test_dry_run_makes_no_api_call(out_dir, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("get_llm must not be called on --dry-run")

    monkeypatch.setattr("rag.pipeline.get_llm", boom)
    assert data_gen.main(["--out", str(out_dir), "label", "--dry-run"]) == 0
    assert "950 calls pending of 950" in capsys.readouterr().out
