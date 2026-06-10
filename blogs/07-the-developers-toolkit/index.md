---
title: "The Developer's Toolkit"
subtitle: "REST API, OpenAPI/Swagger, PyCharm debugging, and JetBrains AI in the same workflow"
author: Stephen Henderson
date: "TBD"
post_number: "07"
description: "Post 7 of Anchoring AI: adding an HTTP REST API layer alongside the MCP server, documenting it with OpenAPI/Swagger, and exploring the JetBrains developer workflow — PyCharm debugging with Docker services, Junie for unit test generation and PEP8 cleanup, and HTTP Request files for live API testing."
prev_url: "../06-built-to-last/"
prev_title: "Built to Last"
next_url: "../08-what-should-we-measure/"
next_title: "What Should We Measure?"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

The MCP server is stdio-only. That's the right transport for Claude Desktop and agent integrations, but it's not how you test a backend in practice. You can't `curl` it. You can't wire it to a browser frontend. And you can't connect it to teams that aren't running an MCP-capable client.

This post adds an HTTP REST API layer alongside the existing MCP server — not replacing it, just adding a second surface. FastAPI handles the routing and generates an OpenAPI spec for free; Swagger UI gives you a browser-accessible test interface. Then we step back from the code and look at the tooling that made all of this possible: PyCharm's Docker-aware debugger, Junie for generating unit tests and enforcing idiomatic Python, and the HTTP Request feature for testing the new API without leaving the IDE.

There's also something worth saying about authoring all of this — including these blog posts — from inside the same JetBrains workspace as the code.

---

## Key Concepts

- **FastAPI** — async Python web framework; automatic OpenAPI 3.1 spec generation; Pydantic models as request/response schemas; why it's a natural fit alongside a LangChain-based backend
- **OpenAPI/Swagger** — what the spec actually contains; the `/docs` and `/redoc` endpoints FastAPI generates automatically; using the spec as a contract for frontend or third-party integration
- **Sharing tool logic** — the three MCP tools in `mcp/tools.py` become the same three REST endpoints; no duplication of business logic, just a new transport
- **PyCharm + Docker debugging** — attaching PyCharm's debugger to a running Docker container; setting breakpoints in `pipeline.py` during a live query; the Docker Compose service configuration for remote debugging
- **JetBrains Junie** — AI-assisted unit test generation; PEP8 and idiomatic Python suggestions; the difference between "AI writes the test" and "AI writes a test worth keeping"; code review workflow inside the IDE
- **HTTP Request files (`.http`)** — JetBrains' built-in HTTP client; `.http` files as living API documentation checked into the repo; running requests against local and production environments with environment variables
- **Writing blog posts in JetBrains** — Markdown preview, Git integration, running build.sh from the terminal — all from the same workspace as the Python source code; the case for not switching contexts
- **IDE landscape comparison** — JetBrains vs. Windsurf vs. Cursor vs. Copilot: agentic ("do this whole task") vs. assistant ("help with this selection") paradigms; where each tool is strongest; a tool-equivalence table so readers on other IDEs can translate the workflow

---

## The New Architecture

```
Claude Desktop ──(stdio)──► MCP Server (mcp/server.py)
                                │
HTTP Client ────(HTTP)───► REST API (api/server.py)  ─────► mcp/tools.py ──► rag_query()
                                                                          └──► csat (JSON)

Both surfaces share the same tool implementations.
```

---

## Planned Outline

1. **Why add REST alongside MCP** — the audiences that can't use stdio; testing and developer experience; frontend integration paths
2. **FastAPI in 30 lines** — a `api/server.py` that exposes the three tools as POST endpoints; Pydantic request/response models; `uvicorn` as the runner
3. **OpenAPI for free** — what FastAPI generates; navigating the Swagger UI; how the spec doubles as integration documentation for other teams
4. **Adding to docker-compose** — the `api` service; port exposure; shared environment variables with the MCP server
5. **PyCharm debugging with Docker** — configuring a remote interpreter; attaching to the API container; stepping through `rag_query()` during a live request; what changes when you debug through a Docker boundary vs. locally
6. **Junie: unit tests from the AI assistant** — generating tests for `query_csat()` and `search_transcripts()`; evaluating what Junie produces; fixing the category filter bug (known bug from Post 3) and watching the test catch it
7. **Junie: PEP8 and idiomatic cleanup** — what suggestions come out; which ones to accept; which ones are style preferences vs. correctness improvements
8. **HTTP Request files** — creating `api/requests.http`; environment variables for local vs. production; checking `.http` files into the repo as executable documentation
9. **Authoring in JetBrains** — the blog post you're reading was written in the same IDE; Markdown preview, terminal, Git — one window
10. **IDE comparison: JetBrains in context** — this workflow is one ecosystem, not the only one; Windsurf (Cascade, agentic multi-file flows), Cursor (VS Code fork + full-repo context, configurable model), GitHub Copilot (ubiquitous, completion-first); the key distinctions: agentic vs. assistant paradigm, depth of Docker/Python tooling, whether `.http`-style testing is built in or plugin-dependent; a tool-equivalence table so readers on other stacks can map each JetBrains feature to their own workflow

