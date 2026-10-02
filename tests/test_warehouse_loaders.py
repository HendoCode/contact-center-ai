"""
Offline tests for the Snowflake and Databricks loaders (olap/dbt/loaders).

No driver is imported and nothing connects. A fake warehouse interprets the statements the
loaders emit (CREATE, PUT, TRUNCATE, COPY INTO, count) closely enough to prove the properties
that matter: every OLTP table is covered, a second run lands on the same rows, and the
generated SQL has the shape the real engines expect.
"""

import json
import re
import subprocess
import sys
import types
from pathlib import Path

import jinja2
import pytest
import yaml

from olap.dbt.loaders import databricks_loader, snowflake_loader
from olap.dbt.loaders.data import TableData, load_tables, write_ndjson
from olap.dbt.loaders.plan import Plan, TablePlan, check_ident, execute, render
from olap.dbt.loaders.schema import SCHEMA_SQL, parse_schema
from olap.seed import TABLES

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCES_YML = REPO_ROOT / "olap" / "dbt" / "models" / "marts" / "_sources.yml"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


@pytest.fixture(scope="module", autouse=True)
def synthetic_data():
    if not (REPO_ROOT / "data" / "synthetic" / "interactions.json").exists():
        subprocess.run(
            [sys.executable, "data/synthetic/generate_data.py"],
            cwd=REPO_ROOT, check=True, capture_output=True,
        )


@pytest.fixture(scope="module")
def schema():
    return parse_schema()


@pytest.fixture(scope="module")
def data():
    return load_tables()


def _snowflake_cfg(raw_schema="raw"):
    return snowflake_loader.SnowflakeConfig(
        account="acct", user="u", private_key_path="/k.p8", private_key_passphrase="",
        role="r", warehouse="w", database="CCAI", raw_schema=raw_schema,
    )


def _databricks_cfg(raw_schema="raw"):
    return databricks_loader.DatabricksConfig(
        host="https://h.example", http_path="/p", token="t", catalog="ccai", raw_schema=raw_schema,
    )


def _plan(kind, schema, data, tmp_path, **cfg_kwargs):
    files = {td.name: write_ndjson(td, tmp_path) for td in data}
    if kind == "snowflake":
        return snowflake_loader.build_plan(_snowflake_cfg(**cfg_kwargs), schema, data, files)
    return databricks_loader.build_plan(_databricks_cfg(**cfg_kwargs), schema, data, files)


# ── fake warehouse ────────────────────────────────────────────────────────────


class FakeWarehouse:
    """Interprets loader statements. COPY appends (as a real COPY does); only TRUNCATE clears."""

    def __init__(self):
        self.tables: dict[str, list[dict]] = {}
        self.staged: dict[str, list[dict]] = {}
        self.copied_files: dict[str, set[str]] = {}
        self.statements: list[str] = []

    def cursor(self):
        return FakeCursor(self)


class FakeCursor:
    def __init__(self, warehouse):
        self.w = warehouse
        self._result = None

    @staticmethod
    def _name(raw):
        return raw.replace("`", "").lower()

    def execute(self, sql):
        w = self.w
        w.statements.append(sql)
        if m := re.match(r"CREATE TABLE IF NOT EXISTS (\S+)", sql):
            w.tables.setdefault(self._name(m[1]), [])
        elif sql.startswith(("CREATE SCHEMA", "CREATE STAGE", "CREATE VOLUME")):
            pass
        elif sql.startswith("PUT "):
            local = Path(re.match(r"PUT '(?:file://)?([^']+)'", sql)[1])
            w.staged[local.name] = [json.loads(line) for line in local.read_text().splitlines()]
        elif m := re.match(r"TRUNCATE TABLE (\S+)", sql):
            w.tables[self._name(m[1])] = []
        elif m := re.match(r"COPY INTO (\S+)", sql):
            self._copy(self._name(m[1]), sql)
        elif m := re.match(r"SELECT count\(\*\) FROM (\S+)", sql):
            self._result = (len(w.tables[self._name(m[1])]),)
        else:
            raise AssertionError(f"unexpected statement: {sql[:80]}")

    def _copy(self, table, sql):
        w = self.w
        filename = re.search(r"([\w]+\.ndjson)", sql)[1]
        force = "FORCE = TRUE" in sql or "'force' = 'true'" in sql
        loaded = w.copied_files.setdefault(table, set())
        if filename in loaded and not force:
            return  # a real COPY skips a file it has already loaded
        rows = w.staged[filename]
        # every column the COPY reads must be a key in the file
        read = re.findall(r"\$1:(\w+)", sql) or re.findall(r"cast\(`(\w+)`", sql)
        assert set(read) <= set(rows[0]), f"COPY reads columns missing from the file: {read}"
        w.tables[table].extend(rows)
        loaded.add(filename)

    def fetchone(self):
        return self._result


