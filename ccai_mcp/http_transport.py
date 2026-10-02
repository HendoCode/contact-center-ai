"""
Streamable HTTP transport for the MCP server (M1), beside the stdio default.

Opt in with `MCP_TRANSPORT=http` or `python -m ccai_mcp.server --http`. The endpoint is
`http://<host>:<port>/mcp`; `GET /healthz` answers 200 without auth for container probes.

Safe by default, because this puts database-backed tools on a network:
  * binds 127.0.0.1 unless `MCP_SERVER_HOST` says otherwise;
  * a non-loopback bind refuses to start unless `MCP_AUTH_TOKEN` is set, or
    `MCP_AUTH_UPSTREAM=true` declares that an authenticating proxy such as Azure Easy Auth
    fronts the container AND the process is running on Azure Container Apps (at least two of
    the platform's injected variables are present; see `PLATFORM_SIGNAL_VARS`);
  * with a token set, every request except `/healthz` needs `Authorization: Bearer <token>`,
    compared in constant time, and is rejected with 401 before MCP sees it. The token is never
    logged.

A static bearer token is demo-grade: no expiry, no per-user identity, no revocation. It
does not encrypt anything either; put TLS (a reverse proxy, Azure ingress) in front of any
non-local use. Auth, OAuth and TLS termination beyond this are out of scope.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from mcp.server import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("ccai_mcp.http")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"

_TRUE = {"1", "true", "yes", "on"}

# Variables Azure Container Apps injects into every app container.
# Source: https://learn.microsoft.com/en-us/azure/container-apps/environment-variables
# ("Built-in environment variables" > Apps; page ms.date 2026-03-31, checked 2026-10-02).
# `CONTAINER_NAME` (managed function/logic apps) and the `CONTAINER_APP_JOB_*` variables (jobs)
# are deliberately left out. Two or more non-empty ones are required before
# MCP_AUTH_UPSTREAM may skip the token. This is a misconfiguration guard, not a security
# boundary: anyone who controls the environment can set these. Easy Auth is the real control.
PLATFORM_SIGNAL_VARS = (
    "CONTAINER_APP_NAME",
    "CONTAINER_APP_REVISION",
    "CONTAINER_APP_HOSTNAME",
    "CONTAINER_APP_ENV_DNS_SUFFIX",
    "CONTAINER_APP_PORT",
    "CONTAINER_APP_REPLICA_NAME",
)
PLATFORM_SIGNAL_MIN = 2


class ConfigError(ValueError):
    """The HTTP settings are unsafe or malformed; the server must not start."""


def is_loopback(host: str) -> bool:
    """True only for `localhost` and loopback IPs. Any other name or address is remote."""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class HttpSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str | None = None
    auth_upstream: bool = False
    platform_signal: tuple[str, ...] = ()  # names (never values) of the ACA variables found

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "HttpSettings":
        env = os.environ if environ is None else environ
        try:
            port = int(env.get("MCP_SERVER_PORT") or DEFAULT_PORT)
        except ValueError:
            raise ConfigError(f"MCP_SERVER_PORT must be an integer, got {env['MCP_SERVER_PORT']!r}") from None
        settings = cls(
            host=(env.get("MCP_SERVER_HOST") or DEFAULT_HOST).strip(),
            port=port,
            token=(env.get("MCP_AUTH_TOKEN") or "").strip() or None,
            auth_upstream=(env.get("MCP_AUTH_UPSTREAM") or "").strip().lower() in _TRUE,
            platform_signal=tuple(n for n in PLATFORM_SIGNAL_VARS if (env.get(n) or "").strip()),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not 0 <= self.port <= 65535:
            raise ConfigError(f"MCP_SERVER_PORT out of range: {self.port}")
        if is_loopback(self.host) or self.token:
            return
        if not self.auth_upstream:
            raise ConfigError(
                f"MCP_SERVER_HOST={self.host!r} is not a loopback address and MCP_AUTH_TOKEN is "
                "not set. Set MCP_AUTH_TOKEN (for example `openssl rand -hex 32`) or bind "
                f"{DEFAULT_HOST}. If an authenticating proxy such as Azure Easy Auth is the only "
                "way to reach this server on Azure Container Apps, set MCP_AUTH_UPSTREAM=true instead."
            )
        if len(self.platform_signal) < PLATFORM_SIGNAL_MIN:
            missing = [n for n in PLATFORM_SIGNAL_VARS if n not in self.platform_signal]
            raise ConfigError(
                f"MCP_AUTH_UPSTREAM=true on a non-loopback bind (MCP_SERVER_HOST={self.host!r}) needs "
                f"proof of Azure Container Apps: at least {PLATFORM_SIGNAL_MIN} of "
                f"{', '.join(PLATFORM_SIGNAL_VARS)} must be set and non-empty (found "
                f"{len(self.platform_signal)}; missing {', '.join(missing)}). Set MCP_AUTH_TOKEN "
                "instead if this is not Azure Container Apps."
            )

    @property
    def auth_mode(self) -> str:
        if self.token:
            return "bearer token"
        if self.auth_upstream:
            return "upstream proxy"
        return "none (loopback only)"


def _digest(value: bytes) -> bytes:
    return hashlib.sha256(value).digest()


class BearerAuthMiddleware:
    """Pure-ASGI bearer check that runs before the MCP app sees a request.

    Both sides are hashed first so `compare_digest` always compares equal-length values and
    the comparison time does not depend on how much of the token matched or on its length.
    """

    def __init__(self, app: ASGIApp, token: str | None):
        self.app = app
        self._expected = _digest(token.encode()) if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._expected is None or self._authorized(scope):
            await self.app(scope, receive, send)
            return
        await _unauthorized(send)

    def _authorized(self, scope: Scope) -> bool:
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, presented = value.partition(b" ")
                if scheme.lower() != b"bearer":
                    return False
                return hmac.compare_digest(_digest(presented.strip()), self._expected)
        return False


async def _unauthorized(send: Send) -> None:
    body = json.dumps({"error": "unauthorized", "detail": "missing or invalid bearer token"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="ccai-mcp"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class HealthMiddleware:
    """`GET /healthz` -> 200, for Compose and Container Apps probes. Reveals nothing."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] == HEALTH_PATH and scope["method"] in ("GET", "HEAD"):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain"), (b"content-length", b"2")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})
            return
        await self.app(scope, receive, send)


