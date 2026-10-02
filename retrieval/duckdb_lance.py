"""
DuckDB over the LanceDB dataset: SQL and search through the Lance extension, read-only.

`LanceDBRetriever` writes one Lance dataset (`<LANCE_URI>/<table>.lance`). DuckDB opens
that same directory through the `lance` extension (`INSTALL lance; LOAD lance`, from
DuckDB's core extension repository), so a SQL engine and the vector store share one copy
of the data. Nothing here writes; ingest stays with `LanceDBRetriever`.

    reader = DuckDBLanceReader()                 # same LANCE_URI / COLLECTION_NAME as the backend
    reader.count_by("category", "outcome")       # plain SQL aggregate over the dataset
    reader.search("unauthorized charge", k=5, mode="hybrid")

`INSTALL` downloads the extension on first use, so tests that touch it are marked
`integration` and skipped by default. Importing this module needs neither `duckdb` nor the
extension; both are loaded on first use.

Mapping to the Retriever contract (§4.1)
----------------------------------------
Not a full `Retriever`: no `ingest`. `search`, `get_by_id` and `count` return the same
`Hit`s as `LanceDBRetriever`, with these differences, checked against DuckDB 1.5.6 and its
`lance` extension:

* `vector`: `lance_vector_search` ranks by L2 distance and has no distance-type option.
  The reported `score` is recomputed as cosine similarity (`1 - array_cosine_distance`),
  the same quantity `LanceDBRetriever` reports. Ranking matches cosine for unit-length
  embeddings (OpenAI's are; check others before relying on it).
* `fts`: BM25 via `lance_fts`; `score` is `bm25 / max bm25 in the result set`, as in the
  LanceDB backend.
* `hybrid`: `lance_hybrid_search` fuses with a weighted `alpha` blend (default 0.5), not
  Reciprocal Rank Fusion, so rankings and scores differ from the LanceDB backend's hybrid.
  `score` is the extension's `_hybrid_score`.
* `where`: the extension's `filter` argument is only supported for namespace-backed
  tables, and `lance_hybrid_search` has no filter at all. So: `vector` with a `where` scans
  the dataset in SQL (`WHERE ... ORDER BY array_cosine_distance`), an exact, prefiltered
  search that bypasses any vector index. `fts` and `hybrid` with a `where` fetch every
  candidate (`k = count`) and filter afterwards, which is exact but touches every
  matching row. Fine at ~1,250 documents; it does not scale like LanceDB's prefilter.
* Object stores: only a local `LANCE_URI` was checked. `az://` and `s3://` are untested here.
"""

import json
import os
from typing import Literal

from retrieval.base import Hit, validate_where
from retrieval.lancedb_backend import DEFAULT_LANCE_URI

_SQL_OPS = {"gte": ">=", "lte": "<=", "lt": "<"}
# Columns are named explicitly so `vector` and the score columns never reach the Hit.
_COLUMNS = "call_id, text, metadata"


def where_to_sql(where: dict | None) -> tuple[str, list]:
    """Portable `where` -> (DuckDB WHERE clause or "", positional parameters)."""
    clauses: list[str] = []
    params: list = []
    for fld, op, value in validate_where(where):
        if op == "eq":
            if value is None:
                clauses.append(f"{fld} IS NULL")
            else:
                clauses.append(f"{fld} = ?")
                params.append(str(value))
        elif op == "in":
            if value:
                clauses.append(f"{fld} IN ({', '.join('?' * len(value))})")
                params.extend(str(v) for v in value)
            else:
                clauses.append("1 = 0")
        else:
            clauses.append(f"{fld} {_SQL_OPS[op]} ?")
            params.append(str(value))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


