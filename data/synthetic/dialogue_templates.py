"""
Dialogue templates for the synthetic call generator.

Everything here is plain data plus one composer, so the generator, the fine-tune gold
rules and the retrieval labels can all import it without running any generation.

Baseline calls
--------------
`CATEGORY_DIALOGUES[category]` is a list of at least three variants. A variant is a list
of `(speaker, text)` turns. Variant 0 is the original wording; the others change wording
only. Every variant of a category speaks the same placeholders, in the same order, and
takes the same actions, so a figure or a resolution never depends on which one was drawn.

Event calls
-----------
An event call is `intro + body`. The intro depends on the event and category (how the
member opens the call); the body depends on the driver (why they called). Both come in
three wordings, and wording `i` of the intro goes with wording `i` of the body. The
placeholders add the facts an event needs (`old_fee`, `new_fee`, `change_date`,
`overdraft_count`, `fee_name`, `branch_a`, ...), which the generator derives from the
same price schedule and ledger rows it writes.

Keys here are the ids used in `events.yml`. Those ids never reach a transcript: only the
wording below does.
"""

from __future__ import annotations

Turn = tuple[str, str]
Template = list[Turn]

# ── baseline dialogue: three variants per category ───────────────────────────

CATEGORY_DIALOGUES: dict[str, list[Template]] = {
    "account_balance": [
        [
            ("member", "Hi, I'd like to check my account balance."),
            ("agent", "Of course. Can you verify your member number for me?"),
            ("member", "Sure, it's {member_id}."),
            ("agent", "Thanks, {member_name}. Your account ending in {account_last4} shows a {balance_kind} of {balance}."),
            ("member", "Is that the same number I'd see in online banking?"),
            ("agent", "It should be — your {secondary_kind} today is {secondary_balance}."),
        ],
        [
            ("member", "Hello, could you tell me what my account balance is right now?"),
            ("agent", "Certainly. May I have your member number so I can verify you?"),
            ("member", "It's {member_id}."),
            ("agent", "Thank you, {member_name}. The account ending in {account_last4} has a {balance_kind} of {balance}."),
            ("member", "Does online banking show the same figure?"),
            ("agent", "It should. Your {secondary_kind} at the moment is {secondary_balance}."),
        ],
        [
            ("member", "Hi there, I'm calling to find out how much is in my account."),
            ("agent", "Happy to look. First, can you confirm your member number?"),
            ("member", "Yes, {member_id}."),
            ("agent", "Great, thanks {member_name}. For the account ending in {account_last4}, the {balance_kind} is {balance}."),
            ("member", "And is that what I'll see if I log in online?"),
            ("agent", "Yes, and the {secondary_kind} is {secondary_balance} as of now."),
        ],
    ],
    "online_banking_support": [
        [
            ("member", "I can't sign in to online banking. It keeps saying my password is wrong."),
            ("agent", "I can help reset that. Can you verify your member number and the last four of the account you're signing into?"),
            ("member", "Sure. {member_id}, and the account ends in {account_last4}."),
            ("agent", "Thank you, {member_name}. I've sent a secure password reset link to the email on file."),
            ("member", "Got it. Will that take effect immediately?"),
            ("agent", "Yes, within a minute or two. You'll then see your balance of {ledger_balance} on the dashboard."),
        ],
        [
            ("member", "Online banking won't let me in. It says the password is incorrect every time."),
            ("agent", "Let's get you back in. Please give me your member number and the last four digits of the account."),
            ("member", "{member_id}, and the account ends in {account_last4}."),
            ("agent", "Thanks, {member_name}. A secure password reset link is on its way to the email we have on file."),
            ("member", "Okay. How soon does the new password work?"),
            ("agent", "Right away, usually within a minute or two. Your dashboard will then show a balance of {ledger_balance}."),
        ],
        [
            ("member", "I can't get into my online banking, the password isn't being accepted."),
            ("agent", "I can fix that. I'll need your member number and the last four of the account you use to sign in."),
            ("member", "My member number is {member_id}; the account ends in {account_last4}."),
            ("agent", "Perfect, {member_name}. I've emailed you a secure password reset link."),
            ("member", "Thanks. Is it active as soon as I click it?"),
            ("agent", "Yes, within a minute or two, and your dashboard will show a balance of {ledger_balance}."),
        ],
    ],
    "card_services": [
        [
            ("member", "I have a question about my credit card."),
            ("agent", "I'd be happy to help. I've verified {member_id}. Which card are we looking at?"),
            ("member", "The one ending in {account_last4}."),
            ("agent", "Got it — that's your Visa with a {credit_limit} limit, {outstanding_balance} outstanding, and a purchase APR of {purchase_apr}."),
            ("member", "And what's my available credit?"),
            ("agent", "You have {available_credit} available right now."),
        ],
        [
            ("member", "Hi, I need some information about my credit card account."),
            ("agent", "Glad to help. I've confirmed {member_id}. Which card is it?"),
            ("member", "It's the card ending in {account_last4}."),
            ("agent", "Okay, that Visa has a {credit_limit} limit with {outstanding_balance} outstanding, and the purchase APR is {purchase_apr}."),
            ("member", "How much can I still spend on it?"),
            ("agent", "Your available credit is {available_credit} right now."),
        ],
        [
            ("member", "Can you go over my credit card details with me?"),
            ("agent", "Absolutely. {member_id} is verified. Which card should I open?"),
            ("member", "The card ending in {account_last4}, please."),
            ("agent", "Found it. The credit limit is {credit_limit}, the outstanding balance is {outstanding_balance}, and the purchase APR is {purchase_apr}."),
            ("member", "What does that leave me to spend?"),
            ("agent", "You have {available_credit} of available credit at the moment."),
        ],
    ],
    "fraud_dispute": [
        [
            ("member", "I see a charge I don't recognize on my account. It's from an online retailer."),
            ("agent", "I understand — let me look at that. On the account ending in {account_last4}, your {balance_kind} is {balance}."),
            ("member", "I definitely didn't make that purchase."),
            ("agent", "I've opened a dispute and issued a provisional credit, so your balance won't be affected while we investigate."),
            ("member", "How long until the full resolution?"),
            ("agent", "Typically 10 business days."),
        ],
        [
            ("member", "There's a charge from an online retailer on my account that isn't mine."),
            ("agent", "Thanks for flagging it. On the account ending in {account_last4}, the {balance_kind} is {balance}."),
            ("member", "I never bought anything from them."),
            ("agent", "I've opened a dispute and posted a provisional credit, so your balance stays whole while we investigate."),
            ("member", "When will this be fully resolved?"),
            ("agent", "Usually within 10 business days."),
        ],
        [
            ("member", "I'm looking at my statement and one charge from an online store looks fraudulent."),
            ("agent", "Let me check it. On the account ending in {account_last4}, your {balance_kind} is {balance}."),
            ("member", "I didn't authorize that purchase at all."),
            ("agent", "Understood. I've started a dispute and applied a provisional credit, so you won't be out the money while we investigate."),
            ("member", "How long does the investigation take?"),
            ("agent", "Typically 10 business days to a full resolution."),
        ],
    ],
    "payment_assistance": [
        [
            ("member", "I'm having trouble making a payment this month."),
            ("agent", "I'm sorry to hear that. For the account ending in {account_last4}, your {payment_label} is {payment_amount}."),
            ("member", "Right now that's more than I can manage."),
            ("agent", "We have a hardship program that may defer this month's payment to the end of the term. Would you like me to submit that request?"),
            ("member", "Yes, please."),
            ("agent", "Done. You'll get a confirmation by email shortly."),
        ],
        [
            ("member", "I'm going to struggle to make my payment this month."),
            ("agent", "I'm sorry you're in that spot. On the account ending in {account_last4}, the {payment_label} is {payment_amount}."),
            ("member", "That's more than I can afford right now."),
            ("agent", "Our hardship program can move this month's payment to the end of the term. Shall I put in that request for you?"),
            ("member", "Yes, go ahead."),
            ("agent", "All set. A confirmation will arrive by email shortly."),
        ],
        [
            ("member", "I'm short on cash and can't cover my payment this month."),
            ("agent", "Thanks for telling us early. For the account ending in {account_last4}, your {payment_label} is {payment_amount}."),
            ("member", "I can't pay that right now."),
            ("agent", "We have a hardship program that can defer this month's payment to the end of the term. Would you like me to submit the request?"),
            ("member", "Please do."),
            ("agent", "It's submitted, and you'll get an email confirmation shortly."),
        ],
    ],
    "rate_inquiry": [
        [
            ("member", "I'd like to know what interest rate I'm currently paying."),
            ("agent", "Happy to help. Let me pull up the account ending in {account_last4}."),
            ("member", "What does it show?"),
            ("agent", "For that account, your {rate_label} is {rate}."),
            ("member", "Is that fixed, or can it change?"),
            ("agent", "That depends on the product. For reference, your current {balance_kind} is {balance}."),
        ],
        [
            ("member", "Could you tell me the interest rate on my account?"),
            ("agent", "Sure. Let me open the account ending in {account_last4}."),
            ("member", "What rate do you see?"),
            ("agent", "On that account, the {rate_label} is {rate}."),
            ("member", "Will that rate stay the same?"),
            ("agent", "It depends on the product. For context, the {balance_kind} right now is {balance}."),
        ],
        [
            ("member", "I'm calling to ask what rate I'm being charged."),
            ("agent", "Of course. I'm pulling up the account ending in {account_last4}."),
            ("member", "And what is it?"),
            ("agent", "The {rate_label} on that account is {rate}."),
            ("member", "Does it ever change?"),
            ("agent", "That varies by product. Your {balance_kind} at the moment is {balance}, for reference."),
        ],
    ],
    "loan_inquiry": [
        [
            ("member", "I'd like to ask about your current loan rates."),
            ("agent", "Of course. Our posted rates vary by product — can I ask what you're looking to finance?"),
            ("member", "Something comparable to my account ending in {account_last4}."),
            ("agent", "Understood. For a comparable product today, the {rate_label} would be {rate}, and your existing {balance_kind} is {balance}."),
            ("member", "Thanks, that's helpful."),
            ("agent", "Would you like me to start a pre-qualification?"),
        ],
        [
            ("member", "What are your loan rates right now?"),
            ("agent", "Happy to share. Rates depend on the product, so what are you hoping to finance?"),
            ("member", "Something like my account ending in {account_last4}."),
            ("agent", "Got it. A comparable product today would carry a {rate_label} of {rate}, and your current {balance_kind} is {balance}."),
            ("member", "Okay, that gives me an idea."),
            ("agent", "Shall I begin a pre-qualification for you?"),
        ],
        [
            ("member", "I'm thinking about a new loan and want to know today's rates."),
            ("agent", "Sure thing. Our rates differ by product, so what would the loan be for?"),
            ("member", "Similar to my account ending in {account_last4}."),
            ("agent", "Thanks. For something comparable, the {rate_label} is {rate} today, and your existing {balance_kind} is {balance}."),
            ("member", "That's useful, thank you."),
            ("agent", "Would you like to start a pre-qualification?"),
        ],
    ],
    "mortgage_inquiry": [
        [
            ("member", "I'd like to check on my mortgage."),
            ("agent", "Sure — the mortgage ending in {account_last4} has a note rate of {note_rate} and a current principal of {current_principal}."),
            ("member", "What's my loan-to-value now? I saw LTV mentioned on my statement."),
            ("agent", "Your LTV at origination was {ltv}. Your escrow balance is {escrow_balance}."),
            ("member", "Great, that matches what I expected."),
            ("agent", "Is there anything else about the mortgage I can look into?"),
        ],
        [
            ("member", "Can you give me an update on my mortgage?"),
            ("agent", "Of course — the mortgage ending in {account_last4} carries a note rate of {note_rate} with a current principal of {current_principal}."),
            ("member", "And the loan-to-value? My statement shows an LTV figure."),
            ("agent", "The LTV at origination was {ltv}, and your escrow balance is {escrow_balance}."),
            ("member", "That lines up with what I thought."),
            ("agent", "Anything else on the mortgage I can check for you?"),
        ],
        [
            ("member", "I'm calling to review my mortgage details."),
            ("agent", "Happy to. The mortgage ending in {account_last4} has a {note_rate} note rate and {current_principal} in current principal."),
            ("member", "What is the loan-to-value these days? I noticed LTV on my statement."),
            ("agent", "At origination the LTV was {ltv}. The escrow balance is {escrow_balance}."),
            ("member", "Good, thanks, that's what I was expecting."),
            ("agent", "Is there something else about the mortgage you'd like me to look at?"),
        ],
    ],
    "insurance_service": [
        [
            ("member", "I'd like to review my auto insurance policy."),
            ("agent", "Of course. Your policy shows an annual premium of {annual_premium}, which is {monthly_premium} a month, with a {deductible} deductible."),
            ("member", "Can you walk through the coverage?"),
            ("agent", "Happy to. Your current coverage includes the liability and collision components you selected."),
            ("member", "And what would full coverage add?"),
            ("agent", "Great question — I can quote that for your vehicle right now if you'd like."),
        ],
        [
            ("member", "I'm calling to go over my auto insurance."),
            ("agent", "Sure. The policy has an annual premium of {annual_premium}, or {monthly_premium} a month, and a {deductible} deductible."),
            ("member", "Could you explain what's covered?"),
            ("agent", "Gladly. You currently have the liability and collision coverage you chose."),
            ("member", "What would I get with full coverage?"),
            ("agent", "Good question — I can price that for your vehicle right now if you want."),
        ],
        [
            ("member", "I want to look at my auto policy and see where I stand."),
            ("agent", "Absolutely. Your annual premium is {annual_premium}, which works out to {monthly_premium} a month, with a {deductible} deductible."),
            ("member", "What does the coverage include?"),
            ("agent", "It includes the liability and collision components you picked."),
            ("member", "How would full coverage be different?"),
            ("agent", "I can quote full coverage for your vehicle right now, if you'd like."),
        ],
    ],
    "investment_review": [
        [
            ("member", "I'd like to review my investment account."),
            ("agent", "Let's take a look. Your market value is {market_value}, with {cash_value} in settled cash."),
            ("member", "How has it performed this year?"),
            ("agent", "Your year-to-date return is {ytd_return}. Your cost basis sits at roughly {market_value} versus what you contributed."),
            ("member", "That's good to know. I appreciate the review."),
            ("agent", "My pleasure — as a long-time member, your lifetime value to the credit union is important to us, and we're glad to keep the advice coming."),
        ],
        [
            ("member", "I'm calling for a check-in on my investment account."),
            ("agent", "Gladly. The market value is {market_value}, and {cash_value} of it is settled cash."),
            ("member", "How have I done so far this year?"),
            ("agent", "Your year-to-date return is {ytd_return}, and the cost basis is near {market_value} compared with your contributions."),
            ("member", "Thanks, that's helpful to hear."),
            ("agent", "Happy to help. As a long-time member, you matter a great deal to the credit union, and we're glad to keep supporting you."),
        ],
        [
            ("member", "Could we go over how my investments have performed?"),
            ("agent", "Of course. Right now the market value is {market_value}, including {cash_value} in settled cash."),
            ("member", "What's the return for the year?"),
            ("agent", "Year to date you're at {ytd_return}. Your cost basis is roughly {market_value} against what you put in."),
            ("member", "Good to know, thank you for walking me through it."),
            ("agent", "Any time. Long-time members like you are important to the credit union, and we're glad to keep helping."),
        ],
    ],
    "account_opening": [
        [
            ("member", "I'd like to open a new account."),
            ("agent", "Wonderful! I've verified {member_id}. Were you thinking checking or savings?"),
            ("member", "A savings account, please."),
            ("agent", "Great choice. Our high-yield savings earns {new_account_apy} right now."),
            ("member", "That's a good rate. Let's open it."),
            ("agent", "I'll get that started — you'll receive the account details in a few minutes."),
        ],
        [
            ("member", "I'm interested in opening another account with you."),
            ("agent", "Glad to hear it. I've confirmed {member_id}. Do you want checking or savings?"),
            ("member", "Savings, please."),
            ("agent", "Good pick. Our high-yield savings is paying {new_account_apy} at the moment."),
            ("member", "Nice rate. Please go ahead and open it."),
            ("agent", "Starting it now — the account details will reach you in a few minutes."),
        ],
        [
            ("member", "Can I set up a new account today?"),
            ("agent", "Of course! {member_id} is verified. Would you like a checking or a savings account?"),
            ("member", "I want a savings account."),
            ("agent", "Excellent. Our high-yield savings currently earns {new_account_apy}."),
            ("member", "That works for me. Let's do it."),
            ("agent", "I'll begin the application, and you'll get the account details within a few minutes."),
        ],
    ],
    "fee_dispute": [
        [
            ("member", "I see a fee on my account I wasn't expecting."),
            ("agent", "Let me look. On the account ending in {account_last4}, I see a monthly fee of {monthly_fee}."),
            ("member", "I thought that was waived."),
            ("agent", "It is waived when you keep a minimum balance of {waiver_min_balance}. I can refund this month's fee as a courtesy."),
            ("member", "That would be great, thank you."),
            ("agent", "Done. I've refunded it and noted your account."),
        ],
        [
            ("member", "There's a fee on my account that I didn't expect."),
            ("agent", "I'll check. On the account ending in {account_last4}, there is a monthly fee of {monthly_fee}."),
            ("member", "I thought that fee was waived for me."),
            ("agent", "It's waived when you maintain a minimum balance of {waiver_min_balance}. As a courtesy, I can refund this month's fee."),
            ("member", "Thank you, I'd appreciate that."),
            ("agent", "It's refunded, and I've added a note to your account."),
        ],
        [
            ("member", "Why is there a deduction on my account? I wasn't expecting one."),
            ("agent", "Let me see. For the account ending in {account_last4}, I find a monthly fee of {monthly_fee}."),
            ("member", "Isn't that supposed to be waived?"),
            ("agent", "Yes, with a minimum balance of {waiver_min_balance}. I'm able to refund this month's fee as a courtesy."),
            ("member", "That's kind of you, thanks."),
            ("agent", "All done — the fee is refunded and the account is noted."),
        ],
    ],
    "escrow_analysis": [
        [
            ("member", "I got a notice about my escrow account."),
            ("agent", "Let me pull up your escrow analysis. Your escrow balance is {escrow_balance}, and your escrow payment is included in the {monthly_pi} monthly payment."),
            ("member", "Is there a shortage?"),
            ("agent", "It looks like your property taxes changed slightly, so there's a small adjustment. Not much, but worth reviewing."),
        ],
        [
            ("member", "I received a letter about my escrow account and want to understand it."),
            ("agent", "I'll open your escrow analysis. The escrow balance is {escrow_balance}, and the escrow portion sits inside your {monthly_pi} monthly payment."),
            ("member", "Does that mean there's a shortage?"),
            ("agent", "Your property taxes shifted a little, which causes a small adjustment. It's minor, but worth a review."),
        ],
        [
            ("member", "There's an escrow notice in my mail and I have questions."),
            ("agent", "Let me look at the escrow analysis. Your escrow balance is {escrow_balance}, and your escrow payment is part of the {monthly_pi} monthly payment."),
            ("member", "Is the escrow underfunded?"),
            ("agent", "Property taxes were slightly different, so there's a small adjustment. Not large, but worth reviewing."),
        ],
    ],
    "rate_lock_status": [
        [
            ("member", "I want to check on my rate lock."),
            ("agent", "No problem. I can see an active lock on your application with a rate of {locked_rate}."),
            ("member", "When does it expire?"),
            ("agent", "It's locked through {expires_at}, so you're covered while we finish underwriting."),
            ("member", "What was the loan amount again?"),
            ("agent", "The application is for {loan_amount}."),
        ],
        [
            ("member", "I'm calling to confirm my rate lock remains in place."),
            ("agent", "Sure. Your application has an active lock at a rate of {locked_rate}."),
            ("member", "How long is it good for?"),
            ("agent", "It's locked through {expires_at}, so you're protected while underwriting finishes."),
            ("member", "And the loan amount?"),
            ("agent", "The application is for {loan_amount}."),
        ],
        [
            ("member", "Can you tell me where my rate lock stands?"),
            ("agent", "Of course. I see an active lock on your application at {locked_rate}."),
            ("member", "How long does it last?"),
            ("agent", "It runs through {expires_at}, which covers you while we complete underwriting."),
            ("member", "Remind me what the loan totals?"),
            ("agent", "The application is for {loan_amount}."),
        ],
    ],
}

