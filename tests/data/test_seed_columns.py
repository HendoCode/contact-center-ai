"""The two new OLTP columns (member.home_branch, account_transaction.fee_type) reach Postgres."""

import re
import sqlite3

from olap import seed
from olap.dbt.loaders.schema import parse_schema


def test_schema_declares_both_columns_with_an_in_place_upgrade():
    tables = parse_schema()
    assert "home_branch" in {c.name for c in tables["member"].columns}
    assert "fee_type" in {c.name for c in tables["account_transaction"].columns}
    sql = seed.REPO_ROOT.joinpath("olap", "oltp", "schema.sql").read_text()
    assert "ALTER TABLE member ADD COLUMN IF NOT EXISTS home_branch" in sql
    assert "ALTER TABLE account_transaction ADD COLUMN IF NOT EXISTS fee_type" in sql


def test_loader_columns_match_the_schema_and_the_generated_rows(corpus):
    schema = parse_schema()
    for table, columns, _pk, preparer, source in seed.TABLES:
        assert set(columns) <= {c.name for c in schema[table].columns}, table
        if table in ("member", "account_transaction"):
            rows = list(preparer(corpus.load(source)))
            assert rows and set(rows[0]) == set(columns), table
    members = list(seed.prep_members(corpus.load("members.json")))
    assert {m["home_branch"] for m in members} == {"Westside", "Downtown", "Northgate", "Riverside"}
    fees = [t for t in seed.prep_transactions(corpus.load("transactions.json")) if t["txn_type"] == "fee"]
    assert fees and all(t["fee_type"] for t in fees)


class SqliteCursor:
    def __init__(self, conn):
        self.conn, self.cur = conn, None

    def execute(self, sql, params=()):
        self.cur = self.conn.execute(re.sub(r"%s", "?", sql), params)

    def fetchall(self):
        return self.cur.fetchall()


def test_reseeding_a_regenerated_corpus_drops_surveys_and_junction_rows_that_moved():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE csat_survey (survey_id INTEGER PRIMARY KEY)")
    conn.execute("CREATE TABLE interaction_account (interaction_id INT, account_id INT)")
    conn.executemany("INSERT INTO csat_survey VALUES (?)", [(1,), (2,), (3,)])
    conn.executemany("INSERT INTO interaction_account VALUES (?, ?)", [(1, 10), (2, 20), (2, 21)])
    cur = SqliteCursor(conn)
    assert seed.prune(cur, "csat_survey", [{"survey_id": 2}, {"survey_id": 9}], ["survey_id"]) == 2
    kept = [{"interaction_id": 2, "account_id": 21}]
    assert seed.prune(cur, "interaction_account", kept, ["interaction_id", "account_id"]) == 2
    assert conn.execute("SELECT survey_id FROM csat_survey").fetchall() == [(2,)]
    assert conn.execute("SELECT interaction_id, account_id FROM interaction_account").fetchall() == [(2, 21)]
    assert {"csat_survey", "interaction_account"} == seed.PRUNED
