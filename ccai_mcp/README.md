# ccai_mcp

The MCP server: six tools (`search_transcripts`, `get_call_summary`, `query_csat`, `query_metric`, `ask_the_analyst`, `explain_change`) over two transports. Tool names and input schemas are identical on both.

| Transport | How to start | Use it for |
|---|---|---|
| stdio (default) | `python -m ccai_mcp.server` | a client on the same machine that spawns the server (Claude Desktop, the agent) |
| streamable HTTP (opt in) | `python -m ccai_mcp.server --http` or `MCP_TRANSPORT=http` | clients on other machines: register a URL |

Built on `mcp` 2.2.0 (`Server.streamable_http_app()`, served by uvicorn). Code: `server.py` (tools, transport choice), `change.py` (`explain_change`), `http_transport.py` (settings, bearer check, `/healthz`), `http_smoke.py` (a client for checking it).

## `explain_change`

Why did a governed metric move around a change? The tool compares one metric (`call_volume`, `csat_average_score`, `first_contact_resolution_rate` or `average_handle_time`) before and after a change point, reads the calls in both windows, and answers with:

- both windows' values and the SQL MetricFlow generated for each;
- how the change point was found: given as a date, the one logged change in a period (`search_range`), or the largest weekly shift, snapped to a logged change within 7 days. A shift that is not clear gives "no clear change point" and a request for a date;
- the drivers found in the calls after the change, each as "n of M calls in the after window" with its count before the change and up to three verbatim quotes with call ids. A model proposes and assigns the drivers; code counts them and keeps only quotes found in the call;
- what the data could not determine, always including that it cannot show the change caused the shift.

Example call (needs the seeded database and a chat model; a CPU-only local model takes minutes):

```bash
python -m ccai_mcp.http_smoke --call explain_change '{"metric": "csat_average_score", "filters": {"category": ["fee_dispute"]}, "change_point": "2026-04-15", "window_days": 14, "question": "Why did satisfaction on fee calls drop after the fee schedule change?"}'
```

It never reports a per-agent score. `mode="coaching"` takes `groups` (call ids from an earlier answer) and reports patterns in how those calls were handled. The result is two text blocks: the cited answer, then `CHANGE RESULT (JSON, v1):` and the result as JSON. `change_log.json` and `branches.json` are read from `data/synthetic/` (see `agent/changes.py`); the windows are half-open ISO dates. Code: `change.py`; the agent's route is described in [agent/README.md](../agent/README.md).

## Streamable HTTP

```bash
export MCP_AUTH_TOKEN="$(openssl rand -hex 32)"   # keep it; clients send it back
python -m ccai_mcp.server --http                  # http://127.0.0.1:8000/mcp
python -m ccai_mcp.http_smoke                     # lists the tools (reads MCP_SERVER_URL, MCP_AUTH_TOKEN)
python -m ccai_mcp.http_smoke --call get_call_summary '{"call_id": "CALL-00042"}'
```

**URL shape:** `http://<host>:<port>/mcp` (`https://` behind TLS). `GET /healthz` returns `200 ok` without auth, for container probes. The server is stateless and answers with plain JSON, so a restart or a scale-to-zero never leaves a client holding a dead session.

### Safe by default

This puts database-backed tools on a network, so:

