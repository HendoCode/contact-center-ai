"""The Compose stack reads transcripts from LanceDB. A service that overrides `environment:`
loses the `*app` anchor's values (a YAML merge replaces the mapping), so each reader must
carry the backend, the absolute dataset path and the shared volume itself."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
MOUNT = "lance-data:/app/data/lance"
READERS = ["mcp-server", "langgraph-api", "demo", "jupyter"]


def _compose():
    return yaml.safe_load(COMPOSE.read_text())


def test_readers_use_lancedb_with_the_absolute_path_and_volume():
    services = _compose()["services"]
    for name in READERS:
        env = services[name]["environment"]
        assert env["RETRIEVER_BACKEND"] == "lancedb", name
        assert env["LANCE_URI"] == "/app/data/lance", name
        assert MOUNT in services[name]["volumes"], name


def test_seed_is_the_writer_and_ingests_both_stores():
    seed = _compose()["services"]["seed"]
    assert MOUNT in seed["volumes"]
    script = "\n".join(seed["command"])
    assert "RETRIEVER_BACKEND=pgvector python -m rag.pipeline --ingest" in script
    assert "RETRIEVER_BACKEND=lancedb python -m rag.pipeline --ingest" in script
    assert script.index("pgvector python") < script.index("lancedb python")


def test_backend_and_uri_are_not_interpolated_from_env():
    for svc in _compose()["services"].values():
        env = svc.get("environment") or {}
        if not isinstance(env, dict):  # list-form `KEY=value` entries (open-webui)
            continue
        for key in ("RETRIEVER_BACKEND", "LANCE_URI"):
            assert "$" not in str(env.get(key, "")), key


def test_the_volume_is_declared():
    assert "lance-data" in _compose()["volumes"]


def test_image_installs_the_lance_group():
    syncs = [line for line in (ROOT / "Dockerfile").read_text().splitlines() if "uv sync" in line]
    assert len(syncs) == 2 and all("--group lance" in line for line in syncs)
