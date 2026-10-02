"""
Deterministic train/dev/test split, stratified by category.

Within each category, calls are ordered by `sha256("summary_v1:" + call_id)`
and sliced into test, dev and train. The order depends only on the call_id, not on
input order or position in the corpus. Per-split sizes are the design note's 800 / 150 / 300 of 1,250, distributed across
categories by largest remainder so each split total is exact.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from fractions import Fraction

SPLIT_NAMES = ("train", "dev", "test")
# Design note: 800 train, 150 dev, 300 test of the 1,250 generated calls.
SPLIT_SIZES = {"train": 800, "dev": 150, "test": 300}


def split_key(call_id: str) -> str:
    """The ordering key for a call: sha256 of the versioned call_id."""
    return hashlib.sha256(f"summary_v1:{call_id}".encode()).hexdigest()


def _largest_remainder(shares: dict[str, Fraction], total: int) -> dict[str, int]:
    """Round `shares` to integers that sum to `total` (ties go to the earlier category name)."""
    counts = {k: int(v) for k, v in shares.items()}
    leftover = total - sum(counts.values())
    by_remainder = sorted(shares, key=lambda k: (-(shares[k] - counts[k]), k))
    for k in by_remainder[:leftover]:
        counts[k] += 1
    return counts


def make_splits(calls: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Split `(call_id, category)` pairs into train/dev/test lists of call_ids, each in hash order."""
    n = len(calls)
    total_size = sum(SPLIT_SIZES.values())
    test_n = round(n * SPLIT_SIZES["test"] / total_size)
    dev_n = round(n * SPLIT_SIZES["dev"] / total_size)

    by_category: dict[str, list[str]] = defaultdict(list)
    for call_id, category in calls:
        by_category[category].append(call_id)
    for ids in by_category.values():
        ids.sort(key=split_key)

    sizes = {c: len(ids) for c, ids in by_category.items()}
    test_q = _largest_remainder({c: Fraction(s * test_n, n) for c, s in sizes.items()}, test_n)
    dev_q = _largest_remainder({c: Fraction(s * dev_n, n) for c, s in sizes.items()}, dev_n)

    splits: dict[str, list[str]] = {name: [] for name in SPLIT_NAMES}
    for category, ids in by_category.items():
        t = min(test_q[category], len(ids))
        d = min(dev_q[category], len(ids) - t)
        splits["test"] += ids[:t]
        splits["dev"] += ids[t : t + d]
        splits["train"] += ids[t + d :]
    for ids in splits.values():
        ids.sort(key=split_key)
    return splits
