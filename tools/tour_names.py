#!/usr/bin/env python3
"""Print the Azure portal tour tables from docs/TOUR.md with live resource names filled in.

    python tools/tour_names.py        # or: make tour-names

The walk lives only in docs/TOUR.md (the "Azure portal tour" section). This script reads its
tables and swaps each `<suffix>` name pattern for the real name from `terraform output -json` in
`infra/azure/envs/dev` and `infra/azure/bootstrap`. Nothing is written to disk, so no live name
is ever committed. A row keeps its masked pattern when terraform is missing, a root has no state
or is not initialised, or the output is absent; a trailing note says which roots resolved.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOUR = ROOT / "docs" / "TOUR.md"
ROOTS = {"dev": ROOT / "infra/azure/envs/dev", "bootstrap": ROOT / "infra/azure/bootstrap"}

# name pattern in docs/TOUR.md -> (root, terraform output, transform of its value)
FIRST_LABEL = lambda fqdn: fqdn.split(".", 1)[0]  # noqa: E731  server name is the FQDN's first label
IDENTITY = lambda v: v  # noqa: E731
LIVE: dict[str, tuple[str, str, object]] = {
    "psql-ccai-dev-<suffix>": ("dev", "postgres_fqdn", FIRST_LABEL),
    "stccaidev<suffix>": ("dev", "adls_account_name", IDENTITY),
    "acrccaidev<suffix>": ("dev", "acr_name", IDENTITY),
    "stccaitf<suffix>": ("bootstrap", "state_storage_account_name", IDENTITY),
    "kv-ccai-<suffix>": ("bootstrap", "key_vault_name", IDENTITY),
}


def terraform_outputs(root: Path) -> dict | None:
    """`terraform output -json` for a root, or None when it cannot be read."""
    try:
        done = subprocess.run(
            ["terraform", f"-chdir={root}", "output", "-json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return json.loads(done.stdout) if done.returncode == 0 and done.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def tour_section(text: str) -> list[str]:
    """The lines of the "Azure portal tour" section, up to the next horizontal rule."""
    lines = text.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## Azure portal tour"))
    end = next((i for i in range(start, len(lines)) if lines[i] == "---"), len(lines))
    return lines[start:end]


def resolve(lines: list[str], outputs: dict[str, dict | None]) -> tuple[list[str], set[str]]:
    resolved: set[str] = set()
    out = []
    for line in lines:
        for pattern, (root, key, convert) in LIVE.items():
            value = (outputs.get(root) or {}).get(key, {}).get("value")
            if f"`{pattern}`" in line and isinstance(value, str) and value:
                line = line.replace(f"`{pattern}`", f"`{convert(value)}`")
                resolved.add(root)
        out.append(line)
    return out, resolved


def main() -> int:
    outputs = {name: terraform_outputs(path) for name, path in ROOTS.items()}
    lines, resolved = resolve(tour_section(TOUR.read_text()), outputs)
    print("\n".join(lines).rstrip())
    print()
    for name in ROOTS:
        state = "live names filled in" if name in resolved else (
            "masked pattern kept: no terraform, no state, or output unavailable")
        print(f"> {name} ({ROOTS[name].relative_to(ROOT)}): {state}")
    print("> The Entra service principal's name is not a terraform output; find it in Entra ID > App registrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