N_VARIANTS = 3

# ── event dialogue: intro + body, three wordings each ────────────────────────

# Fee-schedule change: how the member opens a fee call or a balance call.
_E1_FEE_INTRO: list[Template] = [
    [("member", "I'm calling about a fee on my account."),
     ("agent", "I can help with that. I have {member_name}, member {member_id}, and the account ending in {account_last4} open.")],
    [("member", "Hi, there's a charge on my account that I want to ask about."),
     ("agent", "Sure thing. I have {member_name}, {member_id}, and the account ending in {account_last4} in front of me.")],
    [("member", "I just noticed a fee on my account and I'd like to understand it."),
     ("agent", "Of course. I'm looking at the account ending in {account_last4} for {member_name}, member {member_id}.")],
]
_E1_BALANCE_INTRO: list[Template] = [
    [("member", "Hi, I'd like to check my balance."),
     ("agent", "Sure. I have {member_name}, member {member_id}. Your account ending in {account_last4} shows a ledger balance of {ledger_balance}.")],
    [("member", "Can you tell me my current balance? It looks lower than I expected."),
     ("agent", "Let me look at the account ending in {account_last4} for {member_name}, member {member_id}. The ledger balance is {ledger_balance}.")],
    [("member", "I'm checking my balance after some activity on my account."),
     ("agent", "Of course. For {member_name}, member {member_id}, the account ending in {account_last4} has a ledger balance of {ledger_balance}.")],
]

