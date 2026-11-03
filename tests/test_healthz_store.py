"""/healthz reports 503 while the configured transcript store is empty or unreadable."""
import asyncio

import pytest

from ccai_mcp import server as ccai_server, store_check
from ccai_mcp.http_transport import HttpSettings, build_app


async def _get(app, path="/healthz", method="GET"):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    await app({"type": "http", "path": path, "method": method, "headers": []}, receive, send)
    return sent[0]["status"], sent[1]["body"]


def _app(check):
    return build_app(ccai_server.app, HttpSettings(), store_check=check)


def test_healthy_without_a_check_and_with_a_clean_one():
    assert asyncio.run(_get(_app(None))) == (200, b"ok")
    assert asyncio.run(_get(_app(lambda: None))) == (200, b"ok")


def test_unhealthy_reveals_no_reason():
    status, body = asyncio.run(_get(_app(lambda: "lancedb store is empty")))
    assert (status, body) == (503, b"unhealthy")


def test_a_raising_check_is_unhealthy():
    def boom():
        raise RuntimeError("x")

    assert asyncio.run(_get(_app(boom)))[0] == 503


class _Fake:
    def __init__(self, n=0, exc=None):
        self.n, self.exc = n, exc

    def count(self):
        if self.exc:
            raise self.exc
        return self.n


@pytest.mark.parametrize(
    "fake,expect",
    [(_Fake(0), "empty"), (_Fake(exc=ImportError("lancedb")), "unreadable"), (_Fake(3), None)],
)
def test_store_problem(monkeypatch, fake, expect):
    monkeypatch.setattr(store_check, "get_retriever", lambda: fake)
    problem = store_check.store_problem()
    assert (problem is None) if expect is None else (expect in problem)


def test_empty_real_lance_store_is_a_problem(monkeypatch, tmp_path):
    pytest.importorskip("lancedb")
    monkeypatch.setenv("RETRIEVER_BACKEND", "lancedb")
    monkeypatch.setenv("LANCE_URI", str(tmp_path / "lance"))
    assert "empty" in store_check.store_problem()