class DuckDBLanceReader:
    name = "duckdb-lance"

    def __init__(
        self,
        uri: str | None = None,
        table_name: str | None = None,
        embeddings=None,
        install: bool = True,
    ):
        self.uri = uri or os.getenv("LANCE_URI") or DEFAULT_LANCE_URI
        self.table_name = table_name or os.getenv("COLLECTION_NAME", "call_transcripts")
        self.install = install
        self._embeddings = embeddings
        self._con = None

    # ── plumbing ──────────────────────────────────────────────────────────────

    @property
    def dataset(self) -> str:
        """Path of the Lance dataset the LanceDB backend writes for this table."""
        return f"{self.uri.rstrip('/')}/{self.table_name}.lance"

    @property
    def embeddings(self):
        if self._embeddings is None:
            from rag.embeddings import get_embeddings
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def con(self):
        if self._con is None:
            import duckdb

            con = duckdb.connect()
            if self.install:
                con.execute("INSTALL lance")
            con.execute("LOAD lance")
            self._con = con
        return self._con

    def versions(self) -> dict:
        """DuckDB, extension build and dataset-reader versions, for the results record."""
        ext = self.con.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name = 'lance'"
        ).fetchone()
        return {
            "duckdb": self.con.execute("SELECT version()").fetchone()[0],
            "duckdb_lance_extension": ext[0] if ext else None,
        }

    def _dim(self) -> int:
        """Vector width of the dataset, read from its `FLOAT[n]` column type."""
        rows = self.con.execute(f"DESCRIBE SELECT vector FROM '{self.dataset}'").fetchall()
        return int(rows[0][1].split("[")[1].rstrip("]"))

    # ── SQL ───────────────────────────────────────────────────────────────────

    def count(self) -> int:
        return self.con.execute(f"SELECT count(*) FROM '{self.dataset}'").fetchone()[0]

    def count_by(self, *columns: str, where: dict | None = None) -> list[dict]:
        """`SELECT <columns>, count(*) ... GROUP BY <columns>`, e.g. by category and outcome."""
        allowed = ("category", "outcome", "date")
        bad = [c for c in columns if c not in allowed]
        if not columns or bad:
            raise ValueError(f"count_by needs columns from {allowed}; got {list(columns)}")
        clause, params = where_to_sql(where)
        cols = ", ".join(columns)
        rows = self.con.execute(
            f"SELECT {cols}, count(*) AS n FROM '{self.dataset}'{clause} "
            f"GROUP BY {cols} ORDER BY {cols}",
            params,
        ).fetchall()
        return [dict(zip([*columns, "n"], row)) for row in rows]

    def sql(self, query: str, params: list | None = None) -> list[tuple]:
        """Run any DuckDB SQL; the dataset is addressable as `'<LANCE_URI>/<table>.lance'`."""
        return self.con.execute(query, params or []).fetchall()

    # ── search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        k: int = 5,
        where: dict | None = None,
        mode: Literal["vector", "fts", "hybrid"] = "vector",
    ) -> list[Hit]:
        if mode not in ("vector", "fts", "hybrid"):
            raise ValueError(f"Unknown search mode {mode!r}; use 'vector', 'fts' or 'hybrid'")
        clause, params = where_to_sql(where)  # validates before any DuckDB call
        if mode == "fts":
            return self._fts(query, k, clause, params)
        vec = self.embeddings.embed_query(query)
        if mode == "vector":
            return self._vector(vec, k, clause, params)
        return self._hybrid(vec, query, k, clause, params)

    def _vector(self, vec: list[float], k: int, clause: str, params: list) -> list[Hit]:
        dim = self._dim()
        cast = f"?::FLOAT[{dim}]"
        score = f"1 - array_cosine_distance(vector, {cast})"
        if clause:
            # No `filter` support on a plain path: exact, prefiltered scan in SQL.
            sql = (
                f"SELECT {_COLUMNS}, {score} AS score FROM '{self.dataset}'{clause} "
                "ORDER BY score DESC LIMIT ?"
            )
            args = [vec, *params, k]
        else:
            sql = (
                f"SELECT {_COLUMNS}, {score} AS score FROM "
                f"lance_vector_search('{self.dataset}', 'vector', {cast}, k = ?) "
                "ORDER BY score DESC"
            )
            args = [vec, vec, k]
        return self._hits(self.con.execute(sql, args).fetchall())

    def _fts(self, query: str, k: int, clause: str, params: list) -> list[Hit]:
        window = self.count() if clause else k
        rows = self.con.execute(
            f"SELECT {_COLUMNS}, _score FROM "
            f"lance_fts('{self.dataset}', 'text', ?, k = ?){clause} ORDER BY _score DESC LIMIT ?",
            [query, window, *params, k],
        ).fetchall()
        top = max((r[3] for r in rows), default=0.0)
        return self._hits([(*r[:3], r[3] / top if top > 0 else 0.0) for r in rows])

    def _hybrid(self, vec: list[float], query: str, k: int, clause: str, params: list) -> list[Hit]:
        dim = self._dim()
        window = self.count() if clause else k
        rows = self.con.execute(
            f"SELECT {_COLUMNS}, _hybrid_score FROM lance_hybrid_search("
            f"'{self.dataset}', 'vector', ?::FLOAT[{dim}], 'text', ?, k = ?){clause} "
            "ORDER BY _hybrid_score DESC LIMIT ?",
            [vec, query, window, *params, k],
        ).fetchall()
        return self._hits(rows)

    @staticmethod
    def _hits(rows: list[tuple]) -> list[Hit]:
        return [
            Hit(call_id=cid, text=text, score=float(score), metadata=json.loads(meta))
            for cid, text, meta, score in rows
        ]

    # ── lookup ────────────────────────────────────────────────────────────────

    def get_by_id(self, call_id: str) -> Hit | None:
        rows = self.con.execute(
            f"SELECT {_COLUMNS}, 1.0 FROM '{self.dataset}' WHERE call_id = ? LIMIT 1", [call_id]
        ).fetchall()
        return self._hits(rows)[0] if rows else None
