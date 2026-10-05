"""Result files never overwrite, LATEST names the newest, and evals.compare's before/after table."""

import json
from datetime import UTC, datetime

import pytest

from evals import compare
from evals.run import result_path, write_record

NOW = datetime(2026, 10, 5, 4, 30, 7, tzinfo=UTC)


def record(agent="z-ai/glm-5.3-flash", judge="anthropic/claude-opus-5.5", prompt="judge_answer_v1",
           golden="a" * 64, limit=None, **groups):
    metrics = {g: {"n": n, "sql": sql, "judge": jd} for g, (n, sql, jd) in groups.items()}
    metrics["errors"] = 0
    return {"date": "2026-10-05", "git_sha": "abc1234def", "area": "evals", "run": "agent-golden-openai",
            "hardware": "test", "versions": {"python": "3.12", "agent_model": agent, "judge_model": judge},
            "params": {"judge_prompt": prompt, "golden_sha256": golden, "limit": limit},
            "metrics": metrics, "notes": ""}


BEFORE = record(all=(51, 0.0, 0.34), metric=(18, 0.0, 0.19))
AFTER = record(all=(51, 0.9, 0.61), metric=(18, 1.0, 0.55))


def test_result_names_are_unique_and_never_overwrite(tmp_path):
    first = result_path("agent-golden-openai", "abc1234def", NOW, tmp_path)
    assert first.name == "2026-10-05T043007Z_agent-golden-openai_abc1234.json"
    write_record(BEFORE, first)
    second = result_path("agent-golden-openai", "abc1234def", NOW, tmp_path)
    assert second.name == "2026-10-05T043007Z_agent-golden-openai_abc1234-2.json"
    write_record(AFTER, second)
    assert json.loads(first.read_text()) == BEFORE  # the first run survived
    assert (tmp_path / "LATEST").read_text() == second.name + "\n"
    with pytest.raises(FileExistsError):
        write_record(AFTER, first)


def test_compare_table_has_both_sides_and_deltas():
    out = compare.compare(BEFORE, AFTER, "before.json", "after.json")
    assert "WARNING" not in out
    assert "all  (n 51 -> 51)" in out
    assert "  sql           0.00    0.90   +0.90" in out
    assert "  judge         0.19    0.55   +0.36" in out
    assert "agent model   z-ai/glm-5.3-flash" in out


def test_unchanged_check_reads_equal():
    out = compare.compare(BEFORE, BEFORE)
    assert "  judge         0.34    0.34       =" in out


def test_changed_judge_warns_that_judge_scores_are_incomparable():
    out = compare.compare(BEFORE, record(judge="openai/gpt-5.5", all=(51, 0.0, 0.5)))
    assert ("WARNING: judge model differs (anthropic/claude-opus-5.5 -> openai/gpt-5.5): "
            "judge scores are not comparable") in out


def test_changed_agent_is_flagged_as_what_the_delta_measures():
    out = compare.compare(BEFORE, record(agent="other/model", all=(51, 0.0, 0.34)))
    assert "WARNING: agent model differs (z-ai/glm-5.3-flash -> other/model): the delta measures this change" in out


def test_changed_limit_or_golden_set_makes_everything_incomparable():
    out = compare.compare(BEFORE, record(limit=5, golden="b" * 64, all=(5, 0.0, 0.3)))
    assert "WARNING: golden set differs (aaaaaaaaaaaa -> bbbbbbbbbbbb): every score is incomparable" in out
    assert "WARNING: limit differs (none -> 5): every score is incomparable" in out


def test_group_on_one_side_only_shows_n_a():
    out = compare.compare(BEFORE, record(all=(51, 0.5, 0.5)))
    assert "metric  (n 18 -> 0)" in out
    assert "  sql           0.00     n/a" in out


def write_runs(tmp_path, monkeypatch, names):
    """Timestamped results files, one minute apart, in run order; RESULTS_DIR points at them."""
    out = []
    for n, (name, rec) in enumerate(names):
        p = tmp_path / f"2026-10-05T15{n:02d}00Z_{name}_abc1234.json"
        p.write_text(json.dumps(rec))
        out.append(p)
    monkeypatch.setattr(compare, "RESULTS_DIR", tmp_path)
    return out


def test_run_names_resolve_to_their_newest_file(tmp_path, monkeypatch):
    files = write_runs(tmp_path, monkeypatch, [("open-pgvector-vector", BEFORE), ("open-lance-hybrid", AFTER),
                                               ("open-pgvector-vector", BEFORE)])
    assert compare.resolve("open-pgvector-vector") == files[2]
    assert compare.resolve("open-lance-hybrid") == files[1]
    assert compare.resolve("latest") == files[2]
    assert compare.resolve("latest~2") == files[0]
    assert compare.resolve(str(files[0])) == files[0]
    assert compare.resolve(files[0].name) == files[0]


def test_main_by_name_prints_what_it_resolved_and_defaults_b_to_latest(tmp_path, monkeypatch, capsys):
    files = write_runs(tmp_path, monkeypatch, [("before-run", BEFORE), ("after-run", AFTER)])
    assert compare.main(["before-run"]) == 0
    out = capsys.readouterr().out
    assert f"A: before-run -> {files[0]}" in out and f"B: latest -> {files[1]}" in out
    assert "+0.90" in out


def test_unknown_name_lists_the_runs(tmp_path, monkeypatch):
    write_runs(tmp_path, monkeypatch, [("open-pgvector-vector", BEFORE), ("open-lance-hybrid", AFTER)])
    with pytest.raises(SystemExit, match="no run named 'open-lance'; runs: open-lance-hybrid, open-pgvector-vector"):
        compare.resolve("open-lance")


def test_same_minute_runs_are_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "RESULTS_DIR", tmp_path)
    for name in ("2026-10-05T150001Z_x_abc1234.json", "2026-10-05T150059Z_x_def5678.json"):
        (tmp_path / name).write_text(json.dumps(BEFORE))
    with pytest.raises(SystemExit, match="more than one run in the same minute"):
        compare.resolve("x")


def test_latest_beyond_the_runs_is_one_line(tmp_path, monkeypatch):
    write_runs(tmp_path, monkeypatch, [("only", BEFORE)])
    with pytest.raises(SystemExit, match=r"latest~3: only 1 run\(s\)"):
        compare.resolve("latest~3")


def test_non_evals_file_is_refused(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({**BEFORE, "area": "retrieval"}))
    with pytest.raises(SystemExit, match="not an evals result"):
        compare.load(f)
