"""The generator plants dated events with known drivers, and records the answer key.

These tests run the real generator offline into temp folders. They check that two runs match
byte for byte, that the events add calls without disturbing the baseline stream, that volumes,
driver shares and CSAT shifts match events.yml, and that no label from the answer key shows up
in anything the services read.
"""

import hashlib
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from fractions import Fraction
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_CALLS = 1250
DATA_DAYS = 183                       # 2026-03-03 .. 2026-09-01 inclusive
TOTAL_WEEKS = DATA_DAYS / 7


def hashes(folder: Path) -> dict[str, str]:
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def largest_remainder(shares: dict, total: int) -> dict:
    exact = {k: Fraction(str(v)) * total for k, v in shares.items()}
    counts = {k: int(v) for k, v in exact.items()}
    order = sorted(exact, key=lambda k: (-(exact[k] - counts[k]), list(exact).index(k)))
    for k in order[: total - sum(counts.values())]:
        counts[k] += 1
    return counts


def day(text: str) -> date:
    return date.fromisoformat(text[:10])


def in_filters(category: str, branch: str, filters: dict) -> bool:
    return (category in filters.get("category", [category])
            and branch in filters.get("home_branch", [branch]))


def event_calls(corpus, event_id: str) -> list[str]:
    return [cid for cid, lab in corpus.truth["calls"].items() if lab["event"] == event_id]


# ── determinism and the baseline stream ───────────────────────────────────────

def test_two_runs_write_identical_files(corpus, corpus_again):
    first, second = hashes(corpus.dir), hashes(corpus_again.dir)
    assert first == second
    assert "ground_truth/events.json" in first and "change_log.json" in first


def test_events_add_calls_without_disturbing_the_baseline_stream(corpus, baseline_only):
    """Everything the baseline stream drew is the same with and without events."""
    planted = set(corpus.truth["calls"])
    assert not baseline_only.truth["calls"]
    assert len(baseline_only.transcripts) == BASELINE_CALLS
    assert len(corpus.transcripts) == BASELINE_CALLS + len(planted)

    def fingerprint(c, ids):
        trs, ints = c.transcripts, c.interactions
        return sorted(
            (ints[i]["started_at"], ints[i]["member_id"], ints[i]["category_code"],
             ints[i]["staff_id"], ints[i]["outcome"], ints[i]["channel"],
             ints[i]["duration_seconds"], trs[i]["full_text"]) for i in ids)

    assert fingerprint(corpus, set(corpus.transcripts) - planted) == \
        fingerprint(baseline_only, baseline_only.transcripts)

    def surveys(c, skip=frozenset()):
        ints = c.interactions
        return sorted((ints[s["call_id"]]["started_at"], ints[s["call_id"]]["member_id"],
                       s["score"], s["comment"], s["survey_date"])
                      for s in c.load("csat.json") if s["call_id"] not in skip)

    assert surveys(corpus, planted) == surveys(baseline_only)
    for name in ("members.json", "accounts.json", "banking_accounts.json", "staff.json"):
        mine = corpus.load(name)
        theirs = baseline_only.load(name)
        assert mine == theirs, name


def test_baseline_transactions_survive_and_only_overdraft_fees_differ(corpus, baseline_only):
    """Apart from overdraft fees from the fee change on (repriced $29 to $35, plus the 2-4 fees
    each same-day call is backed by), the ledger is what the baseline stream alone produces."""
    key = lambda t: (t["account_id"], t["posted_at"], t["amount"], t["txn_type"],  # noqa: E731
                     t["status"], t["description"], t["fee_type"])
    with_events = Counter(map(key, corpus.load("transactions.json")))
    without = Counter(map(key, baseline_only.load("transactions.json")))
    dropped, added = without - with_events, with_events - without
    assert dropped and added
    for k in dropped:
        assert k[3:7:3] == ("fee", "overdraft") and k[1][:10] >= "2026-04-15" and k[2] == -29.0
    for k in added:
        assert k[3] == "fee" and k[6] == "overdraft" and k[1][:10] >= "2026-04-15" and k[2] == -35.0
    same_day_calls = sum(1 for c in corpus.truth["calls"].values()
                         if c["driver"] == "multiple_overdrafts_same_day")
    planted_rows = sum(added.values()) - sum(dropped.values())
    assert 2 * same_day_calls <= planted_rows <= 4 * same_day_calls


