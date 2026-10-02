"""
M1: the streamable-HTTP transport, offline.

Real MCP client over a real loopback socket against the real server app. No database and
no LLM key: the one successful tool call runs against a stubbed `query_metric`, and the
subprocess test calls a tool that fails argument validation before touching anything.

What this guards:
  * stdio stays the default (the handshake tests cover that path; here the env/flag selection);
  * a non-loopback bind refuses to start without MCP_AUTH_TOKEN;
  * a missing or wrong bearer token is a 401 before any MCP handling;
  * the same five tools, with the same schemas, are served over the wire.
"""

import asyncio
import contextlib
import json
import socket
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from ccai_mcp import server as ccai_server
from ccai_mcp.http_transport import (
    BearerAuthMiddleware,
    ConfigError,
    HttpSettings,
    build_app,
    is_loopback,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SNAPSHOT = Path(__file__).parent / "fixtures" / "mcp_tool_schemas.json"
TOKEN = "test-token-not-a-secret"
EXPECTED_TOOLS = set(json.loads(SCHEMA_SNAPSHOT.read_text()))


# ── settings ─────────────────────────────────────────────────────────────────

def test_defaults_bind_loopback_without_a_token():
    s = HttpSettings.from_env({})
    assert (s.host, s.port, s.token) == ("127.0.0.1", 8000, None)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.20", "mcp.example.com"])
def test_non_loopback_without_a_token_is_refused(host):
    with pytest.raises(ConfigError, match="MCP_AUTH_TOKEN"):
        HttpSettings.from_env({"MCP_SERVER_HOST": host})


def test_blank_token_counts_as_unset():
    with pytest.raises(ConfigError):
        HttpSettings.from_env({"MCP_SERVER_HOST": "0.0.0.0", "MCP_AUTH_TOKEN": "   "})


def test_non_loopback_with_a_token_or_declared_upstream_auth_is_allowed():
    assert HttpSettings.from_env({"MCP_SERVER_HOST": "0.0.0.0", "MCP_AUTH_TOKEN": TOKEN}).token == TOKEN
    assert HttpSettings.from_env({**UPSTREAM_ENV, **ACA_SIGNAL}).auth_upstream


ACA_SIGNAL = {"CONTAINER_APP_NAME": "mcp-server", "CONTAINER_APP_REVISION": "mcp-server--abc123"}
UPSTREAM_ENV = {"MCP_SERVER_HOST": "0.0.0.0", "MCP_AUTH_UPSTREAM": "true"}


def test_upstream_flag_without_platform_signal_is_refused():
    with pytest.raises(ConfigError, match="CONTAINER_APP_NAME"):
        HttpSettings.from_env(UPSTREAM_ENV)


def test_platform_signal_without_the_flag_still_needs_a_token():
    with pytest.raises(ConfigError, match="MCP_AUTH_TOKEN"):
        HttpSettings.from_env({"MCP_SERVER_HOST": "0.0.0.0", **ACA_SIGNAL})


@pytest.mark.parametrize(
    "partial",
    [{"CONTAINER_APP_NAME": "mcp-server"}, {"CONTAINER_APP_NAME": "mcp-server", "CONTAINER_APP_REVISION": "  "}],
)
def test_upstream_flag_with_a_partial_signal_is_refused(partial):
    with pytest.raises(ConfigError, match="at least 2"):
        HttpSettings.from_env({**UPSTREAM_ENV, **partial})


def test_upstream_flag_with_full_signal_records_which_variables_matched():
    s = HttpSettings.from_env({**UPSTREAM_ENV, **ACA_SIGNAL})
    assert s.platform_signal == ("CONTAINER_APP_NAME", "CONTAINER_APP_REVISION")
    assert s.auth_mode == "upstream proxy"


def test_a_token_still_works_without_any_signal():
    assert HttpSettings.from_env({"MCP_SERVER_HOST": "0.0.0.0", "MCP_AUTH_TOKEN": TOKEN}).auth_mode == "bearer token"


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost"])
def test_loopback_hosts(host):
    assert is_loopback(host)
    HttpSettings.from_env({"MCP_SERVER_HOST": host})


def test_bad_port_is_refused():
    with pytest.raises(ConfigError):
        HttpSettings.from_env({"MCP_SERVER_PORT": "http"})
    with pytest.raises(ConfigError):
        HttpSettings.from_env({"MCP_SERVER_PORT": "70000"})


# ── bearer middleware, unit level ────────────────────────────────────────────

async def _call(app, headers: list[tuple[bytes, bytes]]) -> int:
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}
    await app(scope, receive, send)
    return sent[0]["status"]


