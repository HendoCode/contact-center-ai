"""
LanceDB on az:// (ADLS Gen2): the R3 bench's LanceDB half run on local disk and on Azure.

    make lance-azure-check ARGS="--dry-run"      # the plan; touches nothing
    make lance-azure-check                       # needs `az login` and the envs/dev ADLS account

Same corpus, labels, embeddings and metrics as `make bench` (`retrieval/bench.py`): every
unique document is embedded once and shared by both stores, so no extra embedding calls.
It ingests into a fresh `az://<container>/<prefix>` dataset, runs vector, fts and hybrid
for every labeled query (the filter queries exercise the metadata prefilter), prints
recall@10 and p50 next to the local-disk numbers, then drops the Azure table (`--keep`
leaves it). Nothing is written to results/.

Auth: Entra only, no account keys (shared keys are off on the account). The Lance object
store takes `azure_use_azure_cli`, so it asks `az` for a storage token for the signed-in
user, who needs Storage Blob Data Contributor on the account (envs/dev grants it to
`operator_object_id`). AZURE_STORAGE_SAS_KEY, when set, is used instead: a container-scoped
SAS is the fallback if CLI auth is refused.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, UTC
from pathlib import Path

from retrieval.bench import (
    REPO_ROOT,
    MODES,
    Store,
    ensure_ollama_model,
    load_corpus,
    load_queries,
    open_lancedb,
    run_bench,
)

DEV_ENV = REPO_ROOT / "infra" / "azure" / "envs" / "dev"
TABLE = "bench"


def azure_storage_options(account: str, *, sas: str | None = None, emulator: bool = False) -> dict[str, str]:
    """Lance object-store options for one ADLS account. Never an account key."""
    if emulator:  # Azurite: object_store's emulator mode supplies its well-known dev account
        return {"azure_storage_use_emulator": "true", "azure_storage_account_name": account,
                "azure_allow_http": "true"}
    opts = {"azure_storage_account_name": account}
    if sas:
        opts["azure_storage_sas_key"] = sas
    else:
        opts["azure_use_azure_cli"] = "true"
    return opts


def account_name(given: str | None) -> str | None:
    """--account, else AZURE_STORAGE_ACCOUNT_NAME, else envs/dev's adls_account_name output."""
    if given or os.getenv("AZURE_STORAGE_ACCOUNT_NAME"):
        return given or os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    try:
        out = subprocess.run(["terraform", f"-chdir={DEV_ENV}", "output", "-raw", "adls_account_name"],
                             capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def open_lancedb_azure(uri: str, options: dict[str, str]):
    """A bench opener for the az:// store; its rows are named lancedb-az/<mode>."""
    def opener(embeddings, workdir: Path) -> Store:
        from retrieval.lancedb_backend import LanceDBRetriever

        r = LanceDBRetriever(uri=uri, embeddings=embeddings, table_name=TABLE, storage_options=options)
        r.name = "lancedb-az"
        return Store(r, cleanup=lambda: None)
    return opener


def comparison(metrics: dict, modes=MODES) -> str:
    lines = ["| mode | local recall@10 | az recall@10 | local p50 ms | az p50 ms |", "|---|---|---|---|---|"]
    for m in modes:
        loc, az = metrics.get(f"lancedb/{m}", {}), metrics.get(f"lancedb-az/{m}", {})
        def f(row, key, fmt):
            return format(row[key], fmt) if row.get("status") == "ok" else row.get("status", "n/a")
        lines.append(f"| {m} | {f(loc, 'recall@10', '.3f')} | {f(az, 'recall@10', '.3f')} | "
                     f"{f(loc, 'p50_ms', '.1f')} | {f(az, 'p50_ms', '.1f')} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None, embeddings=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--account", help="ADLS account (default AZURE_STORAGE_ACCOUNT_NAME or terraform output)")
    p.add_argument("--container", default="lance", help="container (default lance)")
    p.add_argument("--prefix", help="dataset prefix (default ccai-check-<UTC timestamp>)")
    p.add_argument("--repeats", type=int, default=3, help="latency passes per query")
    p.add_argument("--keep", action="store_true", help="leave the Azure table in place")
    p.add_argument("--emulator", action="store_true", help="Azurite on 127.0.0.1:10000 (local test)")
    p.add_argument("--dry-run", action="store_true", help="print the plan and touch nothing")
    args = p.parse_args(argv)

    account = "devstoreaccount1" if args.emulator else account_name(args.account)
    prefix = args.prefix or f"ccai-check-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    uri = f"az://{args.container}/{prefix}"
    sas = os.getenv("AZURE_STORAGE_SAS_KEY")
    auth = "Azurite emulator" if args.emulator else ("SAS (AZURE_STORAGE_SAS_KEY)" if sas else "Entra via az login")

    if args.dry_run:
        print("lance-azure-check plan (dry run: nothing connects, embeds or writes)")
        print(f"  account:   {account or '<not found: pass --account or set AZURE_STORAGE_ACCOUNT_NAME>'}")
        print(f"  LANCE_URI: {uri}   table: {TABLE}")
        print(f"  auth:      {auth}")
        print("  corpus:    retrieval/bench.py load_corpus() + labels/queries.jsonl, embedded once (get_embeddings)")
        print(f"  runs:      lancedb (local temp dir) and lancedb-az, modes {', '.join(MODES)}, "
              f"{args.repeats} latency passes, filter queries prefiltered")
        print(f"  then:      {'keep' if args.keep else 'drop'} the Azure table; nothing written to results/")
        return 0
    if not account:
        raise SystemExit("no ADLS account: pass --account, set AZURE_STORAGE_ACCOUNT_NAME, or apply envs/dev")
    if not args.emulator and not sas and shutil.which("az") is None:
        raise SystemExit("az CLI not found: install it and run `az login` (or set AZURE_STORAGE_SAS_KEY)")

    if embeddings is None:
        from rag.embeddings import get_embeddings  # importing rag loads .env

        embeddings = get_embeddings()
        ensure_ollama_model(embeddings)
    options = azure_storage_options(account, sas=sas, emulator=args.emulator)
    docs, queries = load_corpus(), load_queries()
    print(f"lance-azure-check: {len(docs)} docs, {len(queries)} queries -> {uri} ({auth})")

    opener = open_lancedb_azure(uri, options)
    with tempfile.TemporaryDirectory(prefix="ccai-lance-az-") as tmp:
        metrics = run_bench(docs, queries, embeddings, backends=["lancedb", "lancedb-az"],
                            openers={"lancedb": open_lancedb, "lancedb-az": opener},
                            repeats=args.repeats, workdir=Path(tmp))
    if not args.keep:
        import lancedb

        lancedb.connect(uri, storage_options=options).drop_table(TABLE)
    print()
    print(comparison(metrics))
    az = metrics.get("lancedb-az/vector", {})
    print(f"\naz ingest_s={az.get('ingest_s', float('nan')):.2f}  "
          f"local ingest_s={metrics.get('lancedb/vector', {}).get('ingest_s', float('nan')):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