- **stdio stays the default.** HTTP starts only when asked (`--http` or `MCP_TRANSPORT=http`; a flag wins over the variable).
- **Binds `127.0.0.1`.** `MCP_SERVER_HOST` can change that, but any non-loopback host (`0.0.0.0`, a LAN address, a hostname) refuses to start unless `MCP_AUTH_TOKEN` is set. The refusal names the variable and exits non-zero.
- **With a token, every request except `/healthz` needs `Authorization: Bearer <token>`.** A missing or wrong token is a `401` with `WWW-Authenticate: Bearer`, returned before MCP sees the request. The check is a constant-time compare, and the token is never logged.
- **`MCP_AUTH_UPSTREAM=true`** is the one other way past the refusal, and only on Azure Container Apps. It declares that an authenticating proxy is the only way to reach the server, and it is honored only when at least two of the variables the platform injects into every app container (`CONTAINER_APP_NAME`, `CONTAINER_APP_REVISION`, `CONTAINER_APP_HOSTNAME`, `CONTAINER_APP_ENV_DNS_SUFFIX`, `CONTAINER_APP_PORT`, `CONTAINER_APP_REPLICA_NAME`; [docs](https://learn.microsoft.com/en-us/azure/container-apps/environment-variables)) are non-empty. Without them the server refuses to start and names what is missing; at startup it logs which path was taken (variable names only). The Azure `mcp-server` app sets the flag, because Easy Auth (Entra) owns the `Authorization` header there and an Entra token would never match a static one. Do not set it for any other reason. The env-var signal is a misconfiguration guard, not a security boundary: anyone who controls the environment can set it. Easy Auth in front of the app is the real control.

**A static bearer token is demo-grade.** It has no expiry, no per-user identity and no revocation short of changing it everywhere, and it travels in clear text over plain `http://`. Use it on a trusted LAN, or behind TLS (a reverse proxy such as Caddy or nginx, or Azure ingress) for anything else. The Azure deployment does not use it: Easy Auth validates Entra tokens in front of the container (`infra/azure/modules/apps`). OAuth and TLS termination in the server itself are out of scope.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_SERVER_HOST` | `127.0.0.1` | HTTP bind address |
| `MCP_SERVER_PORT` | `8000` | HTTP port |
| `MCP_AUTH_TOKEN` | empty | bearer token; required for a non-loopback bind |
| `MCP_AUTH_UPSTREAM` | `false` | an authenticating proxy fronts the server (Azure Easy Auth) |
| `MCP_PUBLISH_HOST` | `127.0.0.1` | Compose `mcp-server` only: interface its port is published on |
| `MCP_SERVER_URL` | `http://127.0.0.1:8000/mcp` | client side: where `http_smoke` connects |

### Compose

```bash
echo "MCP_AUTH_TOKEN=$(openssl rand -hex 32)" >> .env
docker compose --profile app up -d mcp-server     # db, seed and dbt run first (several minutes the first time)
python -m ccai_mcp.http_smoke                     # with MCP_AUTH_TOKEN exported
```

The service is in the `app` profile, so a bare `docker compose up -d` is unchanged. Inside the container the server binds `0.0.0.0`, so it will not start without `MCP_AUTH_TOKEN`. The port is published on `127.0.0.1` only; set `MCP_PUBLISH_HOST=0.0.0.0` to serve other machines, and put TLS in front.

### Azure

`infra/azure/modules/apps` has a count-gated `mcp-server` Container App (`enable_mcp_server`, off by default) that runs `python -m ccai_mcp.server --http` behind Easy Auth. Clients send an Entra access token as the bearer token. Nothing here applies it; see that module's README for what Stephen runs.

## Registering a client

Replace `<host>` and `<token>`. Prefer an environment variable over pasting the token into a file you might commit.

### Claude Desktop

`claude_desktop_config.json` is at `~/Library/Application Support/Claude/` (macOS) or `%APPDATA%\Claude\` (Windows); restart Claude Desktop after editing it. It only starts local (stdio) servers and does not take a remote URL directly, so there are three routes. Route 0 is the one to use for `explain_change` on the same machine as the repo.

**0. stdio, the server on the same machine.** Claude Desktop starts `python -m ccai_mcp.server` itself. Replace `/path/to/contact-center-ai`. The database must be seeded and the dbt marts built (`make up dev-data`), and the chat and embedding models must be reachable, because `explain_change` asks a model to label the drivers. `uv` must be on the PATH Claude Desktop sees; if it is not, put the full path to `uv` in `command`.

```json
{
  "mcpServers": {
    "contact-center": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/contact-center-ai",
        "--group",
        "dbt",
        "python",
        "-m",
        "ccai_mcp.server"
      ],
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/contactcenter",
        "LLM_PROVIDER": "ollama",
        "EMBEDDING_PROVIDER": "ollama"
      }
    }
  }
}
```

Then ask Claude Desktop: "Use explain_change on csat_average_score for fee_dispute calls, change point 2026-04-15, and tell me why satisfaction moved." The first text block is the cited answer.

Two more routes cover a server on another machine:

**1. The `mcp-remote` bridge, for a LAN or localhost server.** It runs locally, speaks stdio to Claude Desktop and HTTP to the server. Needs Node.js. Add this to the same file:

```json
{
  "mcpServers": {
    "contact-center": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "http://<host>:8000/mcp",
        "--allow-http",
        "--header",
        "Authorization:${AUTH_HEADER}"
      ],
      "env": {
        "AUTH_HEADER": "Bearer <token>"
      }
    }
  }
}
```

`--allow-http` is needed for a plain `http://` URL and `mcp-remote` documents it for trusted networks only; drop it for `https://`. The token sits in an environment variable and the header has no space after the colon, which is `mcp-remote`'s documented workaround for argument quoting on Windows.