def test_ids_follow_time_order_and_do_not_mark_events(corpus):
    ints = corpus.load("interactions.json")
    assert [i["interaction_id"] for i in ints] == list(range(1, len(ints) + 1))
    assert [i["call_id"] for i in ints] == [f"CALL-{n:05d}" for n in range(1, len(ints) + 1)]
    stamps = [i["started_at"] for i in ints]
    assert stamps == sorted(stamps)
    tx = corpus.load("transactions.json")
    assert [t["transaction_id"] for t in tx] == list(range(1, len(tx) + 1))
    assert [t["posted_at"] for t in tx] == sorted(t["posted_at"] for t in tx)
    # Event calls are spread through the id range, not appended at the end.
    ids = sorted(int(c.split("-")[1]) for c in corpus.truth["calls"])
    assert ids[0] < BASELINE_CALLS // 2 and ids[-1] > len(ints) - 20


def test_event_calls_fall_inside_their_phases_on_weekdays(corpus):
    ints = corpus.interactions
    for ev in corpus.truth["events"]:
        for cid in event_calls(corpus, ev["id"]):
            lab = corpus.truth["calls"][cid]
            phase = ev["phases"][lab["phase"]]
            started = datetime.fromisoformat(ints[cid]["started_at"])
            assert day(phase["start"]) <= started.date() < day(phase["end"]), cid
            assert started.weekday() < 5, cid


# ── volume ────────────────────────────────────────────────────────────────────

def test_event_volume_follows_the_multiplier_and_the_measured_baseline_rate(corpus, events_config):
    planted = set(corpus.truth["calls"])
    members = {f"MBR-{m['member_id']:06d}": m for m in corpus.load("members.json")}
    baseline = [t for t in corpus.transcripts.values() if t["call_id"] not in planted]
    assert len(baseline) == BASELINE_CALLS
    for ev_cfg, ev in zip(events_config["events"], corpus.truth["events"], strict=True):
        branch = (ev_cfg["population"] or {}).get("home_branch")
        for index, (phase_cfg, phase) in enumerate(zip(ev_cfg["phases"], ev["phases"], strict=True)):
            weeks = (day(phase["end"]) - day(phase["start"])).days / 7
            for category, mult in phase_cfg["volume"].items():
                rate = sum(1 for t in baseline if t["category"] == category and
                           (branch is None or members[t["member_id"]]["home_branch"] == branch)
                           ) / TOTAL_WEEKS
                expected = round((mult - 1) * rate * weeks)
                actual = sum(
                    1 for cid in event_calls(corpus, ev["id"])
                    if corpus.truth["calls"][cid]["phase"] == index
                    and corpus.transcripts[cid]["category"] == category)
                assert actual == expected == sum(phase["planned"][category].values()), \
                    (ev["id"], category)


def test_phases_end_where_the_next_begins_and_stop_at_the_data(corpus):
    assert corpus.truth["data_range"] == {"start": "2026-03-03", "end": "2026-09-02"}
    for ev in corpus.truth["events"]:
        assert ev["phases"][0]["start"] == ev["date"]
        for a, b in zip(ev["phases"], ev["phases"][1:]):
            assert a["end"] == b["start"]
        assert ev["phases"][-1]["end"] <= "2026-09-02"
        assert all(day(p["start"]).weekday() < 5 for p in ev["phases"]), ev["id"]


def test_westside_event_calls_come_from_westside_members_only(corpus):
    members = {f"MBR-{m['member_id']:06d}": m for m in corpus.load("members.json")}
    for cid in event_calls(corpus, "westside_branch_closure"):
        assert members[corpus.transcripts[cid]["member_id"]]["home_branch"] == "Westside"


# ── drivers, patterns, quotas ─────────────────────────────────────────────────

def test_driver_counts_are_exact_for_every_event_phase_and_category(corpus, events_config):
    by_cell = defaultdict(Counter)
    for cid in corpus.truth["calls"]:
        lab = corpus.truth["calls"][cid]
        by_cell[(lab["event"], lab["phase"], corpus.transcripts[cid]["category"])][lab["driver"]] += 1
    for ev_cfg in events_config["events"]:
        for (event, phase, category), got in by_cell.items():
            if event != ev_cfg["id"]:
                continue
            drivers = [d for d in ev_cfg["drivers"] if category in d["share"]]
            want = largest_remainder({d["id"]: d["share"][category] for d in drivers},
                                     sum(got.values()))
            assert dict(got) == {k: v for k, v in want.items() if v}, (event, phase, category)


def test_balance_calls_in_the_fee_event_carry_only_the_two_balance_drivers(corpus):
    drivers = {corpus.truth["calls"][cid]["driver"]
               for cid in event_calls(corpus, "fee_schedule_change")
               if corpus.transcripts[cid]["category"] == "account_balance"}
    assert drivers == {"unaware_of_change", "multiple_overdrafts_same_day"}


