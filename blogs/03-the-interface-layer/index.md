---
title: "The Interface Layer"
subtitle: "MCP as enterprise glue: tools, routing, and why this architecture scales across teams"
author: Stephen Henderson
date: "TBD"
post_number: "03"
description: "Post 3 of Anchoring AI: MCP server internals — tool schemas, the stdio transport, connecting Claude Desktop, and why MCP is the right abstraction for a multi-team AI platform."
prev_url: "../02-from-text-to-vectors/"
prev_title: "From Text to Vectors"
next_url: "../04-run-anywhere/"
next_title: "Run Anywhere"
---

> **Status: Placeholder.** This post is planned. The outline and key concepts below describe what it will cover.

---

## What This Post Covers

The RAG pipeline from Post 2 is a library. What makes it useful is the MCP server in front of it. This post goes inside `mcp/server.py` and `mcp/tools.py`: what MCP is, how the stdio transport works, how tool schemas are defined, and why exposing this functionality as MCP tools — rather than a REST API or a standalone app — is the right design for an organization with multiple teams and multiple LLM vendors.

---

## Key Concepts

- **Model Context Protocol (MCP)** — what it is, why Anthropic designed it, how it differs from a REST API for AI tools
- **stdio transport** — how the client spawns the server process and communicates over stdin/stdout; why this is the local dev default
- **Tool schemas (inputSchema)** — how JSON Schema describes what each tool accepts; what Claude Desktop reads to decide when to call a tool
- **The `call_tool` router** — dispatching by tool name; why the routing is intentionally thin
- **TextContent as the return contract** — why all tools return text, not structured JSON, and what that means for the client
- **Reusability across teams** — why MCP is the right abstraction for a shared platform, not a team-specific app
- **Production transport** — how the stdio server becomes an HTTP server in Azure App Service

---

## Planned Outline

1. **What is MCP, and why does it matter** — the protocol's design intent; how it differs from OpenAI function calling and REST APIs
2. **The server entry point** — `server.py` end-to-end: imports, server initialization, `list_tools`, `call_tool`, `main()`
3. **Tool schema design** — the three tools' inputSchemas in detail; how Claude reads them to decide when and how to call a tool
4. **The routing layer** — `call_tool`'s dispatch logic; the intentional simplicity; where error handling lives
5. **Tool implementations** — `search_transcripts`, `get_call_summary`, `query_csat` side by side; the CSAT bypass explained
6. **Connecting Claude Desktop** — editing `claude_desktop_config.json`; watching tool calls in the Claude Desktop UI
7. **From stdio to HTTP** — how Azure App Service surfaces the same tools over HTTPS; the transport abstraction in the MCP SDK
8. **The multi-team angle** — why MCP tools are the right unit of sharing; how a compliance team would add their own tools alongside these three

---

## Code Changes for This Post

No new code changes — this post documents the existing `mcp/server.py` and `mcp/tools.py`. May include a worked example of adding a fourth tool as an exercise.

---

## Outstanding Questions / TBD

- Show a live Claude Desktop screenshot?
- Walk through the MCP SDK source code briefly to explain how `stdio_server` works?
- Include the `claude_desktop_config.json` snippet?