# ── coverage: every OLTP table ────────────────────────────────────────────────


def test_schema_parser_finds_every_create_table(schema):
    declared = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA_SQL.read_text())
    assert list(schema) == declared
    assert len(declared) == 21


def test_every_oltp_table_has_a_loader_entry_and_a_dbt_source(schema, data):
    seed_tables = [name for name, *_ in TABLES]
    sources = yaml.safe_load(SOURCES_YML.read_text())["sources"][0]["tables"]
    assert set(seed_tables) == set(schema)
    assert {t["name"] for t in sources} == set(schema)
    assert [td.name for td in data] == seed_tables


def test_seed_columns_match_the_ddl_columns(schema):
    for name, columns, *_ in TABLES:
        assert set(columns) == {c.name for c in schema[name].columns}, name


@pytest.mark.parametrize("kind", ["snowflake", "databricks"])
def test_plan_covers_every_table(kind, schema, data, tmp_path):
    plan = _plan(kind, schema, data, tmp_path)
    assert [t.name for t in plan.tables] == list(schema)
    assert all(t.expected_rows > 0 for t in plan.tables)


# ── type mapping ──────────────────────────────────────────────────────────────


def test_type_mapping(schema):
    mortgage = schema["mortgage_account"]
    assert (mortgage.column("original_principal").snowflake,
            mortgage.column("original_principal").databricks) == ("NUMBER(14,2)", "DECIMAL(14,2)")
    assert mortgage.column("note_rate").snowflake == "NUMBER(6,3)"
    assert mortgage.column("account_id").databricks == "BIGINT"
    assert schema["household"].column("household_id").snowflake == "VARCHAR(36)"
    assert schema["household"].column("household_id").databricks == "STRING"
    assert schema["product"].column("is_active").snowflake == "BOOLEAN"
    assert schema["interaction"].column("started_at").snowflake == "TIMESTAMP_TZ"
    assert schema["interaction"].column("started_at").databricks == "TIMESTAMP"
    assert schema["interaction"].column("duration_seconds").snowflake == "INTEGER"
    assert schema["interaction"].column("duration_seconds").databricks == "INT"
    assert schema["csat_survey"].column("surveyed_at").snowflake == "DATE"
    assert schema["interaction_transcript"].column("utterances").snowflake == "VARIANT"
    assert schema["interaction_transcript"].column("utterances").databricks == "STRING"


def test_unmapped_types_raise_instead_of_guessing():
    with pytest.raises(ValueError, match="no warehouse type mapping.*MONEY"):
        parse_schema("CREATE TABLE IF NOT EXISTS t (\n    amount MONEY\n);")
    with pytest.raises(ValueError, match="precision and scale"):
        parse_schema("CREATE TABLE IF NOT EXISTS t (\n    amount NUMERIC\n);")


# ── the rows sent ─────────────────────────────────────────────────────────────


