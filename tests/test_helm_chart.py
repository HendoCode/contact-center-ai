"""The Helm chart (infra/k8s/ccai) runs the same steps as the Compose stack it mirrors.

The templates are Go templates, not YAML, so these read them as text. Rendering and schema
validation are `make k8s-check` (helm and kubeconform), which CI runs in its own job.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infra" / "k8s" / "ccai"
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _template(name: str) -> str:
    return (CHART / "templates" / name).read_text()


def _seed_step() -> str:
    helpers = _template("_helpers.tpl")
    start = helpers.index('{{- define "ccai.seedStep" -}}')
    return helpers[start : helpers.index("{{- end }}\n", helpers.index("ccai.markJob", start))]


def test_seed_step_runs_every_compose_seed_command_in_order():
    compose_lines = [line.strip() for line in COMPOSE["seed"]["command"][2].splitlines() if line.strip()]
    step = _seed_step()
    positions = [step.index(line) for line in compose_lines]
    assert positions == sorted(positions), "the chart's seed step runs the Compose steps out of order"


def test_dbt_runs_after_the_seed_in_one_job():
    job = _template("job-data.yaml")
    # The seed step is an init container when dbt is enabled, so dbt starts only after it.
    init = job.index("initContainers:")
    assert init < job.index("- name: seed") < job.index("containers:\n        {{- if $d.enabled }}")
    assert "dbt build" in job


def test_mcp_server_command_matches_compose():
    assert f'command: {COMPOSE["mcp-server"]["command"]}'.replace("'", '"') in _template("mcp-server.yaml")


def test_default_lance_path_matches_compose():
    assert COMPOSE["mcp-server"]["environment"]["LANCE_URI"] == "/app/data/lance"
    assert '{{- default "/app/data/lance" .Values.lance.uri }}' in _template("_helpers.tpl")


def test_values_reference_a_secret_and_never_hold_secret_values():
    for path in (CHART / "values.yaml", ROOT / "infra" / "k8s" / "values-aks.yaml"):
        values = yaml.safe_load(path.read_text())
        assert set(values["secrets"]) == {"name"}, path
        config = values.get("config") or {}
        assert not [k for k in config if k.endswith(("_KEY", "_TOKEN", "_PASSWORD"))], path
