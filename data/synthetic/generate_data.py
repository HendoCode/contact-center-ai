"""
Synthetic data generator for contact-center-ai portfolio project.

Generates realistic (but entirely fake) call center data:
- Call transcripts (member + agent dialogue)
- CSAT survey results
- Call metadata (duration, date, category, outcome)

Output: data/synthetic/transcripts.json, data/synthetic/csat.json
"""

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from faker import Faker

fake = Faker()

# ── Call categories and sample dialogue templates ────────────────────────────

CATEGORIES = [
    "loan_inquiry",
    "account_balance",
    "fraud_dispute",
    "card_replacement",
    "payment_assistance",
    "online_banking_support",
    "mortgage_inquiry",
    "account_opening",
]

OUTCOMES = ["resolved", "escalated", "callback_scheduled", "unresolved"]

SAMPLE_DIALOGUES = {
    "loan_inquiry": [
        ("member", "Hi, I'd like to ask about personal loan rates."),
        ("agent", "Of course! I'd be happy to help with that. Our current personal loan rates start at 7.9% APR for well-qualified members."),
        ("member", "What's the maximum amount I could borrow?"),
        ("agent", "Personal loans go up to $50,000 depending on your creditworthiness and debt-to-income ratio. Would you like to start a pre-qualification?"),
        ("member", "Yes, let's do that."),
        ("agent", "Great. I'll need to pull a soft credit check first — that won't impact your score. Can I verify your member number?"),
    ],
    "fraud_dispute": [
        ("member", "I'm seeing a charge on my account I don't recognize. It's for $84.50 from somewhere called TechMerch Online."),
        ("agent", "I understand how concerning that can be. Let me pull up your account and look at that transaction."),
        ("member", "I definitely didn't make that purchase."),
        ("agent", "I can see the transaction from yesterday. I'm going to go ahead and initiate a dispute for you and issue a replacement card. You should receive it in 3-5 business days."),
        ("member", "Will I get the money back?"),
        ("agent", "Yes — while the dispute is under investigation, we'll issue a provisional credit to your account within 24 hours."),
    ],
    "payment_assistance": [
        ("member", "I'm having trouble making my loan payment this month. I lost my job two weeks ago."),
        ("agent", "I'm sorry to hear that. We do have a hardship assistance program that may be able to help. Can you tell me which loan this is for?"),
        ("member", "It's my auto loan. I'm about 15 days behind now."),
        ("agent", "Okay, I can see that. We may be able to defer your payment by 30 days and move it to the end of your loan term at no charge. Would that help?"),
        ("member", "Yes, that would really help. Thank you so much."),
        ("agent", "Of course. I'm submitting that request now. You'll get a confirmation by email within the hour."),
    ],
}

# Fallback generic dialogue
GENERIC_DIALOGUE = [
    ("member", "Hi, I need some help with my account."),
    ("agent", "I'd be happy to help. Can you verify your name and member number for me?"),
    ("member", "Sure, it's {name}, member number {member_id}."),
    ("agent", "Thank you. I've got your account pulled up. What can I help you with today?"),
    ("member", "I just had a quick question about my recent statement."),
    ("agent", "Of course. What would you like to know?"),
    ("member", "I think I see a fee I wasn't expecting."),
    ("agent", "Let me take a look at that for you. I can see the fee you're referring to — that's our monthly maintenance fee. However, you can waive it by maintaining a $500 minimum balance."),
    ("member", "Oh, I didn't know that. I'll make sure to keep that balance."),
    ("agent", "Perfect. Is there anything else I can help you with today?"),
    ("member", "No, that covers it. Thank you."),
    ("agent", "My pleasure! Have a great day."),
]


def generate_transcript(call_id: str, category: str, member_name: str, member_id: str) -> dict:
    """Generate a single call transcript."""
    dialogue_template = SAMPLE_DIALOGUES.get(category, GENERIC_DIALOGUE)

    # Format dialogue with member details
    turns = []
    for speaker, text in dialogue_template:
        formatted_text = text.format(name=member_name, member_id=member_id) if "{" in text else text
        turns.append({"speaker": speaker, "text": formatted_text})

    # Add some random variation
    duration_seconds = random.randint(180, 900)
    call_date = fake.date_time_between(start_date="-90d", end_date="now")

    return {
        "call_id": call_id,
        "date": call_date.isoformat(),
        "duration_seconds": duration_seconds,
        "category": category,
        "outcome": random.choice(OUTCOMES),
        "member_id": member_id,
        "member_name": member_name,
        "agent_id": f"AGT-{random.randint(100, 999)}",
        "transcript": turns,
        "full_text": " ".join(f"{t['speaker'].upper()}: {t['text']}" for t in turns),
    }


def generate_csat(call_id: str, member_id: str, outcome: str) -> dict:
    """Generate a CSAT survey result for a call."""
    # Weight scores toward the outcome
    if outcome == "resolved":
        score = random.choices([4, 5], weights=[30, 70])[0]
    elif outcome == "escalated":
        score = random.choices([2, 3, 4], weights=[20, 50, 30])[0]
    elif outcome == "unresolved":
        score = random.choices([1, 2, 3], weights=[40, 40, 20])[0]
    else:
        score = random.choices([3, 4, 5], weights=[30, 40, 30])[0]

    comments_by_score = {
        1: ["Very frustrated with the outcome.", "Problem was not resolved.", "Had to call back multiple times."],
        2: ["Not satisfied with the help I received.", "Took too long.", "Agent seemed unsure of the answer."],
        3: ["Okay experience, nothing special.", "Issue was partially resolved.", "Average service."],
        4: ["Good experience overall.", "Agent was helpful and professional.", "Minor wait time but resolved quickly."],
        5: ["Excellent service!", "Agent went above and beyond.", "Very satisfied — quick and easy."],
    }

    return {
        "call_id": call_id,
        "member_id": member_id,
        "score": score,
        "comment": random.choice(comments_by_score[score]),
        "survey_date": (datetime.now() - timedelta(days=random.randint(0, 3))).isoformat(),
    }


def main():
    output_dir = Path(__file__).parent
    n_calls = 150

    transcripts = []
    csat_results = []

    print(f"Generating {n_calls} synthetic calls...")

    for i in range(n_calls):
        call_id = f"CALL-{str(i + 1).zfill(5)}"
        member_name = fake.name()
        member_id = f"MBR-{fake.numerify('######')}"
        category = random.choice(CATEGORIES)

        transcript = generate_transcript(call_id, category, member_name, member_id)
        transcripts.append(transcript)

        # ~75% of calls result in a CSAT survey
        if random.random() < 0.75:
            csat_results.append(generate_csat(call_id, member_id, transcript["outcome"]))

    # Write output
    with open(output_dir / "transcripts.json", "w") as f:
        json.dump(transcripts, f, indent=2)

    with open(output_dir / "csat.json", "w") as f:
        json.dump(csat_results, f, indent=2)

    print(f"Done. Generated {len(transcripts)} transcripts and {len(csat_results)} CSAT records.")
    print(f"Output: {output_dir}/transcripts.json, {output_dir}/csat.json")


if __name__ == "__main__":
    main()