def test_rows_match_the_seed_counts_and_carry_every_column(data):
    from olap.seed import load_json

    sources = {name: source for name, _, _, _, source in TABLES}
    for td in data:
        assert td.rows, td.name
        assert all(set(r) == set(td.columns) for r in td.rows), td.name
        if td.name != "csat_survey":  # the csat file is mapped 1:1 below
            assert len(td.rows) == len(load_json(sources[td.name])), td.name
    assert len(next(t for t in data if t.name == "csat_survey").rows) == len(load_json("csat.json"))


def test_values_are_json_safe_and_typed_for_the_warehouses(data, tmp_path):
    transcript = next(t for t in data if t.name == "interaction_transcript").rows[0]
    assert isinstance(transcript["utterances"], str)  # JSON text, not a psycopg2 Json wrapper
    assert json.loads(transcript["utterances"])[0].keys() >= {"speaker", "text"}
    interaction = next(t for t in data if t.name == "interaction").rows[0]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT[\d:]+\+00:00", interaction["started_at"])
    member = next(t for t in data if t.name == "member").rows[0]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", member["joined_date"])
    for td in data:  # every file is valid NDJSON with one line per row
        path = write_ndjson(td, tmp_path)
        assert len(path.read_text().splitlines()) == len(td.rows)


# ── generated statements ──────────────────────────────────────────────────────


def test_snowflake_statements(schema, data, tmp_path):
    plan = _plan("snowflake", schema, data, tmp_path)
    assert plan.setup == [
        "CREATE SCHEMA IF NOT EXISTS CCAI.raw",
        "CREATE STAGE IF NOT EXISTS CCAI.raw.ccai_loader_stage",
    ]
    member = next(t for t in plan.tables if t.name == "member")
    create, put, truncate, copy = member.statements
    assert create.startswith("CREATE TABLE IF NOT EXISTS CCAI.raw.member (member_id BIGINT,")
    assert put.startswith("PUT 'file://") and "OVERWRITE = TRUE" in put
    assert truncate == "TRUNCATE TABLE CCAI.raw.member"
    assert "COPY INTO CCAI.raw.member (member_id," in copy
    assert "$1:member_id::BIGINT" in copy and "$1:joined_date::DATE" in copy
    assert "FILES = ('member.ndjson')" in copy and "FORCE = TRUE" in copy
    assert member.count_sql == "SELECT count(*) FROM CCAI.raw.member"
    transcript = next(t for t in plan.tables if t.name == "interaction_transcript")
    assert "PARSE_JSON($1:utterances::STRING)" in transcript.statements[3]


def test_databricks_statements(schema, data, tmp_path):
    plan = _plan("databricks", schema, data, tmp_path)
    assert plan.setup == [
        "CREATE SCHEMA IF NOT EXISTS `ccai`.`raw`",
        "CREATE VOLUME IF NOT EXISTS `ccai`.`raw`.`ccai_loader_stage`",
    ]
    csat = next(t for t in plan.tables if t.name == "csat_survey")
    create, put, truncate, copy = csat.statements
    assert "`comment` STRING" in create  # reserved-ish names are quoted
    assert put.endswith("INTO '/Volumes/ccai/raw/ccai_loader_stage/csat_survey.ndjson' OVERWRITE")
    assert truncate == "TRUNCATE TABLE `ccai`.`raw`.`csat_survey`"
    assert "cast(`score` AS INT) AS `score`" in copy
    assert "FROM '/Volumes/ccai/raw/ccai_loader_stage/csat_survey.ndjson'" in copy
    assert "'primitivesAsString' = 'true'" in copy and "'force' = 'true'" in copy


@pytest.mark.parametrize("kind", ["snowflake", "databricks"])
def test_statement_order_is_stage_then_truncate_then_copy(kind, schema, data, tmp_path):
    for table in _plan(kind, schema, data, tmp_path).tables:
        verbs = [s.split()[0] for s in table.statements]
        assert verbs == ["CREATE", "PUT", "TRUNCATE", "COPY"], table.name


