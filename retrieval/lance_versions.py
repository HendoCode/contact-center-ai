"""
Versioned LanceDB store: tags, time travel, one-command rollback and a retention policy.

Every write to a Lance table commits a new immutable version, so the store already keeps its
history; this module names the versions that matter and makes them usable:

* **Tag at each ingest.** `make ingest` (RETRIEVER_BACKEND=lancedb) tags the finished table
  `seed-<git sha>` (`seed-<sha>-dirty` for a working tree with uncommitted changes). A tag that
  already exists gets a numeric suffix (`seed-<sha>-2`), so re-running ingest at the same commit
  never moves or overwrites an earlier tag.
* **Time travel.** `LanceDBRetriever(as_of=<tag or version>)` reads that snapshot, read-only.
* **Rollback.** `restore` makes an old version the newest one again; history is kept, nothing is
  rewritten. `make lance-rollback TAG=seed-<sha>`.
* **Provenance.** `snapshot()` is `{"backend", "lance_version", "lance_tag", "rows"}`, recorded
  in `params.store` of `results/evals/*.json` and `results/retrieval/*.json` so a result names the
  exact table version it read.
* **Retention.** Versions pile up (one ingest commits several). `prune` keeps the newest
  `KEEP_SEED_TAGS` seed tags, then cleans every version older than the oldest remaining tag.
  A tagged version is never deleted: LanceDB 0.39 refuses `optimize(cleanup_older_than=...)`
  outright when a tagged version falls inside the cleanup window, so the cutoff is placed at the
  oldest tag and only history before it goes. Tags not named `seed-*` are never expired, and
  while one exists, history older than it is kept for it.

* **Embedding-model change.** When ingest replaces vectors made by another model
  (`lancedb_backend.ingest`), it first tags the version being replaced `pre-model-change-<n>`
  unless that version already carries a tag (a `seed-*` tag from the previous ingest usually does).
  Rolling back to such a tag restores that model's vectors, in their own dimension; query with the
  matching `EMBEDDING_MODEL` afterwards. These tags are not `seed-*`, so retention never expires them.

    python -m retrieval.lance_versions list
    python -m retrieval.lance_versions rollback TAG
    python -m retrieval.lance_versions prune [--keep N]
"""

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PREFIX = "seed-"
KEEP_SEED_TAGS = 10
# With no tags to anchor the cutoff, clean what is older than this (LanceDB's own default).
UNTAGGED_RETENTION = timedelta(days=14)
# Slack between the oldest tag's timestamp and the cleanup cutoff, so clock reads a few
# milliseconds apart can never put the tagged version inside the cleanup window.
CUTOFF_MARGIN = timedelta(seconds=1)


def git_label() -> str:
    """`<7-char sha>` of HEAD, plus `-dirty` with uncommitted tracked changes; `unknown` off git."""
    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True).stdout
        except OSError:
            return ""

    sha = git("rev-parse", "--short=7", "HEAD").strip() or "unknown"
    return f"{sha}-dirty" if git("status", "--porcelain", "--untracked-files=no").strip() else sha


def seed_tag_name(label: str, taken: set[str]) -> str:
    """`seed-<label>`, or `seed-<label>-2`, `-3`, ... when that name is taken."""
    base = f"{SEED_PREFIX}{label}"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name


MODEL_CHANGE_PREFIX = "pre-model-change-"


def tag_before_model_change(table) -> str | None:
    """Tag the newest version `pre-model-change-<n>` unless it already has a tag; returns the name."""
    if tags_by_version(table).get(table.version):
        return None
    taken = set(table.tags.list())
    n = 1
    while f"{MODEL_CHANGE_PREFIX}{n}" in taken:
        n += 1
    name = f"{MODEL_CHANGE_PREFIX}{n}"
    table.tags.create(name, table.version)
    return name


def tags_by_version(table) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for name, info in table.tags.list().items():
        out.setdefault(info["version"], []).append(name)
    return {v: sorted(names) for v, names in out.items()}


def snapshot(table) -> dict:
    """The version `table` is pinned to, for results provenance."""
    tags = tags_by_version(table).get(table.version, [])
    seeds = [t for t in tags if t.startswith(SEED_PREFIX)]
    return {
        "backend": "lancedb",
        "lance_version": table.version,
        "lance_tag": (seeds or tags or [None])[-1],
        "rows": table.count_rows(),
    }


def list_versions(table) -> list[dict]:
    """Every version, oldest first: number, commit time, tags, row count, and which is current."""
    tags = tags_by_version(table)
    head = max(v["version"] for v in table.list_versions())
    return [
        {
            "version": v["version"],
            "timestamp": v["timestamp"],
            "tags": tags.get(v["version"], []),
            "rows": int(v["metadata"].get("total_rows", 0)),
            "current": v["version"] == head,
        }
        for v in table.list_versions()
    ]


