# ccai_mcp

The MCP server: five tools (`search_transcripts`, `get_call_summary`, `query_csat`, `query_metric`, `ask_the_analyst`) over two transports. Tool names and input schemas are identical on both.

| Transport | How to start | Use it for |
|---|---|---|
| stdio (default) | `python -m ccai_mcp.server` | a client on the same machine that spawns the server (Claude Desktop, the agent) |
| streamable HTTP (opt in) | `python -m ccai_mcp.server --http` or `MCP_TRANSPORT=http` | clients on other machines: register a URL |

Built on `mcp` 2.2.0 (`Server.streamable_http_app()`, served by uvicorn). Code: `server.py` (tools, transport choice), `http_transport.py` (settings, bearer check, `/healthz`), `http_smoke.py` (a client for checking it).

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
- **`MCP_AUTH_UPSTREAM=true`** is the one other way past the refusal. It declares that an authenticating proxy is the only way to reach the server. The Azure `mcp-server` app sets it, because Easy Auth (Entra) owns the `Authorization` header there and an Entra token would never match a static one. Do not set it for any other reason.

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

Claude Desktop's `claude_desktop_config.json` only starts local (stdio) servers; it does not take a remote URL directly. Two routes:

**1. The `mcp-remote` bridge, for a LAN or localhost server.** It runs locally, speaks stdio to Claude Desktop and HTTP to the server. Needs Node.js. Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), then restart Claude Desktop:

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
| Real MCP client (`mcp` 2.2.0) over HTTP | tested in `tests/test_server_http.py`: handshake, five tools, a call, 401s |
| `mcp-remote` 0.14.3 (the Claude Desktop route 1 bridge) | run by hand against this server with the header form above: `tools/list` returned the five tools. Claude Desktop itself was not run |
| Claude Desktop custom connector, Cursor, Open WebUI | **not run.** The snippets follow each product's current docs (Anthropic's custom-connector help page, Cursor's MCP docs, Open WebUI's MCP docs, read 2026-10-02). The `mcp-server` service name from inside the `open-webui` container is also untested |

## Tests

`tests/test_server_http.py` runs offline: settings and the refusal rules, the bearer middleware, a real MCP client over an ephemeral loopback port (tools listed, one call with a stubbed tool), 401s, and the `python -m ccai_mcp.server --http` entrypoint as a subprocess. `tests/test_server_handshake.py` still guards the stdio handshake and the tool schemas.
