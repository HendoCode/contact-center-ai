"""A service that overrides `environment:` loses the `*app` anchor's DBT_HOST (a YAML merge
replaces the mapping), and the dbt profile then falls back to localhost."""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def test_services_with_database_url_set_dbt_host():
    services = yaml.safe_load(COMPOSE.read_text())["services"]
    missing = [
        name
        for name, svc in services.items()
        if "DATABASE_URL" in (svc.get("environment") or {})
        and "DBT_HOST" not in svc["environment"]
    ]
    assert not missing, f"set DBT_HOST: db on {missing}"