def test_default_window_shares_match_events_yml_within_five_points(corpus, events_config):
    cfg = {e["id"]: e for e in events_config["events"]}
    for ev in corpus.truth["events"]:
        window = ev["default_window"]
        after = window["after"]
        in_window = [cid for d in window["drivers"] for cid in d["after"]]
        assert in_window
        per_cat = Counter(corpus.transcripts[cid]["category"] for cid in in_window)
        for d in window["drivers"]:
            spec = next(x for x in cfg[ev["id"]]["drivers"] if x["id"] == d["id"])
            want = sum(spec["share"].get(c, 0) * n for c, n in per_cat.items()) / len(in_window)
            got = len(d["after"]) / len(in_window)
            assert abs(got - want) <= 0.05, (ev["id"], d["id"], got, want, after)


def test_every_driver_and_pattern_has_at_least_six_calls_in_its_default_window(corpus):
    for ev in corpus.truth["events"]:
        window = ev["default_window"]
        for d in window["drivers"]:
            assert len(d["after"]) >= 6, (ev["id"], d["id"])
        for p in window["patterns"]:
            assert sum(len(v) for v in p["variants"].values()) >= 6, (ev["id"], p["id"])


def test_fee_types_follow_the_driver_split(corpus):
    other = [cid for cid, lab in corpus.truth["calls"].items() if lab["driver"] == "other_fee_question"]
    names = Counter()
    for cid in other:
        text = corpus.transcripts[cid]["full_text"]
        for key, name in (("nsf", "returned item (NSF) fee"), ("monthly_maintenance", "account maintenance fee"),
                          ("atm", "out-of-network ATM fee")):
            if name in text:
                names[key] += 1
    assert sum(names.values()) == len(other)
    assert names["nsf"] and names["monthly_maintenance"] and names["atm"]


def test_coaching_pattern_spans_the_team_with_one_agent_per_call(corpus):
    ints = corpus.interactions
    refused = [cid for cid, lab in corpus.truth["calls"].items()
               if lab["driver"] == "courtesy_refund_refused"]
    pattern = [cid for cid in refused if corpus.truth["calls"][cid]["pattern"]]
    assert refused and 0.35 <= len(pattern) / len(refused) <= 0.45
    agents = [ints[cid]["staff_id"] for cid in pattern]
    assert len(set(agents)) == len(agents)
    variants = Counter(corpus.truth["calls"][cid]["variant"] for cid in pattern)
    assert set(variants) == {"once_a_year", "once_ever", "partial_refund"}
    assert max(variants.values()) - min(variants.values()) <= 2


# ── ledger ────────────────────────────────────────────────────────────────────

def test_same_day_overdraft_calls_are_backed_by_two_to_four_ledger_fees(corpus):
    interactions = corpus.interactions
    subject = {ia["interaction_id"]: ia["account_id"] for ia in corpus.load("interaction_accounts.json")
               if ia["account_role"] == "subject"}
    banking = {b["account_id"]: b for b in corpus.load("banking_accounts.json")}
    fees = defaultdict(list)
    for t in corpus.load("transactions.json"):
        if t["fee_type"] == "overdraft":
            fees[t["account_id"]].append(t)
    change = "2026-04-15"
    calls = [cid for cid, lab in corpus.truth["calls"].items()
             if lab["driver"] == "multiple_overdrafts_same_day"]
    assert len(calls) >= 12
    for cid in calls:
        account = subject[interactions[cid]["interaction_id"]]
        assert banking[account]["sub_type"] == "checking"
        said = set(re.findall(r"\b([2-4]) (?:overdraft|items)", corpus.transcripts[cid]["full_text"]))
        assert len(said) == 1, cid
        n = int(said.pop())
        called = interactions[cid]["started_at"]
        same_day = Counter(t["posted_at"] for t in fees[account]
                           if change <= t["posted_at"][:10] <= called[:10] and t["amount"] == -35.0)
        assert n in same_day.values(), (cid, same_day)