---

## Code Changes for This Post

- New `api/server.py` — FastAPI application with three endpoints mirroring the MCP tools
- New `api/models.py` — Pydantic request/response models
- Add `fastapi`, `uvicorn` to `pyproject.toml` dependencies
- Add `api` service to `docker-compose.yml`
- New `api/requests.http` — HTTP Request file for all three endpoints (checked into repo)
- Unit tests for `mcp/tools.py` (generated with Junie, then reviewed + fixed)
- Fix the `query_csat` category filter bug (Bug 2 from Post 3, tested by new test)

---

## IDE Comparison Reference

Framing for section 10: the JetBrains workflow is one valid path, not the only one. The post should give readers enough to translate it to their own stack.

**Paradigm distinction:**
- *Assistant* (Junie, Copilot): responds to specific prompts inside a file or selection — you direct every step
- *Agentic* (Windsurf Cascade, Cursor Composer): takes an instruction like "add rate limiting and write the tests" and executes across multiple files autonomously — faster for greenfield work, less control when the answer isn't obvious

**Landscape table (for the post):**

| Tool | Paradigm | Strongest at | Weakest at |
|---|---|---|---|
| PyCharm + Junie | Assistant | Deep Python tooling, Docker debugger, `.http` testing, single-window blog authoring | AI waits to be asked; no autonomous multi-file flows |
| Windsurf (Cascade) | Agentic | "Do this whole task" across multiple files; whole-repo context maintained automatically | Thinner Python-specific tooling; Docker debugging setup more manual |
| Cursor | Agentic/assistant hybrid | Best-in-class autocomplete with repo context; configurable model (Claude or GPT-4o); large VS Code plugin ecosystem | Less opinionated than Windsurf; still feels like VS Code + a smart copilot |
| GitHub Copilot | Assistant | Ubiquitous; good autocomplete; tight GitHub integration | Primarily completion-focused; Workspace (agentic mode) is early |
| Claude Code (CLI) | Agentic | Autonomous multi-step tasks across the whole repo | No GUI, no debugger, no `.http` runner — power tool, not daily driver |

**Tool equivalence table (for the post — end of section 10):**

| JetBrains feature | VS Code / Cursor equivalent | Windsurf equivalent |
|---|---|---|
| Junie (test gen + PEP8) | Copilot Chat / Cursor Composer | Cascade flow |
| HTTP Request files | REST Client extension (plugin) | External tool (Bruno, Insomnia) |
| Docker remote interpreter | Docker extension + `launch.json` | Docker extension + `launch.json` |
| Terminal + Markdown + Git | Built into VS Code | Built into Windsurf |

**Closing note for the section:** if your team is on Windsurf or Cursor, the `.http` files and Junie-generated tests translate directly — the workflows are the same; the tools have different names.

---

## Outstanding Questions / TBD

- Use FastAPI or Flask? (FastAPI is the clear choice for OpenAPI generation; Flask if simplicity is the argument)
- Add authentication to the REST API? (The MCP server relies on Entra Easy Auth at the infra layer — the REST API needs its own auth strategy if exposed beyond localhost)
- Include a section on the difference between the Swagger spec FastAPI generates and writing an OpenAPI spec by hand?
- Show Junie generating tests for the known bug in `get_call_summary()` as well?
- `debugpy` for Docker debugging or PyCharm's built-in Docker interpreter?
- For the IDE comparison: keep it as a short closing section, or pull it up earlier as context-setting for why JetBrains was chosen for this post?