def rollback(table, ref: int | str) -> int:
    """Make version/tag `ref` the newest version again; returns the new version number.

    `restore` commits a copy of the old version on top of the history, so the rolled-back-from
    versions stay reachable until they are pruned.
    """
    if isinstance(ref, str) and ref.isdigit():
        ref = int(ref)
    table.checkout(ref)  # raises for an unknown tag or version
    table.restore()
    return table.version


def _expire_seed_tags(table, keep: int) -> list[str]:
    """Delete all but the `keep` newest seed tags; returns the deleted names."""
    seeds = sorted((info["version"], name) for name, info in table.tags.list().items()
                   if name.startswith(SEED_PREFIX))
    expired = [name for _, name in seeds[: max(0, len(seeds) - keep)]]
    for name in expired:
        table.tags.delete(name)
    return expired


def _cleanup(table) -> None:
    """Delete every version older than the oldest tagged one (all of them but the newest
    `UNTAGGED_RETENTION`-old ones when nothing is tagged)."""
    tagged = {info["version"] for info in table.tags.list().values()}
    if not tagged:
        table.optimize(cleanup_older_than=UNTAGGED_RETENTION)
        return
    oldest = min(v["timestamp"] for v in table.list_versions() if v["version"] in tagged)
    # Version timestamps are naive local time.
    table.optimize(cleanup_older_than=datetime.now() - oldest + CUTOFF_MARGIN)


def prune(table, keep: int = KEEP_SEED_TAGS) -> dict:
    """Apply the retention policy: keep the newest `keep` seed tags, clean older history."""
    before = len(table.list_versions())
    expired = _expire_seed_tags(table, keep)
    _cleanup(table)
    return {"expired_tags": expired, "versions_before": before, "versions_after": len(table.list_versions())}


def tag_seed(table, label: str | None = None, keep: int = KEEP_SEED_TAGS) -> str:
    """Tag the finished table `seed-<label>` after pruning to leave room for it.

    Pruning comes first because `optimize` can commit a version of its own; the tag must name
    the final newest version, the one a search will read.
    """
    _expire_seed_tags(table, keep - 1)
    _cleanup(table)
    name = seed_tag_name(label or git_label(), set(table.tags.list()))
    table.tags.create(name, table.version)
    return name


# ── CLI ───────────────────────────────────────────────────────────────────────

def _open_table():
    import rag  # noqa: F401 - loads .env, so LANCE_URI matches what `make ingest` wrote
    from retrieval.lancedb_backend import LanceDBRetriever

    r = LanceDBRetriever()
    table = r._table()
    if table is None:
        raise SystemExit(f"lance-versions: no table {r.table_name!r} at {r.uri!r}; run `make ingest` first")
    return r, table


def format_versions(rows: list[dict]) -> str:
    lines = [f"{'version':>7}  {'committed':<19}  {'rows':>6}  tags"]
    for r in rows:
        mark = "  <- current" if r["current"] else ""
        lines.append(f"{r['version']:>7}  {r['timestamp']:%Y-%m-%d %H:%M:%S}  {r['rows']:>6}  "
                     f"{', '.join(r['tags']) or '-'}{mark}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m retrieval.lance_versions",
                                     description="Inspect, roll back and prune the LanceDB store's versions.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="every version with its tags")
    rb = sub.add_parser("rollback", help="make a tagged version the newest again")
    rb.add_argument("ref", metavar="TAG", help="a tag (or version number)")
    pr = sub.add_parser("prune", help="expire old seed tags and clean the history before them")
    pr.add_argument("--keep", type=int, default=KEEP_SEED_TAGS, help="seed tags to keep (default %(default)s)")
    args = parser.parse_args(argv)

    r, table = _open_table()
    print(f"lance-versions: {r.uri} table {r.table_name!r}")
    try:
        if args.command == "list":
            print(format_versions(list_versions(table)))
        elif args.command == "rollback":
            new = rollback(table, args.ref)
            print(f"restored {args.ref!r}: now version {new} ({table.count_rows()} rows)")
        else:
            if args.keep < 1:
                parser.error("--keep must be at least 1")
            out = prune(table, args.keep)
            print(f"expired tags: {', '.join(out['expired_tags']) or 'none'}; "
                  f"versions {out['versions_before']} -> {out['versions_after']}")
    except (ValueError, RuntimeError) as e:
        print(f"lance-versions: {args.command} failed: {str(e).splitlines()[0][:300]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
