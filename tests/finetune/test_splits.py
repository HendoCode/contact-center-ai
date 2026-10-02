"""The 800/150/300 split is exact, stratified by category, and deterministic."""

import random
from collections import Counter

import pytest

from models.finetune import data_gen
from models.finetune.gold import load_calls
from models.finetune.splits import make_splits, split_key


@pytest.fixture(scope="module")
def pairs(data_dir):
    return [(c.call_id, c.category) for c in load_calls(data_dir)]


@pytest.fixture(scope="module")
def splits(pairs):
    return make_splits(pairs)


def test_sizes_are_800_150_300(splits):
    assert {k: len(v) for k, v in splits.items()} == {"train": 800, "dev": 150, "test": 300}


def test_splits_partition_the_corpus(pairs, splits):
    everything = [cid for ids in splits.values() for cid in ids]
    assert len(everything) == len(set(everything)) == len(pairs)
    assert set(everything) == {cid for cid, _ in pairs}


def test_every_category_appears_in_every_split_in_proportion(pairs, splits):
    totals = Counter(cat for _, cat in pairs)
    category_of = dict(pairs)
    target = {"train": 800 / 1250, "dev": 150 / 1250, "test": 300 / 1250}
    for name, ids in splits.items():
        per_cat = Counter(category_of[c] for c in ids)
        assert set(per_cat) == set(totals), f"{name} is missing a category"
        for cat, n in totals.items():
            assert abs(per_cat[cat] - n * target[name]) <= 1.5, (name, cat)


def test_split_is_independent_of_input_order(pairs, splits):
    shuffled = pairs[:]
    random.Random(7).shuffle(shuffled)
    assert make_splits(shuffled) == splits


def test_ids_are_listed_in_hash_order(splits):
    for ids in splits.values():
        assert ids == sorted(ids, key=split_key)


def test_split_key_is_the_documented_hash():
    import hashlib

    assert split_key("CALL-00001") == hashlib.sha256(b"summary_v1:CALL-00001").hexdigest()


def test_build_is_reproducible_and_writes_gold_for_every_split(data_dir, tmp_path):
    a = data_gen.build(data_dir, tmp_path / "a")
    b = data_gen.build(data_dir, tmp_path / "b")
    assert a["dataset_sha256"] == b["dataset_sha256"]
    assert a["n_calls"] == 1250
    assert {k: v["n"] for k, v in a["splits"].items()} == {"train": 800, "dev": 150, "test": 300}
    gold_rows = data_gen.read_jsonl(tmp_path / "a" / data_gen.GOLD)
    assert Counter(r["split"] for r in gold_rows) == {"train": 800, "dev": 150, "test": 300}
    test_rows = data_gen.read_jsonl(tmp_path / "a" / "test.jsonl")
    assert len(test_rows) == 300 and all("label" not in r for r in test_rows)
