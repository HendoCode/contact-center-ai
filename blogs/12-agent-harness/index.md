---
title: "The Agent Harness"
subtitle: "Putting gitagent.sh to work on a real codebase — from task prompt to pull request"
author: Stephen Henderson
date: "TBD"
post_number: "12"
description: "Post 12 of Anchoring AI: introducing gitagent.sh as an agent harness, connecting it to this codebase, and exploring what changes when you give an agent a task and the run of the repo rather than prompting it interactively."
prev_url: "../11-whats-next/"
prev_title: "What's Next"
next_url: ""
next_title: ""
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

Post 11 surveyed the agent framework landscape — GitAgent-style tools, LangGraph, Azure AI Foundry Agents — and explained why MCP-first architecture ages well regardless of which orchestration layer wins. This post stops surveying and starts doing.

[gitagent.sh](https://gitagent.sh) is an agent harness: a runtime that gives an AI agent a task, a repo, and the autonomy to execute across both. The distinction from a coding assistant is fundamental — you're not prompting interactively and reviewing each step. You're defining a task, handing it off, and getting back a result (code, a PR, a report). The agent plans, reads, writes, and commits without you in the loop.

This post walks through connecting gitagent.sh to this contact-center-ai codebase: what the harness is, how it's configured, what tasks are a good fit (and what aren't), and what actually happens when you point it at a real backlog item.

---

## Key Concepts

- **Agent harness vs. coding assistant** — the paradigm shift: interactive prompt/response vs. task-level autonomy; what "agentic" actually means in practice when the agent has file access, shell access, and git access
- **gitagent.sh** — what the harness provides: task definition format, repo context injection, tool surface (read/write/shell/git), output contract (diff, PR, report); how it compares to Claude Code CLI, Cursor Composer, and Windsurf Cascade
- **Task design** — what makes a good agent task: bounded scope, clear success criteria, recoverable if wrong; what makes a bad one: ambiguous requirements, tasks that need human judgement mid-execution
- **The MCP server as an agent target** — how this codebase's MCP tools become data sources for the agent harness itself: an agent can query call transcripts while implementing a feature that depends on understanding call patterns
- **Repo context and grounding** — how gitagent.sh injects CLAUDE.md, architecture docs, and file structure to ground the agent before it starts; why the work we did on CLAUDE.md pays off here
- **Reviewing agent output** — what to look for in an agent-generated diff; the human-in-the-loop checkpoint that matters most (before merge, not during execution)
- **The meta angle** — this entire codebase and blog series was built collaboratively with Claude Code; gitagent.sh represents the next step toward fully autonomous codebase evolution

---

## Planned Outline

1. **From assistant to harness** — the conceptual shift: what you give up (interactive control) and what you gain (delegation at task granularity); when that trade-off makes sense

2. **What gitagent.sh is** — the harness model: task file → agent execution → diff/PR output; what the agent can do (read files, run shell commands, write code, commit); what it can't (make architectural decisions without context, handle ambiguous requirements)

3. **Setting it up with this repo** — configuration walkthrough: pointing gitagent.sh at the contact-center-ai repo; CLAUDE.md as the primary grounding document; what environment the agent runs in

4. **Choosing a first task** — picking something from the actual backlog:
   - Fix the `query_csat()` category filter bug (known bug from Post 3, small scope, clear success criteria)
   - Or: implement the S3 deduplication guard from Post 5
   - Criteria: bounded, testable, consequence of getting it wrong is low

5. **Running the task** — what the execution looks like; what the agent reads first; how it plans; what it produces

6. **Reviewing the output** — reading the diff; what the agent got right; what needed adjustment; the human judgement calls that belong to the reviewer, not the agent

7. **The MCP-as-data-source angle** — an agent implementing a feature that queries call data through the MCP server to inform its implementation choices; the loop closes

8. **What this changes** — how the development workflow shifts when some tasks go to an agent; what stays human; the right mental model for agent harnesses as a team tool rather than a solo accelerator

---

## Code Changes for This Post

- Task definition file(s) for gitagent.sh (checked into repo under `agent-tasks/`)
- Fix `query_csat()` category filter bug (the task the agent runs — reviewed and merged if the output is correct)
- Any configuration added to support the harness (environment setup, task format)

---

## Outstanding Questions / TBD

- What format does gitagent.sh use for task definitions? Need to review https://gitagent.sh docs.
- Does gitagent.sh run the MCP server as a tool, or does it interact with the codebase directly?
- Is there a local execution mode (no external API required), or does it require a cloud runtime?
- Show a comparison: same task run by gitagent.sh vs. run interactively with Claude Code — same result? Different approach?
- Include the agent's actual output (the diff or PR) as a code block in the post?
