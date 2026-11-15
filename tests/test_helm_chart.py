"""The Helm chart (infra/k8s/ccai) runs the same steps as the Compose stack it mirrors.

The templates are Go templates, not YAML, so these read them as text. Rendering and schema
validation are `make k8s-check` (helm and kubeconform), which CI runs in its own job.
"""
import re
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


def _values(path: Path = CHART / "values.yaml") -> dict:
    return yaml.safe_load(path.read_text())


def test_stopped_mode_zeroes_every_workload_and_skips_the_data_job():
    assert _values()["enabled"] is True
    for name in ("mcp-server.yaml", "ollama.yaml", "postgresql.yaml", "cloudflared.yaml"):
        assert 'include "ccai.replicas"' in _template(name), name
    assert "{{- if and .Values.enabled" in _template("job-data.yaml")
    assert "{{- if .Values.enabled }}" in _template("tests/healthz.yaml")


def test_default_resources_fit_small_nodes_and_ollama_is_off():
    values = _values()
    assert values["ollama"]["enabled"] is False
    assert values["config"]["EMBEDDING_PROVIDER"] == "openai"
    assert values["config"]["LLM_PROVIDER"] == "openai"
    for section in (values["postgresql"], values["seed"], values["dbt"], values["mcpServer"], values["cloudflared"]):
        assert section["resources"]["requests"]["memory"].endswith(("Mi",)), section
    assert values["mcpServer"]["resources"]["limits"]["memory"] == "1Gi"


def test_cloudflared_runs_a_remotely_managed_tunnel_from_a_secret():
    values = _values()["cloudflared"]
    assert values["enabled"] is False
    assert set(values["tokenSecret"]) == {"name", "key"}
    template = _template("cloudflared.yaml")
    assert "{{- if .Values.cloudflared.enabled }}" in template
    assert "- run" in template and "- tunnel" in template
    # The token comes from a Secret; a remotely managed tunnel has no config file or origin flag.
    assert "secretKeyRef" in template and "TUNNEL_TOKEN" in template
    assert "--config" not in template and "--url" not in template


def test_default_image_is_published_and_tagged_with_the_app_version():
    values = _values()
    assert values["image"]["repository"].startswith("ghcr.io/") and values["image"]["tag"] == ""
    assert "default .Chart.AppVersion .Values.image.tag" in _template("_helpers.tpl")
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    assert re.fullmatch(r"\d+\.\d+\.\d+", chart["version"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", chart["appVersion"])


def test_release_workflows_publish_the_chart_and_a_multi_arch_image():
    workflows = ROOT / ".github" / "workflows"
    chart_release = (workflows / "release-chart.yml").read_text()
    assert "packages: write" in chart_release and "helm push" in chart_release
    assert "/charts" in chart_release
    image = (workflows / "publish-image.yml").read_text()
    assert "packages: write" in image
    assert "linux/amd64" in image and "linux/arm64" in image


def test_laptop_values_layer_on_the_defaults_without_secrets():
    for name in ("values-local.yaml", "values-local-ollama.yaml"):
        values = _values(ROOT / "infra" / "k8s" / name)
        assert "secrets" not in values, name
        config = values.get("config") or {}
        assert not [k for k in config if k.endswith(("_KEY", "_TOKEN", "_PASSWORD"))], name