@pytest.mark.parametrize("kind", ["snowflake", "databricks"])
def test_raw_schema_is_configurable(kind, schema, data, tmp_path):
    plan = _plan(kind, schema, data, tmp_path, raw_schema="landing")
    assert "landing" in plan.setup[0]
    assert all("landing" in s for s in plan.tables[0].statements if not s.startswith("PUT"))


def test_identifiers_from_env_must_be_plain(monkeypatch):
    assert check_ident("raw_2", "x") == "raw_2"
    for bad in ("raw; DROP TABLE x", "a.b", "1raw", "`x`", ""):
        with pytest.raises(ValueError):
            check_ident(bad, "X")
    monkeypatch.setenv("SNOWFLAKE_RAW_SCHEMA", "raw; drop")
    with pytest.raises(ValueError, match="SNOWFLAKE_RAW_SCHEMA"):
        snowflake_loader.SnowflakeConfig.from_env(dry_run=True)


def test_missing_env_fails_loudly_but_dry_run_uses_placeholders(monkeypatch):
    for name in snowflake_loader.REQUIRED_ENV + databricks_loader.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="SNOWFLAKE_ACCOUNT"):
        snowflake_loader.SnowflakeConfig.from_env()
    with pytest.raises(SystemExit, match="DATABRICKS_TOKEN"):
        databricks_loader.DatabricksConfig.from_env()
    assert snowflake_loader.SnowflakeConfig.from_env(dry_run=True).raw_schema == "raw"
    assert databricks_loader.DatabricksConfig.from_env(dry_run=True).raw_schema == "raw"


# ── idempotence ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["snowflake", "databricks"])
def test_second_run_lands_on_the_same_rows(kind, schema, data, tmp_path, capsys):
    warehouse = FakeWarehouse()
    plan = _plan(kind, schema, data, tmp_path)
    first = execute(warehouse.cursor(), plan)
    snapshot = {name: list(rows) for name, rows in warehouse.tables.items()}
    second = execute(warehouse.cursor(), plan)

    assert first == second == {td.name: len(td.rows) for td in data}
    assert warehouse.tables == snapshot
    for td in data:  # no duplicate keys after two runs
        rows = next(r for name, r in warehouse.tables.items() if name.endswith(f".{td.name}"))
        keys = [tuple(r[c] for c in td.pk) for r in rows]
        assert len(keys) == len(set(keys)), td.name
    capsys.readouterr()


@pytest.mark.parametrize("kind", ["snowflake", "databricks"])
def test_rerun_after_a_changed_file_replaces_rather_than_appends(kind, schema, data, tmp_path):
    warehouse = FakeWarehouse()
    plan = _plan(kind, schema, data, tmp_path)
    execute(warehouse.cursor(), plan)
    smaller = [TableData(t.name, t.columns, t.pk, t.rows[:3]) for t in data]
    plan = _plan(kind, schema, smaller, tmp_path)
    assert set(execute(warehouse.cursor(), plan).values()) <= {3}


def test_a_loader_without_truncate_would_be_caught(schema, data, tmp_path, capsys):
    # Guards the guard: drop TRUNCATE and the second run doubles the rows, which the post-load
    # count check turns into an error instead of a silent duplicate.
    plan = _plan("snowflake", schema, data[:1], tmp_path)
    plan.tables = [
        TablePlan(t.name, t.expected_rows,
                  [s for s in t.statements if not s.startswith("TRUNCATE")], t.count_sql)
        for t in plan.tables
    ]
    warehouse = FakeWarehouse()
    execute(warehouse.cursor(), plan)
    with pytest.raises(RuntimeError, match="row counts differ"):
        execute(warehouse.cursor(), plan)
    capsys.readouterr()


