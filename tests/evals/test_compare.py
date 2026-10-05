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


def test_main_defaults_b_to_latest(tmp_path, monkeypatch, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps(BEFORE))
    b.write_text(json.dumps(AFTER))
    (tmp_path / "LATEST").write_text("b.json\n")
    monkeypatch.setattr(compare, "LATEST_PATH", tmp_path / "LATEST")
    assert compare.main([str(a)]) == 0
    assert "+0.90" in capsys.readouterr().out


def test_non_evals_file_is_refused(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({**BEFORE, "area": "retrieval"}))
    with pytest.raises(SystemExit, match="not an evals result"):
        compare.load(f)
