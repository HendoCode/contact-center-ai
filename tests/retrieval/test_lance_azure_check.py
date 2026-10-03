"""LanceDB on az://: option building and the dry run offline, and a full run against Azurite.

The Azurite test is `integration`: start `azurite-blob` on 127.0.0.1:10000, create the
`lance` container, and run with `-m integration` and AZURITE=1.
"""

import os
import sys
from pathlib import Path

import pytest

from retrieval.lance_azure_check import azure_storage_options, comparison, main
from retrieval.lancedb_backend import LanceDBRetriever


def test_options_use_entra_or_sas_and_never_an_account_key():
    assert azure_storage_options("stccai") == {"azure_storage_account_name": "stccai",
                                               "azure_use_azure_cli": "true"}
    assert azure_storage_options("stccai", sas="sv=x&sig=y") == {
        "azure_storage_account_name": "stccai", "azure_storage_sas_key": "sv=x&sig=y"}
    for opts in (azure_storage_options("a"), azure_storage_options("a", sas="s"),
                 azure_storage_options("devstoreaccount1", emulator=True)):
        assert not any("account_key" in k or "access_key" in k for k in opts)


def test_retriever_passes_storage_options_to_lancedb(monkeypatch):
    import lancedb

    seen = {}
    monkeypatch.setattr(lancedb, "connect", lambda uri, **kw: seen.update(uri=uri, **kw) or object())
    assert LanceDBRetriever(uri="az://lance/x", storage_options={"azure_use_azure_cli": "true"}).db is not None
    assert seen == {"uri": "az://lance/x", "storage_options": {"azure_use_azure_cli": "true"}}


def test_dry_run_prints_the_plan_and_touches_nothing(capsys, monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "stccaiexample")
    monkeypatch.delenv("AZURE_STORAGE_SAS_KEY", raising=False)
    assert main(["--dry-run", "--prefix", "p1"]) == 0
    out = capsys.readouterr().out
    assert "account:   stccaiexample" in out
    assert "LANCE_URI: az://lance/p1" in out
    assert "auth:      Entra via az login" in out
    assert "drop the Azure table" in out


def test_comparison_table():
    ok = {"status": "ok", "recall@10": 0.5, "p50_ms": 3.25}
    table = comparison({"lancedb/vector": ok, "lancedb-az/vector": {**ok, "p50_ms": 20.0}}, modes=["vector"])
    assert table.splitlines()[-1] == "| vector | 0.500 | 0.500 | 3.2 | 20.0 |"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("AZURITE"), reason="set AZURITE=1 with azurite-blob running")
def test_full_run_on_azurite_matches_local(capsys):
    sys.path.insert(0, str(Path(__file__).parent))
    from conftest import FakeEmbeddings

    assert main(["--emulator", "--repeats", "1", "--prefix", "pytest-azurite"], embeddings=FakeEmbeddings()) == 0
    rows = [ln.split("|") for ln in capsys.readouterr().out.splitlines() if ln.startswith("| ") and "mode" not in ln]
    assert {r[1].strip() for r in rows} == {"vector", "fts", "hybrid"}
    assert all(r[2].strip() == r[3].strip() for r in rows)  # same recall@10 on disk and on az://


# ── first real run: blank AZURE_* from .env, cache wiring, short failures ─────────────────────