def build_app(server: Server, settings: HttpSettings) -> ASGIApp:
    """The streamable-HTTP ASGI app for `server`, wrapped in health and auth middleware.

    Stateless with plain JSON replies: the tools never push to the client, and no session
    survives a restart or a scale-to-zero anyway, so a client never holds a dead session id.

    The SDK's DNS-rebinding guard defaults on for loopback binds and rejects any Host it
    does not expect, which breaks a TLS reverse proxy that forwards the public name. It
    guards unauthenticated localhost servers; a bearer token already stops a rebinding page,
    so it is switched off whenever a token is set.
    """
    security = TransportSecuritySettings(enable_dns_rebinding_protection=False) if settings.token else None
    mcp_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=settings.host,
    )
    # Health is the outer layer on purpose: it answers before the bearer check.
    return HealthMiddleware(BearerAuthMiddleware(mcp_app, settings.token))


async def serve_http(server: Server, settings: HttpSettings) -> None:
    import uvicorn

    app = build_app(server, settings)
    if settings.auth_mode == "upstream proxy":
        logger.info(
            "tokenless non-loopback bind allowed: MCP_AUTH_UPSTREAM=true and Azure Container Apps "
            "signal present (%s)",
            ", ".join(settings.platform_signal),
        )
    logger.info(
        "serving MCP over streamable HTTP at http://%s:%s%s (auth: %s)",
        settings.host,
        settings.port,
        MCP_PATH,
        settings.auth_mode,
    )
    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    await uvicorn.Server(config).serve()