_E2_CARD_INTRO: list[Template] = [
    [("member", "I have a problem with my credit card."),
     ("agent", "I'm sorry to hear that. I've verified {member_id}, {member_name}. Which card is it?")],
    [("member", "My card isn't working the way it should."),
     ("agent", "Let's sort it out. I've confirmed {member_name}, member {member_id}. Which card are we talking about?")],
    [("member", "I need help with a card purchase."),
     ("agent", "Happy to help. I have {member_id} verified. Which card did you use?")],
]
_E2_FRAUD_INTRO: list[Template] = [
    [("member", "A purchase on my card was blocked and I'm worried something is wrong."),
     ("agent", "I understand. I have {member_name}, {member_id}, verified, and I'm looking at the account ending in {account_last4}.")],
    [("member", "I got an alert about my card and I'm worried about fraud."),
     ("agent", "Let's take a look together. I have member {member_id} verified and the account ending in {account_last4} open.")],
    [("member", "My card was stopped and I want to make sure no one else is using it."),
     ("agent", "Understood, {member_name}. I'm reviewing the account ending in {account_last4} for member {member_id}.")],
]

_E3_APP_INTRO: list[Template] = [
    [("member", "I'm having trouble with the mobile app after the latest update."),
     ("agent", "Sorry about that. I've verified {member_id}, {member_name}. What are you seeing?")],
    [("member", "The banking app is giving me problems since it updated."),
     ("agent", "Let's fix it. I have {member_name}, member {member_id}. What happens when you open the app?")],
    [("member", "Something changed in the app and I can't do what I normally do."),
     ("agent", "I can help. I've confirmed member {member_id}. Tell me what you're running into.")],
]

