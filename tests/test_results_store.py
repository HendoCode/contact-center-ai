"""`params.store` (the store snapshot a run read) in the results contract."""

import pytest

from tools.results import render_record, validate


def record(store):
    params = {} if store is None else {"store": store}
    return {"date": "2026-10-05", "git_sha": "abc1234", "area": "evals", "run": "r", "hardware": "t",
            "versions": {"python": "3.12"}, "params": params, "metrics": {"all": {"n": 1}}, "notes": ""}


@pytest.mark.parametrize("store", [
    None,
    {"backend": "lancedb", "lance_version": 12, "lance_tag": "seed-abc1234", "rows": 1540},
    {"backend": "lancedb", "lance_version": 3, "lance_tag": None, "rows": 10, "ephemeral": True},
    {"backend": "pgvector"},
])
def test_valid_store_provenance(store):
    assert validate(record(store)) == []


@pytest.mark.parametrize("store", [
    "lancedb",
    {},
    {"backend": "lancedb"},
    {"backend": "lancedb", "lance_version": "12"},
    {"backend": "lancedb", "lance_version": True},
])
def test_invalid_store_provenance(store):
    [error] = validate(record(store))
    assert "params.store" in error


def test_store_is_rendered_with_the_params():
    text = render_record(record({"backend": "lancedb", "lance_version": 12, "lance_tag": "seed-abc1234"}))
    assert '"lance_version": 12' in text and "seed-abc1234" in text