@pytest.mark.asyncio
async def test_bearer_middleware_never_reaches_the_app_without_the_right_token():
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})

    app = BearerAuthMiddleware(inner, TOKEN)
    token = TOKEN.encode()
    bad = [
        [],
        [(b"authorization", b"Bearer wrong")],
        [(b"authorization", b"Bearer " + token + b"x")],
        [(b"authorization", b"Bearer " + token[:-1])],
        [(b"authorization", b"Basic " + token)],
        [(b"authorization", token)],
        [(b"authorization", b"Bearer")],
    ]
    for headers in bad:
        assert await _call(app, headers) == 401, headers
    assert reached == []

    assert await _call(app, [(b"authorization", b"Bearer " + token)]) == 200
    assert await _call(app, [(b"authorization", b"bearer " + token)]) == 200
    assert reached == ["/mcp", "/mcp"]


# ── real client over a real socket ───────────────────────────────────────────

@contextlib.asynccontextmanager
async def _serving(settings: HttpSettings):
    """The real app on an ephemeral loopback port; yields its base URL."""
    config = uvicorn.Config(
        build_app(ccai_server.app, settings), host="127.0.0.1", port=0, log_level="warning"
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started or task.done():
                break
            await asyncio.sleep(0.05)
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


def _client(token: str | None):
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return create_mcp_http_client(headers=headers)


@contextlib.asynccontextmanager
async def _session(base: str, token: str | None):
    async with (
        _client(token) as http,
        streamable_http_client(f"{base}/mcp", http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


@pytest.mark.asyncio
async def test_lists_the_five_tools_and_calls_one_over_http(monkeypatch):
    calls = []

    def fake_query_metric(**kwargs):
        calls.append(kwargs)
        return "average_mortgage_note_rate\n6.5"

    monkeypatch.setattr(ccai_server, "query_metric", fake_query_metric)
    async with _serving(HttpSettings(token=TOKEN)) as base, _session(base, TOKEN) as session:
        listed = (await session.list_tools()).tools
        assert {t.name for t in listed} == EXPECTED_TOOLS

        wire = {
            t.name: {
                "description": t.description,
                "inputSchema": t.model_dump(by_alias=True, exclude_none=True)["inputSchema"],
            }
            for t in listed
        }
        assert wire == json.loads(SCHEMA_SNAPSHOT.read_text())

        result = await session.call_tool("query_metric", {"metrics": ["average_mortgage_note_rate"]})

    assert not result.is_error
    assert result.content[0].text == "average_mortgage_note_rate\n6.5"
    assert calls[0]["metrics"] == ["average_mortgage_note_rate"]


@pytest.mark.asyncio
async def test_bad_arguments_are_a_tool_error_over_http_too():
    async with _serving(HttpSettings(token=TOKEN)) as base, _session(base, TOKEN) as session:
        result = await session.call_tool("get_call_summary", {})
    assert result.is_error
    assert "call_id" in result.content[0].text


def _post(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str]]:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers)


@pytest.mark.asyncio
async def test_missing_or_wrong_token_is_401_and_healthz_stays_open():
    async with _serving(HttpSettings(token=TOKEN)) as base:
        url = f"{base}/mcp"
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Basic {TOKEN}"}):
            status, resp_headers = await asyncio.to_thread(_post, url, headers)
            assert status == 401, headers
            assert resp_headers["www-authenticate"].startswith("Bearer")
        status, _ = await asyncio.to_thread(_post, url, {"Authorization": f"Bearer {TOKEN}"})
        assert status == 200

        # urlopen blocks, and the server shares this event loop: run it off-thread.
        status = await asyncio.to_thread(lambda: urllib.request.urlopen(f"{base}/healthz", timeout=10).status)
        assert status == 200


@pytest.mark.asyncio
async def test_a_client_without_the_token_cannot_initialize():
    async with _serving(HttpSettings(token=TOKEN)) as base:
        with pytest.raises(BaseException):  # noqa: B017 - the client surfaces the 401 as an exception group
            async with _session(base, None):
                pass


@pytest.mark.asyncio
async def test_loopback_without_a_token_serves_unauthenticated():
    """The no-token path is only reachable on loopback (see the settings tests)."""
    async with _serving(HttpSettings()) as base, _session(base, None) as session:
        assert {t.name for t in (await session.list_tools()).tools} == EXPECTED_TOOLS


# ── the real entrypoint, as a subprocess ─────────────────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run_module(env_extra: dict[str, str], *args: str) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "CONTAINER_APP"))}
    env.update(env_extra)
    return subprocess.Popen(
        [sys.executable, "-m", "ccai_mcp.server", *args],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_entrypoint_refuses_a_non_loopback_bind_without_a_token():
    proc = _run_module({"MCP_TRANSPORT": "http", "MCP_SERVER_HOST": "0.0.0.0"})
    _, err = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert "MCP_AUTH_TOKEN" in err
    assert "refusing to start" in err


def test_entrypoint_refuses_upstream_flag_without_platform_signal():
    proc = _run_module({"MCP_TRANSPORT": "http", "MCP_SERVER_HOST": "0.0.0.0", "MCP_AUTH_UPSTREAM": "true"})
    _, err = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert "CONTAINER_APP_NAME" in err
    assert "refusing to start" in err


@pytest.mark.asyncio
async def test_entrypoint_serves_tokenless_with_upstream_flag_and_platform_signal():
    port = _free_port()
    env = {
        "MCP_TRANSPORT": "http",
        "MCP_SERVER_HOST": "0.0.0.0",
        "MCP_SERVER_PORT": str(port),
        "MCP_AUTH_UPSTREAM": "true",
        **ACA_SIGNAL,
    }
    proc = _run_module(env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(300):
            if proc.poll() is not None:
                pytest.fail(f"server exited early: {proc.stderr.read()}")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not come up")
        async with _session(base, None) as session:
            assert {t.name for t in (await session.list_tools()).tools} == EXPECTED_TOOLS
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    assert "Azure Container Apps signal present" in err


def test_entrypoint_rejects_an_unknown_transport():
    proc = _run_module({"MCP_TRANSPORT": "carrier-pigeon"})
    _, err = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert "MCP_TRANSPORT" in err


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["flag", "env"])
async def test_entrypoint_serves_http_when_asked(how):
    port = _free_port()
    env = {"MCP_SERVER_PORT": str(port), "MCP_AUTH_TOKEN": TOKEN}
    args: tuple[str, ...] = ()
    if how == "flag":
        args = ("--http",)
    else:
        env["MCP_TRANSPORT"] = "http"
    proc = _run_module(env, *args)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(300):
            if proc.poll() is not None:
                pytest.fail(f"server exited early: {proc.stderr.read()}")
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            pytest.fail("server did not come up")

        assert (await asyncio.to_thread(_post, f"{base}/mcp", {}))[0] == 401
        async with _session(base, TOKEN) as session:
            assert {t.name for t in (await session.list_tools()).tools} == EXPECTED_TOOLS
            result = await session.call_tool("get_call_summary", {})
            assert result.is_error
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    assert TOKEN not in err, "the bearer token must never be logged"
