"""
Staleness guard for the committed Apache Ossie export (docs/semantics/ossie/).

The export is converter output from `ossie-dbt msi-to-ossie` (see docs/semantics/README.md).
These read the committed files only: no dbt, no converter, no network. They fail when
semantic_models.yml or metrics.yml gain something the export doesn't cover, so it gets re-run.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEMANTIC_DIR = ROOT / "olap" / "dbt" / "models" / "marts" / "semantic"
EXPORT = ROOT / "docs" / "semantics" / "ossie" / "ccai_semantic.ossie.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def test_export_is_a_flat_0_2_document():
    doc = _load(EXPORT)
    assert doc["version"] == "0.2.0.dev0"
    assert doc["name"] == "ccai_semantic"
    assert "semantic_model" not in doc  # 0.2.0.dev0 dropped the 0.1.x wrapper array


def test_every_semantic_model_is_a_dataset_with_all_its_fields():
    datasets = {d["name"]: d for d in _load(EXPORT)["datasets"]}
    semantic_models = _load(SEMANTIC_DIR / "semantic_models.yml")["semantic_models"]

    assert set(datasets) == {sm["name"] for sm in semantic_models}
    for sm in semantic_models:
        fields = {f["name"]: f for f in datasets[sm["name"]]["fields"]}
        expected = [x["name"] for key in ("entities", "dimensions", "measures") for x in sm.get(key, [])]
        assert sorted(fields) == sorted(expected), sm["name"]
        for dim in sm.get("dimensions", []):
            assert fields[dim["name"]]["dimension"]["is_time"] == (dim["type"] == "time"), dim["name"]


def test_every_foreign_entity_with_a_primary_owner_is_a_relationship():
    semantic_models = _load(SEMANTIC_DIR / "semantic_models.yml")["semantic_models"]
    owner = {e["name"]: sm["name"] for sm in semantic_models for e in sm["entities"] if e["type"] == "primary"}
    expected = {
        (sm["name"], owner[e["name"]])
        for sm in semantic_models
        for e in sm["entities"]
        if e["type"] == "foreign" and e["name"] in owner
    }

    relationships = _load(EXPORT)["relationships"]
    assert {(r["from"], r["to"]) for r in relationships} == expected


def test_every_metric_is_exported_with_its_description():
    exported = {m["name"]: m for m in _load(EXPORT)["metrics"]}
    metrics = _load(SEMANTIC_DIR / "metrics.yml")["metrics"]

    assert set(exported) == {m["name"] for m in metrics}
    for m in metrics:
        assert exported[m["name"]]["description"] == m["description"], m["name"]