def test_fee_lines_carry_a_fee_type_and_the_schedule_price(corpus):
    banking = {b["account_id"]: b for b in corpus.load("banking_accounts.json")}
    accounts = {a["account_id"] for a in corpus.load("accounts.json")}
    kinds = Counter()
    for t in corpus.load("transactions.json"):
        assert t["account_id"] in accounts
        if t["txn_type"] != "fee":
            assert t["fee_type"] is None
            continue
        kinds[t["fee_type"]] += 1
        if t["account_id"] not in banking:
            assert t["fee_type"] == "other"
            continue
        b = banking[t["account_id"]]
        want = {"overdraft": -(35.0 if t["posted_at"][:10] >= "2026-04-15" else 29.0),
                "nsf": -29.0, "atm": -3.0,
                "monthly_maintenance": -(b["monthly_fee"] or 5.0)}[t["fee_type"]]
        assert t["amount"] == want, t
    assert set(kinds) == {"overdraft", "nsf", "monthly_maintenance", "atm", "other"}
    banked = sum(v for k, v in kinds.items() if k != "other")
    assert 0.38 <= kinds["overdraft"] / banked <= 0.50      # 40% by hash, plus the planted fees


def test_home_branch_is_a_stable_hash_of_the_member_id(corpus, events_config):
    shares = events_config["branches"]
    members = corpus.load("members.json")

    def expect(member_id):
        u = int.from_bytes(hashlib.sha256(str(member_id).encode()).digest()[:8], "big") / 2**64
        cum = 0.0
        for name, share in shares.items():
            cum += share
            if u < cum:
                return name
        return list(shares)[-1]

    assert all(m["home_branch"] == expect(m["member_id"]) for m in members)
    counts = Counter(m["home_branch"] for m in members)
    for name, share in shares.items():
        assert abs(counts[name] / len(members) - share) <= 0.06, name
    assert corpus.load("branches.json") == [{"name": n} for n in shares]


# ── CSAT ──────────────────────────────────────────────────────────────────────

def test_csat_shifts_match_events_yml_within_tolerance(corpus, events_config):
    """Expected change in the default window's CSAT mean, from the nominal phase shifts and the
    window's response mix, against what the lowered scores actually changed it by."""
    ints = corpus.interactions
    members = {f"MBR-{m['member_id']:06d}": m for m in corpus.load("members.json")}
    surveys = corpus.load("csat.json")
    for ev_cfg, ev in zip(events_config["events"], corpus.truth["events"], strict=True):
        window = ev["default_window"]
        filters, after = window["filters"], window["after"]

        scoped = [s for s in surveys
                  if in_filters(s["category"], members[s["member_id"]]["home_branch"], filters)
                  and after["start"] <= s["survey_date"][:10] < after["end"]]
        shifts = {i: p["csat_shift"] for i, p in enumerate(ev_cfg["phases"])}
        own = [shifts[corpus.truth["calls"][s["call_id"]]["phase"]] for s in scoped
               if s["call_id"] in corpus.truth["calls"]
               and corpus.truth["calls"][s["call_id"]]["event"] == ev["id"]]
        expected = sum(own) / len(scoped)
        metrics = window["metrics"]
        assert metrics["responses"]["after"] == len(scoped)
        assert metrics["csat_shift_expected"] == pytest.approx(expected, abs=1e-3)
        assert abs(metrics["csat_shift_realized"] - expected) <= 0.15, ev["id"]
        assert expected < -0.2
        assert ints  # surveys belong to real calls


def test_default_window_metrics_recompute_from_the_data_files(corpus):
    ints = corpus.interactions
    members = {f"MBR-{m['member_id']:06d}": m for m in corpus.load("members.json")}
    surveys = corpus.load("csat.json")
    for ev in corpus.truth["events"]:
        window, filters = ev["default_window"], ev["default_window"]["filters"]

        def match(call_id, filters=filters):
            i, t = ints[call_id], corpus.transcripts[call_id]
            return in_filters(i["category_code"], members[t["member_id"]]["home_branch"], filters)

        for side in ("before", "after"):
            lo, hi = window[side]["start"], window[side]["end"]
            volume = sum(1 for cid in ints if match(cid) and lo <= ints[cid]["started_at"][:10] < hi)
            scores = [s["score"] for s in surveys if match(s["call_id"]) and lo <= s["survey_date"][:10] < hi]
            assert window["metrics"]["call_volume"][side] == volume
            assert window["metrics"]["responses"][side] == len(scores)
            assert window["metrics"]["csat_average_score"][side] == round(sum(scores) / len(scores), 4)


def test_default_windows_are_the_42_day_windows_around_the_event(corpus):
    for ev in corpus.truth["events"]:
        w = ev["default_window"]
        assert w["after"]["start"] == w["before"]["end"] == ev["date"]
        lengths = {(day(w[s]["end"]) - day(w[s]["start"])).days for s in ("before", "after")}
        assert len(lengths) == 1 and lengths.pop() <= 42
    fee = corpus.truth["events"][0]["default_window"]
    assert (fee["before"], fee["after"]) == (
        {"start": "2026-03-04", "end": "2026-04-15"}, {"start": "2026-04-15", "end": "2026-05-27"})


