"""agent.demo_change: the six scripted steps against a fake model and stub tools, and the
preflight's fixes. Offline: no Postgres, no Ollama, no network."""

import pytest
from agent_support import (
    NO_FRAME,
    FakeLLM,
    StubToolbox,
    call_exists,
    change_result,
    coaching_result,
    tool_text,
)
from langgraph.checkpoint.memory import InMemorySaver

from agent import demo_change
from agent.demo_change import STEPS, Check, run_steps
from agent.graph import build_graph
from retrieval.base import Hit

pytestmark = pytest.mark.usefixtures("change_files")

QUOTE = "Nobody told me the fee went up."


class ScriptedModel(FakeLLM):
    """Routes and frames each demo question the way a good model would."""

    def with_structured_output(self, schema):
        from langchain_core.runnables import RunnableLambda

        def respond(prompt):
            question = prompt.rsplit("Question:", 1)[1].strip()
            if "route" in schema.model_fields:
                route = "resolve_metric" if "make on fees" in question else "explain_change"
                return schema(route=route)
            frames = {
                "satisfaction": {"measure": "satisfaction", "subject": "fee calls",
                                 "logged_change": "Fee schedule change"},
                "coach": {"follow_up": True},
                "online banking": {"subject": "online banking calls", "when": "late July"},
            }
            frame = next(v for k, v in frames.items() if k in question)
            return schema(**{**NO_FRAME, **frame})

        return RunnableLambda(respond)


class DemoToolbox(StubToolbox):
    """The stub tools, with the call-volume answer for the online-banking question."""

    async def call(self, name: str, arguments: dict) -> str:
        if name == "explain_change" and arguments.get("metric") == "call_volume":
            self.calls.append((name, arguments))
            return tool_text(change_result(metric="call_volume",
                                           filters={"category": ["online_banking_support"]}))
        if name == "explain_change" and arguments.get("mode") == "coaching":
            self.calls.append((name, arguments))
            return tool_text(coaching_result())
        return await super().call(name, arguments)


def _graph(toolbox):
    return build_graph(get_llm=lambda: ScriptedModel(), toolbox=toolbox, call_exists=call_exists,
                       checkpointer=InMemorySaver())


def _fetch(text: str = f"MEMBER: Hello. AGENT: Hi. MEMBER: {QUOTE} AGENT: Sorry."):
    return lambda call_id: Hit(call_id, text, 1.0, {"date": "2026-04-20", "category": "fee_dispute",
                                                    "outcome": "resolved"})


# ── the script ───────────────────────────────────────────────────────────────

def test_the_script_is_the_six_acceptance_questions_in_order():
    assert [s.kind for s in STEPS] == ["ask", "ask", "ask", "ask", "citation", "mcp"]
    assert STEPS[0].question == STEPS[5].question  # the MCP client asks step 1's question
    assert [s.label for s in STEPS if s.interrupt] == ["which fee? (clarify, then answer)"]
    assert STEPS[0].thread == STEPS[2].thread  # the coaching follow-up reuses step 1's evidence
    assert len({STEPS[i].thread for i in (0, 1, 3)}) == 3


def test_the_mcp_step_sends_the_logged_date_and_the_question(monkeypatch):
    monkeypatch.setattr(demo_change, "load_change_log",
                        lambda: [{"name": demo_change.FEE_CHANGE, "date": "2026-04-15"}])
    arguments = demo_change._mcp_arguments()
    assert arguments["change_point"] == "2026-04-15"
    assert arguments["metric"] == "csat_average_score"
    assert arguments["question"] == demo_change.FEE_QUESTION


@pytest.mark.asyncio
async def test_all_six_steps_run_as_scripted_and_each_is_timed(capsys, monkeypatch):
    monkeypatch.setattr(demo_change, "load_change_log",
                        lambda: [{"name": demo_change.FEE_CHANGE, "date": "2026-04-15"}])
    toolbox = DemoToolbox()
    outcomes, failures = await run_steps(_graph(toolbox), toolbox, _fetch(), STEPS)

    out = capsys.readouterr().out
    assert failures == []
    assert [o.mismatches for o in outcomes] == [[]] * 6
    assert out.count("  time:     ") == 6
    assert "INTERRUPT:" in out and "overdraft_fee_revenue" in out
    assert "Coaching patterns across calls" in out
    assert "citation: CALL-00001" in out and f">> MEMBER: {QUOTE}" in out
    assert "masked to its last four digits" in out
    assert "same numbers as step 1" in out
    assert "MISMATCH" not in out
    coaching = [a for a in toolbox.tool_calls("explain_change") if a.get("mode") == "coaching"]
    assert len(coaching) == 1  # the follow-up reused the thread's analysis


