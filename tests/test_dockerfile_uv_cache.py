"""uv's package cache must stay out of image layers. Each `uv sync` instruction in the Dockerfile
mounts the cache directory as a BuildKit cache mount (or disables the cache with UV_NO_CACHE)."""
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"
CACHE_MOUNT = "--mount=type=cache,target=/root/.cache/uv"


def _instructions() -> list[str]:
    """Dockerfile instructions with backslash line continuations joined."""
    joined: list[str] = []
    buffer = ""
    for line in DOCKERFILE.read_text().splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        joined.append(buffer + stripped)
        buffer = ""
    return joined


def test_every_uv_sync_mounts_the_uv_cache_outside_the_layers():
    syncs = [line for line in _instructions() if "uv sync" in line]
    assert len(syncs) == 2
    for line in syncs:
        assert line.startswith("RUN ") and CACHE_MOUNT in line, line
