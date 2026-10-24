You read a contact-center supervisor's question about a change and pull out the parts of it. Use only the question and the logged changes below. Give null for any part the question does not state.

- measure: the thing that moved, in the question's own words ("satisfaction", "calls", "handle time"). null if the question names none.
- subject: which calls or members it is about, in the question's own words ("mortgage calls", "branch members"). null if the question names none.
- when: the date or period the question gives ("late July", "since April", "2026-06-15", "June 15"). null if it gives none, or if it points at a logged change instead.
- logged_change: the name of the one logged change the question refers to, copied exactly from the list, or null. Choose one only when the question points at it by name or by describing it; a date or a period alone does not choose one.
- follow_up: true only when a change analysis already exists on this thread and the question builds on it (for example, asking what to do about what it found) instead of asking a new why-did-it-change question.

Logged changes:
{change_log}

A change analysis already exists on this thread: {has_prior_change}

Examples (the changes named here are not in your list):
Question: Why did satisfaction on mortgage calls fall after the March rate change?
measure: satisfaction; subject: mortgage calls; when: March; logged_change: null; follow_up: false
Question: Did handle time recover by August?
measure: handle time; subject: null; when: August; logged_change: null; follow_up: false
Question: What should we coach on for those calls? (an analysis exists on this thread)
measure: null; subject: null; when: null; logged_change: null; follow_up: true

Question: {question}
