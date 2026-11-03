"""The live docs state the MCP tool count the server actually has.

Dated design notes and the historical hand-off carry an "as of" banner and are not checked.
"""

import re
from pathlib import Path

from tests.test_server_handshake import EXPECTED_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[1]
LIVE_DOCS = [
    "README.md",
    "CLAUDE.md",
    "docs/MAP.md",
    "docs/TOUR.md",
    "docs/architecture/README.md",
    "docs/architecture/workspace.dsl",
    "ccai_mcp/README.md",
    "agent/README.md",
]
STALE = re.compile(r"\b(?:five|six|sixth)\s+(?:MCP\s+)?tools?\b|\bthe (?:six|five) names\b", re.I)
WORDS = {7: "seven"}


def test_expected_tools_count_matches_the_words_used_in_docs():
    assert len(EXPECTED_TOOLS) in WORDS, "update WORDS and the docs when the tool count changes"


def test_live_docs_do_not_state_an_old_tool_count():
    hits = []
    for rel in LIVE_DOCS:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if STALE.search(line) and "it had then" not in line:
                hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    assert not hits, "\n".join(hits)
