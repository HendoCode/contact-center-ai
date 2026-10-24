"""Change-analysis lookups for the `explain_change` route: the change log, branch names and
period phrases. Pure functions: no LLM, no database.

The change log (`change_log.json`, name and date only) and the branch list
(`branches.json`) are written by the synthetic-data generator next to the other generated
files. A missing file reads as an empty list, so a service that cannot see them still runs;
a question that names a logged change then says it has no change log to match it against.
"""

import json
import re
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path

SYNTHETIC_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"
PAD_DAYS = 3  # a period is widened by this many days on each side before it is searched

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sept", "sep"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_LEAD = r"(?:(?:in|during|since|after|around|on|from)\s+)?(?:the\s+)?"
_ISO_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_POINT_RE = re.compile(
    rf"^\s*{_LEAD}({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*$", re.IGNORECASE
)
_MONTH_RE = re.compile(
    rf"^\s*{_LEAD}(early|mid|late)?[\s-]*(?:(?:of|in)\s+)?({_MONTH})\.?\s*$", re.IGNORECASE
)


def _read_list(name: str, directory: Path | None) -> list[dict]:
    path = (directory or SYNTHETIC_DIR) / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def load_change_log(directory: Path | None = None) -> list[dict]:
    """Logged changes as [{"name", "date"}] in date order; [] when the file is not there."""
    entries = [
        {"name": str(e["name"]), "date": str(e["date"])}
        for e in _read_list("change_log.json", directory)
        if isinstance(e, dict) and e.get("name") and e.get("date")
    ]
    return sorted(entries, key=lambda e: e["date"])


def load_branches(directory: Path | None = None) -> list[str]:
    """Branch names that a question can filter members by; [] when the file is not there."""
    return [str(e["name"]) for e in _read_list("branches.json", directory)
            if isinstance(e, dict) and e.get("name")]


def match_logged_change(name: str | None, log: list[dict]) -> dict | None:
    """The log entry a name refers to: an exact name, ignoring case and surrounding space."""
    if not name:
        return None
    wanted = " ".join(name.lower().split())
    return next((e for e in log if " ".join(e["name"].lower().split()) == wanted), None)


# ── periods ──────────────────────────────────────────────────────────────────

def _bounds(data_range: dict) -> tuple[date, date]:
    return date.fromisoformat(data_range["start"]), date.fromisoformat(data_range["end"])


def _year_for(month: int, data_range: dict) -> int | None:
    """The year in which `month` falls inside the data range (the earliest, if two do)."""
    start, end = _bounds(data_range)
    for year in range(start.year, end.year + 1):
        first = date(year, month, 1)
        last = date(year, month, monthrange(year, month)[1])
        if first < end and last >= start:
            return year
    return None


def _padded(first: date, last_inclusive: date) -> dict:
    return {
        "start": (first - timedelta(days=PAD_DAYS)).isoformat(),
        "end": (last_inclusive + timedelta(days=1 + PAD_DAYS)).isoformat(),
    }


def parse_point(phrase: str | None, data_range: dict) -> date | None:
    """An ISO date or "Month D" as a date; None for anything else.

    "Month D" takes the year in which that month falls inside the data range.
    """
    if not phrase:
        return None
    iso = _ISO_RE.match(phrase)
    try:
        if iso:
            return date(int(iso[1]), int(iso[2]), int(iso[3]))
        point = _POINT_RE.match(phrase)
        if point:
            month = _MONTHS[point[1].lower()]
            year = _year_for(month, data_range)
            return date(year, month, int(point[2])) if year else None
    except ValueError:  # February 30th and the like
        return None
    return None


def parse_period(phrase: str | None, data_range: dict) -> dict | None:
    """A phrase as {"start", "end"} (end exclusive), padded by 3 days on each side, or None.

    - An ISO date or "Month D" is a point.
    - "Month", "in", "during", "since" or "after Month" is that month.
    - "early", "mid" or "late Month" is days 1-10, 11-20, or 21 to the end of the month.

    The year is the one in which the month falls inside `data_range`
    ({"start", "end"}, end exclusive). Anything else, and a month outside the data, is None.
    """
    point = parse_point(phrase, data_range)
    if point:
        return _padded(point, point)
    month_match = _MONTH_RE.match(phrase or "")
    if not month_match:
        return None
    month = _MONTHS[month_match[2].lower()]
    year = _year_for(month, data_range)
    if year is None:
        return None
    last_day = monthrange(year, month)[1]
    first, last = {
        None: (1, last_day), "early": (1, 10), "mid": (11, 20), "late": (21, last_day),
    }[(month_match[1] or "").lower() or None]
    return _padded(date(year, month, first), date(year, month, last))


def contains_phrase(text: str | None, phrase: str) -> bool:
    """Whether `phrase` appears in `text` as whole words, ignoring case."""
    return bool(text) and re.search(rf"\b{re.escape(phrase.lower())}\b", text.lower()) is not None


def is_iso_date(phrase: str | None) -> bool:
    return bool(phrase) and _ISO_RE.match(phrase) is not None
