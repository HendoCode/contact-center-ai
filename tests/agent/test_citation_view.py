"""The citation view and account masking: no full account number can reach what is printed."""

import json
import re
from pathlib import Path

import pytest
from agent_support import change_result

from agent.citation_view import render_citation, split_turns
from ccai_mcp.change import render_change
from ccai_mcp.masking import mask_accounts

REPO_ROOT = Path(__file__).resolve().parents[2]
META = {"date": "2026-04-20T10:30:00", "category": "fee_dispute", "outcome": "resolved"}
FULL_NUMBERS = ["123456789", "00123456789", "1234 5678 9012 3456", "1234-5678-9012"]
LONG_RUN = re.compile(r"(?<![\d$.,])(?<!CALL-)(?<!MBR-)(?<!AGT-)\d{5,}(?![\d,]|\.\d)")


@pytest.mark.parametrize("number", FULL_NUMBERS)
def test_a_full_account_number_keeps_only_its_last_four_digits(number):
    masked = mask_accounts(f"Your account {number} is on file.")

    assert number not in masked and number.replace(" ", "").replace("-", "") not in masked
    assert f"****{re.sub(r'\D', '', number)[-4:]}" in masked


def test_amounts_dates_and_ids_are_left_alone():
    text = "CALL-00042 MBR-000584 AGT-004 on 2026-04-15 paid $1,234,567.89 and 4.816%"
    assert mask_accounts(text) == text


def test_the_view_shows_id_date_the_cited_turn_and_one_turn_of_context():
    text = ("MEMBER: Hi. AGENT: Hello, how can I help? MEMBER: Nobody told me the fee went up. "
            "AGENT: I am sorry about that. MEMBER: Thanks. AGENT: Goodbye.")
    view = render_citation("CALL-00007", "Nobody told me the fee went up.", META, text)

    assert "CALL-00007" in view and "2026-04-20 (Mon) 10:30" in view
    assert ">> MEMBER: Nobody told me the fee went up." in view
    assert "turn 3 of 6, with 1 before and 1 after" in view
    assert "AGENT: Hello, how can I help?" in view and "AGENT: I am sorry about that." in view
    assert "Goodbye" not in view and "MEMBER: Hi." not in view


def test_long_lines_wrap_to_the_width():
    long_turn = "MEMBER: " + "I was charged a fee I did not expect and nobody warned me. " * 6
    view = render_citation("CALL-00007", "nobody warned me", META, long_turn, width=60)

    assert max(len(line) for line in view.splitlines()) <= 60
    assert len(view.splitlines()) > 8


def test_a_quote_missing_from_the_transcript_is_said_so_not_invented():
    view = render_citation("CALL-00007", "never said", META, "MEMBER: Hi. AGENT: Hello.")
    assert "not found in the stored transcript" in view


def test_no_full_account_number_reaches_the_citation_view():
    leaky = [f"MEMBER: My account is {n}. AGENT: Thanks for {n}." for n in FULL_NUMBERS]
    for text in leaky:
        for quote in (text.split(" AGENT")[0].removeprefix("MEMBER: "), "Thanks"):
            view = render_citation("CALL-00007", quote, META, text)
            digits = [re.sub(r"\D", "", n) for n in FULL_NUMBERS]
            assert not any(d in view for d in digits), view
            assert not LONG_RUN.search(view)


def test_no_full_account_number_reaches_the_rendered_answer():
    result = change_result()
    result["drivers"][0]["citations"] = [{"call_id": "CALL-00042",
                                          "quote": "my account 123456789 was charged"}]
    assert "123456789" not in render_change(result)


def test_every_transcript_renders_with_no_unmasked_account():
    calls = json.loads((REPO_ROOT / "data" / "synthetic" / "transcripts.json").read_text())
    if not calls:
        pytest.skip("transcripts.json is empty")
    for call in calls:
        turn = split_turns(call["full_text"])[0]
        view = render_citation(call["call_id"], turn.split(": ", 1)[1][:30], call, call["full_text"])
        assert not LONG_RUN.search(view), call["call_id"]