def test_a_copy_without_force_would_be_caught(schema, data, tmp_path, capsys):
    plan = _plan("databricks", schema, data[:1], tmp_path)
    plan.tables = [
        TablePlan(t.name, t.expected_rows,
                  [s.replace("'force' = 'true'", "'force' = 'false'") for s in t.statements],
                  t.count_sql)
        for t in plan.tables
    ]
    warehouse = FakeWarehouse()
    execute(warehouse.cursor(), plan)
    with pytest.raises(RuntimeError, match="row counts differ"):
        execute(warehouse.cursor(), plan)  # truncated, then COPY skipped the "already loaded" file
    capsys.readouterr()


def test_render_lists_every_statement(schema, data, tmp_path):
    text = render(_plan("snowflake", schema, data, tmp_path))
    assert text.count("COPY INTO") == len(schema)
    assert text.startswith("-- snowflake: setup")
    assert isinstance(Plan("x", []).tables, list)


# ── connections: key-pair auth, no password ───────────────────────────────────


def test_snowflake_connect_uses_key_pair_and_never_a_password(monkeypatch):
    calls = {}
    connector = types.ModuleType("snowflake.connector")
    connector.connect = lambda **kw: calls.update(kw) or "conn"
    pkg = types.ModuleType("snowflake")
    pkg.connector = connector
    monkeypatch.setitem(sys.modules, "snowflake", pkg)
    monkeypatch.setitem(sys.modules, "snowflake.connector", connector)
    cfg = snowflake_loader.SnowflakeConfig(
        "acct", "u", "/k.p8", "pw", "role", "wh", "CCAI", "raw")
    assert snowflake_loader.connect(cfg) == "conn"
    assert calls["private_key_file"] == "/k.p8" and calls["private_key_file_pwd"] == "pw"
    assert "password" not in calls
    assert calls["database"] == "CCAI" and "schema" not in calls


def test_databricks_connect_allows_staging_from_the_temp_dir(monkeypatch, tmp_path):
    calls = {}
    sql = types.ModuleType("databricks.sql")
    sql.connect = lambda **kw: calls.update(kw) or "conn"
    pkg = types.ModuleType("databricks")
    pkg.sql = sql
    monkeypatch.setitem(sys.modules, "databricks", pkg)
    monkeypatch.setitem(sys.modules, "databricks.sql", sql)
    assert databricks_loader.connect(_databricks_cfg(), tmp_path) == "conn"
    assert calls["server_hostname"] == "h.example"  # scheme stripped
    assert calls["staging_allowed_local_path"] == str(tmp_path)
    assert calls["access_token"] == "t" and calls["catalog"] == "ccai"


# ── dbt sources follow the loaders ────────────────────────────────────────────


def _rendered_source_schema(target_type, **env):
    raw = yaml.safe_load(SOURCES_YML.read_text())["sources"][0]["schema"]
    return jinja2.Template(raw).render(
        target=types.SimpleNamespace(type=target_type),
        env_var=lambda name, default="": env.get(name, default),
    )


def test_dbt_source_schema_is_env_driven_per_target_and_dev_is_unchanged():
    assert _rendered_source_schema("postgres") == "public"
    assert _rendered_source_schema("postgres", SNOWFLAKE_RAW_SCHEMA="x") == "public"
    assert _rendered_source_schema("snowflake") == "raw"
    assert _rendered_source_schema("snowflake", SNOWFLAKE_RAW_SCHEMA="landing") == "landing"
    assert _rendered_source_schema("databricks") == "raw"
    assert _rendered_source_schema("databricks", DATABRICKS_RAW_SCHEMA="landing") == "landing"


def test_loader_env_vars_are_documented():
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), flags=re.MULTILINE))
    claude_md = (REPO_ROOT / "CLAUDE.md").read_text()
    for name in ("SNOWFLAKE_RAW_SCHEMA", "DATABRICKS_RAW_SCHEMA"):
        assert name in declared, f"{name} missing from .env.example"
        assert name in claude_md, f"{name} missing from CLAUDE.md"
