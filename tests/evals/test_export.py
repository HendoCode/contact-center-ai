"""evals.export against a fake LangSmith client: per-item outputs, scores and judge comments."""

import json
from types import SimpleNamespace as NS

import pytest

from evals import export


class FakeClient:
    def __init__(self, runs, feedback, examples):
        self.runs, self.feedback, self.examples = runs, feedback, examples
        self.calls = []

    def list_runs(self, project_name, is_root):
        self.calls.append(("list_runs", project_name, is_root))
        return iter(self.runs)

    def list_feedback(self, run_ids):
        return iter(f for f in self.feedback if f.run_id in run_ids)

    def read_example(self, example_id):
        return self.examples[example_id]


def fake():
    runs = [
        NS(id="r2", reference_example_id="e2", inputs={"question": "What is the call volume?"}, error=None,
           outputs={"answer": "Call volume is 1,250.", "route": "ask_the_analyst",
                    "metric_names": ["call_volume"], "sql": "SELECT count(*) FROM calls", "citations": [],
                    "grounded": True, "messages": ["not exported"]}),
        NS(id="r1", reference_example_id="e1", inputs={"question": "rate?"}, error="TimeoutError: x",
           outputs=None),
    ]
    feedback = [
        NS(run_id="r2", key="sql", score=False, comment="answer text lacks the SQL"),
        NS(run_id="r2", key="judge", score=0.25, comment="Terse: gives a number with no explanation."),
        NS(run_id="r1", key="judge", score=0.0, comment="Error text from /home/someone/repo/olap/dbt."),
        NS(run_id="r1", key="route", score=True, comment=None),
    ]
    examples = {"e1": NS(metadata={"golden_id": "g01", "kind": "ambiguous"}),
                "e2": NS(metadata={"golden_id": "g12", "kind": "metric"})}
    return FakeClient(runs, feedback, examples)


def test_export_has_every_item_with_outputs_scores_and_judge_reasoning():
    client = fake()
    data = export.export(client, "agent-golden-openai-74042631")
    assert client.calls == [("list_runs", "agent-golden-openai-74042631", True)]
    assert data["n"] == 2
    first, second = data["items"]
    assert first == {"id": "g01", "kind": "ambiguous", "inputs": {"question": "rate?"},
                     "error": "TimeoutError: x",
                     "tool_error": None,
                     "outputs": dict.fromkeys(("answer", "route", "metric_names", "sql", "citations", "grounded")),
                     "results": {"judge": {"key": "judge", "score": 0.0, "comment": "Error text from ~/repo/olap/dbt."},
                                 "route": {"key": "route", "score": True, "comment": ""}}}
    assert second["id"] == "g12"
    assert second["outputs"]["sql"] == "SELECT count(*) FROM calls"
    assert "messages" not in second["outputs"]
    assert second["results"]["judge"] == {"key": "judge", "score": 0.25,
                                          "comment": "Terse: gives a number with no explanation."}


def test_main_writes_the_file_and_never_overwrites(tmp_path, capsys):
    out = tmp_path / "baseline.json"
    assert export.main(["exp-1", "--out", str(out)], client=fake()) == 0
    assert json.loads(out.read_text())["experiment"] == "exp-1"
    assert "wrote" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="exists"):
        export.main(["exp-1", "--out", str(out)], client=fake())


def test_unknown_experiment_is_one_clear_line():
    with pytest.raises(SystemExit, match="has no runs"):
        export.export(FakeClient([], [], {}), "nope")


def test_default_output_stays_out_of_the_rendered_results(monkeypatch, tmp_path):
    # results/evals/items/, never results/evals/*.json, which tools.results renders as run records
    assert export.ITEMS_DIR.relative_to(export.REPO_ROOT).as_posix() == "results/evals/items"
    monkeypatch.setattr(export, "ITEMS_DIR", tmp_path / "items")
    assert export.main(["exp-2"], client=fake()) == 0
    assert (tmp_path / "items" / "exp-2.json").exists()
