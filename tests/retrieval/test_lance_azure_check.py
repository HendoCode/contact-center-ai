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