def test_scrub_drops_blank_and_dotenv_service_principal_vars():
    from retrieval.lance_azure_check import scrub_azure_env

    env = {"AZURE_TENANT_ID": "", "AZURE_CLIENT_ID": " ", "AZURE_CLIENT_SECRET": "",
           "AZURE_STORAGE_ACCOUNT_NAME": "stccai", "PATH": "/bin"}
    assert scrub_azure_env(set(), using_cli=True, environ=env) == [
        "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]
    assert env == {"AZURE_STORAGE_ACCOUNT_NAME": "stccai", "PATH": "/bin"}

    env = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s"}
    assert scrub_azure_env({"AZURE_CLIENT_ID"}, using_cli=True, environ=env) == [
        "AZURE_CLIENT_SECRET", "AZURE_TENANT_ID"]  # from .env: dropped; set deliberately: kept
    assert env == {"AZURE_CLIENT_ID": "c"}

    env = {"AZURE_TENANT_ID": "t", "AZURE_CLIENT_ID": "c", "AZURE_CLIENT_SECRET": "s"}
    assert scrub_azure_env(set(), using_cli=False, environ=env) == []  # SAS/emulator: only blanks go


def _fake_embeddings_loading_blank_dotenv(monkeypatch):
    """get_embeddings() as rag's import does it: .env.example's blank AZURE_* lines get loaded."""
    import rag.embeddings
    from retrieval.bench import CachedEmbeddings  # noqa: F401 - bench must import cleanly

    class Emb:
        def embed_documents(self, texts):
            return [[1.0, float(len(t))] for t in texts]

        def embed_query(self, text):
            return [1.0, float(len(text))]

    def load():
        for k in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
            monkeypatch.setenv(k, "")
        return Emb()

    monkeypatch.setattr(rag.embeddings, "get_embeddings", load)


def test_run_uses_the_cache_and_no_blank_azure_var_reaches_the_store(monkeypatch, tmp_path, capsys):
    import retrieval.lance_azure_check as lac

    _fake_embeddings_loading_blank_dotenv(monkeypatch)
    monkeypatch.setattr(lac.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.delenv("AZURE_STORAGE_SAS_KEY", raising=False)
    monkeypatch.setattr("retrieval.bench.CACHE_DIR", tmp_path / "cache")
    seen = {}

    def fake_run_bench(docs, queries, embeddings, **kw):
        seen["cache_file"] = kw["cache_file"]
        seen["env"] = {k: v for k, v in os.environ.items() if k.startswith("AZURE_")}
        return {}

    monkeypatch.setattr(lac, "run_bench", fake_run_bench)
    monkeypatch.setattr(lac, "open_lancedb_azure", lambda uri, opts: seen.setdefault("opts", opts))
    monkeypatch.setattr("lancedb.connect", lambda *a, **k: type("Db", (), {"drop_table": lambda s, t: None})())
    assert lac.main(["--account", "stccai", "--prefix", "p"]) == 0
    assert seen["cache_file"].parent == tmp_path / "cache"  # bench.py main's model-keyed file
    assert not any(k in seen["env"] for k in lac.SP_VARS)
    assert seen["opts"] == {"azure_storage_account_name": "stccai", "azure_use_azure_cli": "true"}
    assert "ignoring AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID" in capsys.readouterr().out

    seen.clear()
    assert lac.main(["--account", "stccai", "--prefix", "p", "--no-cache"]) == 0
    assert seen["cache_file"] is None


def test_azure_failure_is_one_short_line_with_the_fix(monkeypatch, tmp_path, capsys):
    import retrieval.lance_azure_check as lac
    from retrieval import bench

    _fake_embeddings_loading_blank_dotenv(monkeypatch)
    monkeypatch.setattr(lac.shutil, "which", lambda name: "/usr/bin/az")
    monkeypatch.setattr("retrieval.bench.CACHE_DIR", tmp_path / "cache")
    rust = "Failed to connect to namespace: MicrosoftAzure TokenRequest POST " + "x" * 2000

    class Failing:
        name = "lancedb-az"

        def ingest(self, docs):
            raise OSError(rust)

    monkeypatch.setattr(lac, "open_lancedb_azure", lambda uri, opts: lambda e, w: bench.Store(Failing()))
    monkeypatch.setattr(lac, "load_corpus", lambda: [{"call_id": "C1", "text": "t", "metadata": {}}])
    monkeypatch.setattr(lac, "load_queries", lambda: [{"id": "q", "query": "t", "where": None,
                                                       "relevant": ["C1"], "type": "topical"}])
    assert lac.main(["--account", "stccai", "--prefix", "p", "--repeats", "1"]) == 1
    out = capsys.readouterr()
    assert "Azure sign-in failed: run `az login`" in out.err
    assert "Traceback" not in out.out + out.err
    assert max(len(line) for line in (out.out + out.err).splitlines()) < 400
