---
title: "What's Next"
subtitle: "GitAgent, LangGraph, Azure AI Foundry — and why the MCP-first approach stays durable"
author: Stephen Henderson
date: "TBD"
post_number: "11"
description: "Post 11 of Anchoring AI: comparing the handcrafted MCP approach with the emerging landscape of AI agent frameworks — GitAgent, LangGraph, Azure AI Agents, GitHub Copilot Workspace — and what to look for when choosing."
prev_url: "../10-trust-but-verify/"
prev_title: "Trust, but Verify"
next_url: ""
next_title: ""
---

> **Status: Structured draft.** This post has a detailed outline and will be written after Posts 02–08 are complete.

---

## Thesis

The system we built — a handcrafted MCP tool server with a RAG pipeline — is a deliberate choice for a specific context: multiple teams, multiple LLM vendors, a need for reuse across use cases, and a client not yet ready to standardize. Every design decision in this series has been shaped by those constraints.

But the landscape is shifting fast. GitAgent-style coding agents, managed agent runtimes from Microsoft and OpenAI, and stateful orchestration frameworks like LangGraph are changing what "build your own" looks like — and what it competes with. This final post maps the next horizon: what these tools are, where they fit, and whether the nine posts of work you just read still make sense.

The short answer: yes. Here's why.

---

## Key Concepts

- **Agentic AI vs. RAG tools** — the shift from "query a tool and get an answer" to "agent plans and executes multi-step tasks autonomously"
- **GitAgent / GitHub Copilot Workspace** — AI agents that operate on repositories: read code, write PRs, run tests, respond to issues; how they could use the MCP server as a data source *or* how a coding agent could evolve this codebase autonomously
- **LangGraph** — stateful, multi-step agent orchestration built on LangChain (already a dependency); when a graph of steps is the right abstraction over a simple RAG chain
- **Azure AI Foundry Agents** — Microsoft's managed agent runtime; how it compares to self-hosted MCP; what "managed" gets you and what it costs
- **OpenAI Assistants API** — the OpenAI-native alternative to MCP; threads, runs, and tool calls; trade-offs vs. the protocol-based approach
- **MCP as the stable adapter layer** — regardless of which agent framework wins, MCP tools remain the interface; the work in this series doesn't get thrown away when the orchestration layer changes
- **The consolidation question** — Northgate Federal Credit Union was going to consolidate GenAI efforts in 3–6 months; what criteria should drive that decision?

---

## Planned Outline

1. **Where we started and where we are** — recap the nine-post arc; what was built, what it cost in time and API calls, what it taught about RAG system design

2. **The agentic shift** — RAG tools answer questions; agents take actions. The call center supervisor use case has natural next steps:
   - *"Find all fraud calls from last month and draft a summary report"* — multi-step task, not a query
   - *"Flag any call where an agent promised something outside policy"* — needs reasoning across multiple retrieved documents
   - These tasks require an agent that can plan, retrieve, reason, and act — not just `rag_query()`

3. **GitAgent and coding agents**
   - What GitAgent-style tools do: repo-aware, code-gen, PR automation, issue triage
   - How they could integrate with this MCP server: a coding agent queries call data to understand what's failing before opening a fix PR
   - The meta case: using Claude Code (or a similar coding agent) to evolve this codebase autonomously — we already did this during development
   - The implication: the MCP server is not just for call center supervisors; it's a data source for any AI agent that needs call center context

4. **Framework comparison**

   | Framework | Strengths | Weaknesses | Fit for this project |
   |---|---|---|---|
   | Custom MCP (built here) | Maximum control, reusable, protocol-standard | You own the ops; stateless by default | ✅ Right for multi-team shared access |
   | LangGraph | Stateful, graph-based agents; built on LangChain (already a dep) | Adds complexity; opinionated | ✅ Right next step for multi-step agent tasks |
   | Azure AI Foundry Agents | Managed, Azure-native; built-in persistence | Vendor lock-in; cost; less flexibility | ⚠️ Evaluate in 6 months |
   | OpenAI Assistants | Mature API, good tooling; threads & runs | No Ollama fallback; OpenAI-only | ⚠️ If committing to OpenAI |
   | GitHub Copilot Workspace | Excellent for code-centric tasks | Not designed for domain data query | ❌ Wrong tool for call center RAG |

5. **The MCP advantage** — MCP is an interface standard, not an implementation. Whichever orchestration layer wins in 12 months, the three tools defined in `mcp/tools.py` remain the stable API surface. The investment is durable in a way that rewriting to a specific framework's native tool format is not.

6. **Criteria for the consolidation decision** — a decision matrix for Northgate Federal Credit Union (and any similar organization):
   - Team size and AI expertise (small team → managed > self-hosted)
   - Data sensitivity (PII, regulated data → local Ollama or private cloud > external API)
   - Vendor contracts already in place (Azure EA → Azure AI Foundry becomes cheaper at the margin)
   - Time-to-market pressure (six weeks to demo → build on existing MCP > rewrite to new framework)
   - Consolidation timeline (< 6 months → don't optimize for the future state; > 12 months → design for it)

7. **Our recommendation for Northgate Federal**
   - *Stay on MCP + add LangGraph for multi-step agent tasks* — the existing tools become nodes in a LangGraph workflow; no rewrite required
   - *Evaluate Azure AI Foundry in 6 months* after the provider consolidation decision is final; don't pre-optimize
   - *Revisit CSAT integration* once the 3rd-party provider transition unblocks; a SQL-backed approach (not in-memory JSON) is the right upgrade
   - *Add the S3 ingestion path* once the data pipeline is productionized; the stub in `pipeline.py` is ready to fill in

8. **The eleven-post arc, complete**

   | Post | What we built | What we learned |
   |---|---|---|
   | 01 | Architecture and diagrams | The value of a clear system context before writing code |
   | 02 | RAG pipeline + pgvector | Embeddings are powerful; dimensions matter; no free dedup |
   | 03 | MCP server and tools | Protocols beat APIs for multi-team AI platforms |
   | 04 | Ollama provider swap | Env-var abstraction is worth the upfront design cost |
   | 05 | S3 ingestion + MinIO | Object storage ingestion is worth completing before you go to production |
   | 06 | Terraform / Azure / Entra | Auth at the infra layer beats auth in the app |
   | 07 | REST API + JetBrains toolkit | The right IDE makes the whole stack more debuggable |
   | 08 | Observability design | What to measure matters more than how to measure it |
   | 09 | OTel + Grafana | Instrumentation is cheap; the hard part is defining good thresholds |
   | 10 | Validation + drift detection | RAG systems need ongoing validation, not just initial testing |
   | 11 | Landscape comparison | MCP-first ages well; the interface standard outlives the implementation |

9. **What to read next** — links to: LangGraph documentation, MCP spec, Ragas (RAG evaluation), Azure AI Foundry overview, OpenTelemetry Python SDK

---

## Code Changes for This Post

None — this is a retrospective and forward-looking design post. No new code added.

---

## Outstanding Questions / TBD

- Include a live demo of adding a LangGraph workflow on top of the existing MCP tools?
- Compare response quality: single RAG query vs. LangGraph multi-step agent on the same question?
- Discuss Claude Code specifically as a GitAgent-style tool and how it was used to build this very codebase?