_E4_BALANCE_INTRO: list[Template] = [
    [("member", "Hi, I'd like my balance, and I also have a question about my branch."),
     ("agent", "Sure. I have {member_name}, member {member_id}. The account ending in {account_last4} shows a {balance_kind} of {balance}.")],
    [("member", "Can you give me my balance? I also need to ask about where I do my banking now."),
     ("agent", "Of course. For member {member_id}, the account ending in {account_last4} has a {balance_kind} of {balance}.")],
    [("member", "I'm checking my balance, and then I have a question about my branch."),
     ("agent", "Happy to. {member_name}, the {balance_kind} on the account ending in {account_last4} is {balance}.")],
]
_E4_OPENING_INTRO: list[Template] = [
    [("member", "I want to open a new account, and my usual branch has just closed."),
     ("agent", "I can help with both. I've verified {member_id}. Our high-yield savings earns {new_account_apy} right now.")],
    [("member", "I'd like to open another account, but my local branch isn't open anymore."),
     ("agent", "Understood. I have member {member_id} verified. Our high-yield savings is paying {new_account_apy} at the moment.")],
    [("member", "Can I open an account over the phone? My branch closed."),
     ("agent", "Yes, we can start it here. {member_id} is verified, and our high-yield savings earns {new_account_apy}.")],
]
_E4_PAYMENT_INTRO: list[Template] = [
    [("member", "I'm calling about a payment, and I also need to ask about my branch."),
     ("agent", "Of course. For the account ending in {account_last4}, your {payment_label} is {payment_amount}.")],
    [("member", "I need to sort out a payment, and my branch is closed so I don't know where to go."),
     ("agent", "I can help. On the account ending in {account_last4}, the {payment_label} is {payment_amount}.")],
    [("member", "Before I make my payment I have a question about my branch."),
     ("agent", "Sure. On the account ending in {account_last4}, the {payment_label} is {payment_amount}.")],
]