# ── the answer key stays in the answer key ────────────────────────────────────

def label_strings(corpus, events_config) -> set[str]:
    labels = set()
    for ev in events_config["events"]:
        labels.add(ev["id"])
        for d in ev["drivers"]:
            labels.add(d["id"])
            for pat in d.get("patterns", []):
                labels.add(pat["id"])
                labels.update(pat["variants"])
    return labels


def test_no_label_from_the_answer_key_appears_in_anything_the_services_read(corpus, events_config):
    labels = label_strings(corpus, events_config)
    assert len(labels) == 21      # 4 events, 13 drivers, 1 pattern, 3 variants
    # Ids only: an event's name (the change log) and ordinary words may appear in dialogue.
    served = [p for p in corpus.dir.rglob("*.json") if "ground_truth" not in p.parts]
    assert len(served) >= 20
    for path in served:
        text = path.read_text().lower()
        for label in labels:
            assert label.lower() not in text, (path.name, label)


def test_no_label_reaches_the_vector_store_metadata(corpus, events_config):
    from rag import pipeline

    docs = pipeline.transcripts_to_documents(list(corpus.transcripts.values()), corpus.dir)
    assert len(docs) == len(corpus.transcripts)
    labels = label_strings(corpus, events_config)
    for doc in docs:
        blob = (doc.page_content + repr(doc.metadata)).lower()
        assert not any(label in blob for label in labels)
        assert "event" not in doc.metadata and "driver" not in doc.metadata
    assert {d.metadata["home_branch"] for d in docs} == {"Westside", "Downtown", "Northgate", "Riverside"}


def test_change_log_holds_names_and_dates_only(corpus, events_config):
    log = corpus.load("change_log.json")
    assert log == sorted(({"name": e["change_log_name"], "date": e["date"].isoformat()}
                          for e in events_config["events"]), key=lambda r: r["date"])
    assert all(set(row) == {"name", "date"} for row in log)


def test_ground_truth_schema_and_hash(corpus):
    truth = corpus.truth
    assert truth["version"] == 1 and truth["seed"] == 42
    yml = REPO_ROOT / "data" / "synthetic" / "events.yml"
    assert truth["events_yml_sha256"] == hashlib.sha256(yml.read_bytes()).hexdigest()
    assert [e["split"] for e in truth["events"]] == ["golden", "golden", "heldout", "heldout"]
    for ev in truth["events"]:
        window = ev["default_window"]
        assert set(window) == {"filters", "before", "after", "metrics", "drivers", "patterns"}
        assert set(window["metrics"]) >= {"call_volume", "csat_average_score", "responses",
                                          "csat_shift_realized"}
    assert set(truth["events"][0]["default_window"]["patterns"][0]["variants"]) == {
        "once_a_year", "once_ever", "partial_refund"}
    for label in truth["calls"].values():
        assert set(label) == {"event", "phase", "driver", "pattern", "variant"}


def test_answer_key_is_excluded_from_git_and_docker():
    for name in (".gitignore", ".dockerignore"):
        lines = (REPO_ROOT / name).read_text().splitlines()
        assert "data/synthetic/ground_truth/" in lines, name
    assert (REPO_ROOT / "data/synthetic/events.yml").exists()


def test_the_services_never_read_events_yml_or_the_answer_key():
    """Only the generator, the evaluation tools and the offline builders may open them."""
    allowed = {"models", "evals", "retrieval/labels", "tests", "data/synthetic"}
    for folder in ("agent", "ccai_mcp", "rag", "retrieval", "olap"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if any(rel.startswith(a) for a in allowed):
                continue
            source = path.read_text()
            assert "events.yml" not in source and "ground_truth" not in source, rel


def test_timestamps_of_event_calls_are_baseline_shaped(corpus):
    """Same business hours as the baseline (the timing function is shared)."""
    planted = set(corpus.truth["calls"])
    hours = Counter()
    for cid, i in corpus.interactions.items():
        if cid in planted:
            hours[datetime.fromisoformat(i["started_at"]).hour] += 1
    assert set(hours) <= set(range(8, 19))


def test_window_dates_are_whole_days_and_end_exclusive(corpus):
    for ev in corpus.truth["events"]:
        for side in ("before", "after"):
            assert day(ev["default_window"][side]["end"]) - day(ev["default_window"][side]["start"]) \
                >= timedelta(days=14)