@pytest.mark.asyncio
async def test_an_unmasked_account_number_is_reported(capsys):
    toolbox = DemoToolbox()
    leaky = _fetch(f"MEMBER: {QUOTE} AGENT: Account 123456789 is on file.")
    outcomes, _ = await run_steps(_graph(toolbox), toolbox, leaky, STEPS)

    assert outcomes[4].mismatches == [
        "an account number is not masked to its last four digits: 123456789"]
    assert "$1,234,567.89" not in demo_change.UNMASKED_ACCOUNT.findall("$1,234,567.89 ending 0531")


@pytest.mark.asyncio
async def test_a_misrouted_step_is_a_mismatch_not_an_error(capsys):
    class AlwaysRetrieve(ScriptedModel):
        def with_structured_output(self, schema):
            inner = super().with_structured_output(schema)
            if "route" in schema.model_fields:
                from langchain_core.runnables import RunnableLambda
                return RunnableLambda(lambda prompt: schema(route="retrieve"))
            return inner

    toolbox = DemoToolbox()
    graph = build_graph(get_llm=lambda: AlwaysRetrieve(), toolbox=toolbox, call_exists=call_exists,
                        checkpointer=InMemorySaver())
    outcomes, failures = await run_steps(graph, toolbox, _fetch(), STEPS[:1])

    assert failures == []
    assert outcomes[0].mismatches == ["routed to retrieve, scripted route is explain_change"]
    assert "MISMATCH: routed to retrieve" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_step_that_raises_is_a_failure_and_the_run_goes_on(capsys):
    class Broken(DemoToolbox):
        async def call(self, name: str, arguments: dict) -> str:
            if name == "explain_change" and "mode" not in arguments:
                raise ConnectionError("database down")
            return await super().call(name, arguments)

    toolbox = Broken()
    outcomes, failures = await run_steps(_graph(toolbox), toolbox, _fetch(), STEPS[:2])

    assert len(failures) == 1 and "step 1" in failures[0] and "database down" in failures[0]
    assert outcomes[0].error and not outcomes[1].error
    assert outcomes[1].mismatches == []


# ── the preflight ────────────────────────────────────────────────────────────

def test_models_on_ollama_name_the_pull_command(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(demo_change, "_ollama_models", lambda base: {"llama3.2:latest"})

    chat, embeddings = demo_change.check_models()

    assert chat.ok and not embeddings.ok
    assert "ollama pull nomic-embed-text" in embeddings.fix


def test_an_unreachable_ollama_fails_both_roles_with_one_fix(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")

    def refuse(base):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(demo_change, "_ollama_models", refuse)
    checks = demo_change.check_models()

    assert [c.ok for c in checks] == [False, False]
    assert all("make up" in c.fix for c in checks)


def test_a_hosted_chat_model_needs_its_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(demo_change, "_ollama_models",
                        lambda base: {"nomic-embed-text:latest"})

    chat = demo_change.check_models()[0]

    assert not chat.ok and "OPENAI_API_KEY" in chat.detail and "LLM_PROVIDER=ollama" in chat.fix


def test_a_down_database_skips_the_checks_that_need_it(monkeypatch):
    seen = []
    monkeypatch.setattr(demo_change, "check_data", lambda: Check("data", True, "1 call"))
    monkeypatch.setattr(demo_change, "check_models", lambda: [Check("chat model", True, "ok")])
    monkeypatch.setattr(demo_change, "check_postgres",
                        lambda url: Check("postgres", False, "refused", "make up"))
    for name in ("check_oltp", "check_marts", "check_index"):
        monkeypatch.setattr(demo_change, name, lambda *a, _n=name: seen.append(_n))
    monkeypatch.setattr(demo_change, "_generated_calls", lambda: 1)

    checks = demo_change.preflight()

    assert [c.name for c in checks] == ["data", "chat model", "postgres"]
    assert seen == []


def test_the_report_prints_each_fix_and_fails(capsys):
    ok = demo_change.print_preflight([
        Check("data", True, "1,540 synthetic calls"),
        Check("index", False, "0 transcripts indexed, 1,540 generated", "make ingest"),
    ])

    out, err = capsys.readouterr()
    assert not ok
    assert "[ok] data" in out and "[FAIL] index" in out and "fix: make ingest" in out
    assert "nothing was asked" in err


def test_a_stale_index_is_caught_by_its_count(monkeypatch):
    class Stale:
        name = "pgvector"

        def count(self):
            return 1250

    monkeypatch.setattr("retrieval.get_retriever", lambda: Stale())

    check = demo_change.check_index(1540)

    assert not check.ok and "1,250" in check.detail and "--reset" in check.fix