# (event id, category) -> three intros
INTROS: dict[tuple[str, str], list[Template]] = {
    ("fee_schedule_change", "fee_dispute"): _E1_FEE_INTRO,
    ("fee_schedule_change", "account_balance"): _E1_BALANCE_INTRO,
    ("card_fraud_rule_change", "card_services"): _E2_CARD_INTRO,
    ("card_fraud_rule_change", "fraud_dispute"): _E2_FRAUD_INTRO,
    ("mobile_app_v5_release", "online_banking_support"): _E3_APP_INTRO,
    ("westside_branch_closure", "account_balance"): _E4_BALANCE_INTRO,
    ("westside_branch_closure", "account_opening"): _E4_OPENING_INTRO,
    ("westside_branch_closure", "payment_assistance"): _E4_PAYMENT_INTRO,
}

# driver id -> three bodies
BODIES: dict[str, list[Template]] = {
    "unaware_of_change": [
        [("member", "There's a {new_fee} overdraft fee here. It's always been {old_fee}, and nobody told me it changed."),
         ("agent", "I understand. The overdraft fee went from {old_fee} to {new_fee} per item on {change_date}, and the notice went out as a statement insert."),
         ("member", "I never saw an insert. I'd have kept more in checking if I'd known."),
         ("agent", "That's fair, and I'm sorry it wasn't clearer. The new {new_fee} fee applies to overdrafts from {change_date} forward.")],
        [("member", "Wait, why was I charged {new_fee}? I thought overdrafts cost {old_fee}."),
         ("agent", "The fee changed on {change_date}. It's now {new_fee} per item, and members were told in a statement insert."),
         ("member", "I don't read the inserts. Nobody called or emailed me about this."),
         ("agent", "I hear you. The change is in effect now, and I can send you the updated fee schedule.")],
        [("member", "I didn't know the overdraft fee had gone up. I was charged {new_fee}."),
         ("agent", "As of {change_date} the overdraft fee is {new_fee} per item instead of {old_fee}. The announcement was a statement insert."),
         ("member", "That's easy to miss. I wish I'd gotten a message."),
         ("agent", "Understood. I'll note your feedback, and I can email you the new schedule so you have it.")],
    ],
    "courtesy_refund_refused": [
        [("member", "I was charged an overdraft fee of {new_fee}. Could you refund it as a courtesy?"),
         ("agent", "I checked, and the courtesy waiver can be used once every 12 months. A waiver was already applied to your account within the last 12 months, so I can't refund this one."),
         ("member", "That seems harsh for a member as long as I've been here."),
         ("agent", "I understand. The waiver rule is once per 12 months from the last waiver, and I'm not able to make an exception today.")],
        [("member", "Can I get a courtesy refund on this {new_fee} overdraft fee?"),
         ("agent", "Under the courtesy waiver policy, we can waive one overdraft fee per rolling 12 months. Your account used its waiver recently, so this fee stands."),
         ("member", "So there's no way to get it back?"),
         ("agent", "Not under the waiver rule. I'm sorry I couldn't give you a better answer.")],
        [("member", "I'd like a courtesy refund for the {new_fee} overdraft fee, please."),
         ("agent", "I looked at the account. The waiver is once every 12 months, and yours was applied within that period, so I can't refund this fee."),
         ("member", "Okay, I didn't realize there was a limit."),
         ("agent", "Yes, it's once per 12 months. I'm sorry, I know that's disappointing.")],
    ],
    "multiple_overdrafts_same_day": [
        [("member", "I was charged {overdraft_count} overdraft fees on {overdraft_day}, all for one day of activity."),
         ("agent", "I see {overdraft_count} overdraft fees of {new_fee} each posted on {overdraft_day} to the account ending in {account_last4}, {overdraft_total} in total."),
         ("member", "Why am I charged per item? That adds up fast."),
         ("agent", "The fee is {new_fee} per item since {change_date}. Several items cleared on the same day, so each one was charged.")],
        [("member", "Several overdraft fees posted on {overdraft_day}. Can you tell me why there are {overdraft_count} of them?"),
         ("agent", "Yes, {overdraft_count} items overdrew the account that day, and each carries the {new_fee} fee, so {overdraft_total} in total."),
         ("member", "All of that in a single day?"),
         ("agent", "On the same day, yes. The per-item fee has been {new_fee} since {change_date}.")],
        [("member", "Why did I get hit with {overdraft_count} overdraft charges on one day?"),
         ("agent", "On {overdraft_day} I see {overdraft_count} overdraft fees at {new_fee}, which is {overdraft_total} on the account ending in {account_last4}."),
         ("member", "That seems like a lot for one day."),
         ("agent", "They were charged per item on the same day, at {new_fee} each since {change_date}.")],
    ],
    "other_fee_question": [
        [("member", "What is this {fee_name} for?"),
         ("agent", "That's the {fee_name} of {fee_amount} on your fee schedule for the account ending in {account_last4}."),
         ("member", "Is it a new fee?"),
         ("agent", "No, it isn't connected to the overdraft change. It's the standard {fee_name}.")],
        [("member", "I see a {fee_name} and I'm not sure why."),
         ("agent", "The {fee_name} is {fee_amount} under the fee schedule for the account ending in {account_last4}."),
         ("member", "Did that fee just start?"),
         ("agent", "No, it's an existing fee and separate from the overdraft fee. I can explain when it applies.")],
        [("member", "Can you tell me about the {fee_name}?"),
         ("agent", "Of course. On the account ending in {account_last4} the {fee_name} is {fee_amount}."),
         ("member", "Has the amount changed recently?"),
         ("agent", "The {fee_name} hasn't changed. Only the overdraft fee is different from before.")],
    ],
    "new_merchant_decline": [
        [("member", "My card was declined at an online store I've never used before, and I'm travelling so I need it to work."),
         ("agent", "I see the decline on the card ending in {account_last4}. Online purchases from a new merchant now need a text verification before they're approved."),
         ("member", "I wasn't asked for anything."),
         ("agent", "The request goes to the phone number on file. Once you confirm, a retry at the new merchant will go through.")],
        [("member", "I tried to buy from a website for the first time and my card was declined."),
         ("agent", "That fits the new fraud rule. Card-not-present purchases from a new merchant need a text verification, and the card ending in {account_last4} is waiting on one."),
         ("member", "So the card isn't broken?"),
         ("agent", "No, it's working. Confirm the text and try the new merchant again.")],
        [("member", "My card got declined on my first order from a new merchant while I'm away from home."),
         ("agent", "The card ending in {account_last4} is fine. A first purchase from a new merchant online now triggers a text verification."),
         ("member", "Can you just approve it from your side?"),
         ("agent", "The verification has to come from you by text. After you confirm, the new merchant can be charged.")],
    ],
    "verification_text_missing_or_unclear": [
        [("member", "I was told to confirm a text to approve a purchase, but the verification text never arrived."),
         ("agent", "Let me check the phone number on file for the card ending in {account_last4}. I can resend the verification text."),
         ("member", "Please do. I'm not even sure what the message will say."),
         ("agent", "It asks you to reply YES to approve the purchase. I've resent it now.")],
        [("member", "I got a verification text but I couldn't understand what it wanted me to do."),
         ("agent", "It's a short message asking you to reply YES to confirm the purchase on the card ending in {account_last4}. I can walk you through it."),
         ("member", "That's all? The wording confused me."),
         ("agent", "That's all. Reply YES to the verification text and the purchase will be approved.")],
        [("member", "The verification text for my card purchase didn't come through."),
         ("agent", "I'll check the number on file for the card ending in {account_last4} and resend the verification text."),
         ("member", "What do I do when it arrives?"),
         ("agent", "Reply YES. Once you do, you can retry the purchase.")],
    ],
    "subscription_decline": [
        [("member", "My monthly subscription charge was declined on my card."),
         ("agent", "I see it on the card ending in {account_last4}. The recurring subscription charge was held for verification under the new rule."),
         ("member", "It's the same charge every month. Why now?"),
         ("agent", "A recurring subscription from a merchant the card hasn't seen under the new rule can be held. I can release it after you confirm by text.")],
        [("member", "A subscription I pay every month got declined and I don't understand why."),
         ("agent", "The subscription charge on the card ending in {account_last4} was held for a text verification."),
         ("member", "I've paid them for years."),
         ("agent", "I know. Confirm the text and ask the subscription provider to retry the charge.")],
        [("member", "My streaming subscription renewal was declined on my card."),
         ("agent", "I see the recurring subscription charge on the card ending in {account_last4}. It's waiting on a verification text."),
         ("member", "Will I lose the service?"),
         ("agent", "Not if you confirm soon. After the text is confirmed the subscription renewal can go through.")],
    ],
    "biometric_reenrollment": [
        [("member", "The app says I have to set up my fingerprint login again."),
         ("agent", "Yes, mobile app version 5 needs members to re-enroll face or fingerprint login. Open Settings, then Security, and re-enroll."),
         ("member", "Why didn't it keep my old setup?"),
         ("agent", "The new version stores biometric credentials differently, so everyone has to re-enroll once.")],
        [("member", "Since the update, my face login stopped working."),
         ("agent", "After app version 5 you need to re-enroll your biometric login. It takes about a minute under Security settings."),
         ("member", "Do I need a new password too?"),
         ("agent", "No. Just re-enroll your face or fingerprint and the biometric login will work again.")],
        [("member", "The mobile app keeps asking me to enroll my fingerprint again."),
         ("agent", "That's expected with version 5. Members have to re-enroll face or fingerprint login after the update."),
         ("member", "Is something wrong with my phone?"),
         ("agent", "Nothing is wrong. Re-enroll once in the app and the prompt goes away.")],
    ],
    "passcode_not_received": [
        [("member", "The app asks for a one-time passcode and it never arrives."),
         ("agent", "I'll resend the one-time passcode to the phone number on file for the account ending in {account_last4}."),
         ("member", "I'm still waiting on it."),
         ("agent", "It can take a minute. If the passcode doesn't arrive, I can switch delivery to email.")],
        [("member", "I can't finish signing in because the passcode text doesn't come."),
         ("agent", "Let me check the number on file for the account ending in {account_last4}. I've sent a new one-time passcode."),
         ("member", "Nothing yet. Is the number wrong?"),
         ("agent", "The number looks right. The passcode may be delayed, so I can also email it to you.")],
        [("member", "After the update I never receive the one-time passcode to log in."),
         ("agent", "I'm resending the passcode to the phone on file for the account ending in {account_last4}."),
         ("member", "Okay, I'll wait."),
         ("agent", "If the one-time passcode still doesn't arrive, tell me and I'll send it by email.")],
    ],
    "bill_pay_payees_missing": [
        [("member", "After updating the app my saved bill-pay payees are gone."),
         ("agent", "Your payees are still on your profile. App version 5 needs a sync to show the saved payees again."),
         ("member", "How do I sync?"),
         ("agent", "Sign out, sign back in, and open Bill Pay. The saved payees will reload.")],
        [("member", "My bill-pay list is empty since the new app version."),
         ("agent", "The saved payees weren't deleted. They should reappear once the app finishes syncing bill pay."),
         ("member", "I have a payment due soon."),
         ("agent", "If the payees still don't appear after you sign in again, I can add the payee for you from here.")],
        [("member", "None of my bill-pay payees show up in the app anymore."),
         ("agent", "I can see all your payees on the profile. After the app update they need a refresh in Bill Pay."),
         ("member", "Will I have to add them all again?"),
         ("agent", "No. Refresh Bill Pay and the saved payees will come back.")],
    ],
    "cash_or_coin_deposits": [
        [("member", "Since the Westside branch closed, where can I deposit cash and coins?"),
         ("agent", "Westside members can use the {branch_a} or {branch_b} branch, and either one takes cash and coin deposits."),
         ("member", "Do they count coins?"),
         ("agent", "The {branch_a} branch has a coin counting machine that takes loose and rolled coins.")],
        [("member", "My branch closed and I used to bring in cash and a jar of coins."),
         ("agent", "You can bring cash and coin deposits to the {branch_a} or {branch_b} branch instead."),
         ("member", "Is there a limit on coins?"),
         ("agent", "The coin machine at {branch_a} handles loose coins, and tellers can take rolled coin.")],
        [("member", "Where do I take cash and coin deposits now that Westside is closed?"),
         ("agent", "The nearest options are the {branch_a} and {branch_b} branches, and both accept cash and coin."),
         ("member", "Can I do it at an ATM instead?"),
         ("agent", "ATMs take cash deposits, but coin deposits need a teller or the {branch_a} coin machine.")],
    ],
    "safe_deposit_box": [
        [("member", "What happens to my safe deposit box now that Westside has closed?"),
         ("agent", "Safe deposit boxes are moving to the {branch_a} branch. You'll get a letter with the new box number."),
         ("member", "Do I have to move my things myself?"),
         ("agent", "You can visit {branch_a} to transfer the contents, and the rent on the box stays the same.")],
        [("member", "I have a safe deposit box at the Westside branch. Where is it going?"),
         ("agent", "Boxes from Westside are being relocated to the {branch_a} branch. A letter will tell you which box is yours."),
         ("member", "When can I get to it?"),
         ("agent", "You can access it at the new branch once the relocation is finished, and we'll confirm the date in the letter.")],
        [("member", "My branch is closed. How do I get into my safe deposit box?"),
         ("agent", "Your safe deposit box is being moved to the {branch_a} branch. You can visit with your ID."),
         ("member", "Is the box size the same?"),
         ("agent", "Yes, the new box is the same size and the same rate.")],
    ],
    "notary_or_cashiers_check": [
        [("member", "I used the Westside branch for notary and cashier's checks. Where do I go now?"),
         ("agent", "The {branch_b} branch offers notary service and cashier's checks."),
         ("member", "Do I need an appointment?"),
         ("agent", "Appointments are best for the notary. Cashier's checks can be done as a walk-in.")],
        [("member", "Now that my branch closed, where can I get a cashier's check?"),
         ("agent", "You can get a cashier's check at the {branch_b} branch."),
         ("member", "And a notary? I need a document notarized."),
         ("agent", "The {branch_b} branch has a notary. I'd book a time so someone is available.")],
        [("member", "I need a notary and a cashier's check and the Westside branch isn't there anymore."),
         ("agent", "Both are available at {branch_b}. A notary works by appointment, and cashier's checks are available during branch hours."),
         ("member", "What should I bring?"),
         ("agent", "Bring a photo ID. For the cashier's check, bring the account details you want it drawn on.")],
    ],
}

