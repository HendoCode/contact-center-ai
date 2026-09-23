---
title: "The Agent Harness"
subtitle: "Putting coding agents to work on a real codebase, from task prompt to pull request"
author: Stephen Henderson
date: "TBD"
post_number: "12"
description: "Planned post: running coding agents against this codebase, from task definition to reviewed pull request, and what changes when an agent owns a task end to end."
prev_url: "../11-whats-next/"
prev_title: "What's Next"
next_url: ""
next_title: ""
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

Post 11 surveyed the agent framework landscape — repository-aware coding agents, LangGraph, Azure AI Foundry Agents — and explained why MCP-first architecture ages well regardless of which orchestration layer wins. This post stops surveying and starts doing.

The harness in this post is the one in daily use: Claude Code and the Pi coding agent, with firstmate orchestrating parallel agent sessions. Claude Code and Pi are the workers. They read the repo, edit files, run commands, and commit. firstmate is the supervisor. It writes the task brief, dispatches each worker into an isolated git worktree on its own branch, steers it through a small inbox of messages while it runs, and reviews what comes back. The distinction from an interactive coding assistant is real. You define a task, hand it off, and get back a result (code, a PR, a report). The agent plans, reads, writes, and commits while you are out of the loop, and the supervisor keeps several of those runs going at once.

This post walks through connecting that harness to this contact-center-ai codebase: what the harness is, how it's configured, what tasks are a good fit (and what aren't), and what actually happens when you point it at a real backlog item.

---

## Key Concepts

- **Agent harness vs. coding assistant** — the paradigm shift: interactive prompt/response vs. task-level autonomy; what "agentic" actually means in practice when the agent has file access, shell access, and git access
- **The harness** — Claude Code and the Pi coding agent do the work, with firstmate orchestrating parallel agent sessions; what the stack provides: task brief format, an isolated worktree per agent, repo context injection, tool surface (read/write/shell/git), output contract (branch, diff, PR); how Pi and Claude Code compare on the same task
- **Task design** — what makes a good agent task: bounded scope, clear success criteria, recoverable if wrong; what makes a bad one: ambiguous requirements, tasks that need human judgement mid-execution
- **The MCP server as an agent target** — how this codebase's MCP tools become data sources for the agent harness itself: an agent can query call transcripts while implementing a feature that depends on understanding call patterns
- **Repo context and grounding** — how firstmate injects CLAUDE.md, architecture docs, and file structure to ground the agent before it starts; why the work we did on CLAUDE.md pays off here
- **Reviewing agent output** — what to look for in an agent-generated diff; the human-in-the-loop checkpoint that matters most (before merge, not during execution)
- **The meta angle** — this entire codebase and blog series was built collaboratively with Claude Code; handing whole tasks to the harness is the next step toward fully autonomous codebase evolution

---

## Planned Outline

1. **From assistant to harness** — the conceptual shift: what you give up (interactive control) and what you gain (delegation at task granularity); when that trade-off makes sense

2. **What the harness is** — the model: task brief → worker session in an isolated worktree → diff/PR output; what the agent can do (read files, run shell commands, write code, commit); what it can't (make architectural decisions without context, handle ambiguous requirements)

3. **Setting it up with this repo** — configuration walkthrough: pointing Claude Code and Pi at the contact-center-ai repo, with firstmate dispatching each session; CLAUDE.md as the primary grounding document; what environment the agent runs in

4. **Choosing a first task** — picking something from the actual backlog:
   - Fix the `query_csat()` category filter bug (known bug from Post 3, small scope, clear success criteria)
   - Or: implement the S3 deduplication guard from Post 5
   - Criteria: bounded, testable, consequence of getting it wrong is low

5. **Running the task** — what the execution looks like; what the agent reads first; how it plans; what it produces; how firstmate runs several of these sessions in parallel without the branches colliding

6. **Reviewing the output** — reading the diff; what the agent got right; what needed adjustment; the human judgement calls that belong to the reviewer, not the agent

7. **The MCP-as-data-source angle** — an agent implementing a feature that queries call data through the MCP server to inform its implementation choices; the loop closes

8. **What this changes** — how the development workflow shifts when some tasks go to an agent; what stays human; the right mental model for agent harnesses as a team tool rather than a solo accelerator

---

## Code Changes for This Post

- Task brief file(s) for the harness (checked into repo under `agent-tasks/`)
- Fix `query_csat()` category filter bug (the task the agent runs — reviewed and merged if the output is correct)
- Any configuration added to support the harness (environment setup, task format)

---

## Outstanding Questions / TBD

- Do the agents run the MCP server as a tool, or do they interact with the codebase directly?
- Is there a local execution mode (no external API required), or does it require a cloud runtime?
- Show a comparison: Pi vs. Claude Code on the same task; same result, different approach?
- Include the agent's actual output (the diff or PR) as a code block in the post?