**2. A custom connector (Customize → Connectors → Add custom connector).** Anthropic's docs say the connection is made from Anthropic's cloud, not from your machine, so the URL must be an `https://` endpoint reachable from the public internet. A `localhost` or LAN address will not work. Static credentials such as a bearer token are one of its documented sign-in options.

### Cursor

`~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project; add it to `.gitignore` if it holds a token). Cursor expands `${env:NAME}` in `url` and `headers`:

```json
{
  "mcpServers": {
    "contact-center": {
      "url": "http://<host>:8000/mcp",
      "headers": {
        "Authorization": "Bearer ${env:MCP_AUTH_TOKEN}"
      }
    }
  }
}
```

Export `MCP_AUTH_TOKEN` in the environment Cursor is launched from, or write the token in place of the variable.

### Open WebUI

Open WebUI supports MCP natively from v0.6.31. Admin only: **Settings → Admin → Integrations → External Tool Servers → + Add Connection**, then:

| Field | Value |
|---|---|
| Type | MCP (Streamable HTTP) |
| Server URL | `http://<host>:8000/mcp` |
| Auth | Bearer |
| Key | the token (leave Auth on None if the server has none; Bearer with an empty key sends a broken header) |

The request comes from the Open WebUI **backend**, not your browser. If Open WebUI runs in Docker and the MCP server on the host, use `http://host.docker.internal:8000/mcp` (the repo's Compose file already maps that name). From the repo's own Compose `open-webui` container, `http://mcp-server:8000/mcp` should resolve to the `mcp-server` service when that service is running.

### What is verified

| Client | Status |
|---|---|
| Real MCP client (`mcp` 2.2.0) over HTTP | tested in `tests/test_server_http.py`: handshake, six tools, a call, 401s |
| `mcp-remote` 0.14.3 (the Claude Desktop route 1 bridge) | run by hand against this server with the header form above: `tools/list` returned the five tools it had then. Claude Desktop itself was not run |
| Claude Code CLI 2.1.310 as the client, stdio server, one `explain_change` call (2026-10-25, on `2df810a` plus the replay change) | ran: the CLI started `python -m ccai_mcp.server` over stdio, called `explain_change` for `csat_average_score` on `fee_dispute` calls at 2026-04-15, and returned the cited answer (both windows, SQL-backed values, a driver with three verbatim quotes). The database, MetricFlow and the tool were real; the chat and embedding models were deterministic stand-ins, so the driver label is not a model's reading |
| Claude Desktop, stdio config above | **not run.** The config follows Claude Desktop's documented `mcpServers` format |
| Claude Desktop custom connector, Cursor, Open WebUI | **not run.** The snippets follow each product's current docs (Anthropic's custom-connector help page, Cursor's MCP docs, Open WebUI's MCP docs, read 2026-10-02). The `mcp-server` service name from inside the `open-webui` container is also untested |

## Tests

`tests/test_server_http.py` runs offline: settings and the refusal rules, the bearer middleware, a real MCP client over an ephemeral loopback port (tools listed, one call with a stubbed tool), 401s, and the `python -m ccai_mcp.server --http` entrypoint as a subprocess. `tests/test_server_handshake.py` still guards the stdio handshake and the tool schemas.