# The coaching pattern: in some refused-refund calls the agent misstates the waiver rule.
# pattern variant id -> three bodies replacing the plain refusal.
MISSTATED_REFUSALS: dict[str, list[Template]] = {
    "once_a_year": [
        [("member", "I was charged an overdraft fee of {new_fee}. Could you refund it as a courtesy?"),
         ("agent", "We only waive one overdraft fee once a year, and your account already had its waiver this year, so I can't refund this one."),
         ("member", "Is that the rule for every member?"),
         ("agent", "Yes, it's once a year per member.")],
        [("member", "Can I get a courtesy refund on this {new_fee} overdraft fee?"),
         ("agent", "The courtesy waiver is once a year. Your account has already used this year's waiver, so the fee stands."),
         ("member", "Does it reset in January?"),
         ("agent", "It does, once a year. I'm sorry I can't refund it today.")],
        [("member", "I'd like a courtesy refund for the {new_fee} overdraft fee, please."),
         ("agent", "I can only waive an overdraft fee once a year, and the waiver for this year was already used on your account."),
         ("member", "So I just wait for next year?"),
         ("agent", "That's right, the waiver comes around once a year.")],
    ],
    "once_ever": [
        [("member", "I was charged an overdraft fee of {new_fee}. Could you refund it as a courtesy?"),
         ("agent", "The courtesy waiver is a one-time courtesy for the life of the account, and yours has already been used, so I can't refund this one."),
         ("member", "Never again?"),
         ("agent", "Correct, it's once ever per account.")],
        [("member", "Can I get a courtesy refund on this {new_fee} overdraft fee?"),
         ("agent", "We waive an overdraft fee one time only, and your account already received that one-time waiver."),
         ("member", "So there won't be another?"),
         ("agent", "No. It's a single waiver for the lifetime of the account.")],
        [("member", "I'd like a courtesy refund for the {new_fee} overdraft fee, please."),
         ("agent", "The waiver is a one-time courtesy, once ever, and I see it was applied to your account before."),
         ("member", "That doesn't sound right to me."),
         ("agent", "That's how I understand it. Once ever, and it has been used.")],
    ],
    "partial_refund": [
        [("member", "I was charged an overdraft fee of {new_fee}. Could you refund it as a courtesy?"),
         ("agent", "I can't waive the full {new_fee}, but I can offer a partial refund of half the fee as a goodwill gesture."),
         ("member", "Half is better than nothing, I suppose."),
         ("agent", "I'll note the partial refund on your account.")],
        [("member", "Can I get a courtesy refund on this {new_fee} overdraft fee?"),
         ("agent", "Under the new policy I can only give a partial refund, half of the fee, rather than the full amount."),
         ("member", "Why only half?"),
         ("agent", "That's the partial refund I'm able to offer today.")],
        [("member", "I'd like a courtesy refund for the {new_fee} overdraft fee, please."),
         ("agent", "I'm not able to refund all of it, but I can do a partial refund of half the fee as a courtesy."),
         ("member", "Okay, I'll take the partial refund."),
         ("agent", "Understood, I've put the half refund request in.")],
    ],
}


def compose_event_turns(event_id: str, category: str, driver_id: str, variant: int,
                        misstatement: str | None = None) -> Template:
    """Intro plus body for one event call; `variant` picks the wording (0 to 2)."""
    intro = INTROS[(event_id, category)][variant]
    if misstatement is not None:
        body = MISSTATED_REFUSALS[misstatement][variant]
    else:
        body = BODIES[driver_id][variant]
    return [*intro, *body]
