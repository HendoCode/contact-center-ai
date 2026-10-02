# Retrieval benchmark labels (R3)

`queries.jsonl` holds 40 labeled queries for `make bench`. Each line looks like this:

```json
{"id": "q15", "type": "filter", "query": "escalated credit card limit questions in July",
 "where": {"outcome": "escalated", "date": {"gte": "2026-07-01", "lte": "2026-07-31T23:59:59"}},
 "relevant": ["CALL-…", …],
 "derivation": "category = card_services AND outcome = escalated AND 2026-07-01 <= date <= 2026-07-31T23:59:59"}
```

## How the relevant sets are made

`build_labels.py` computes every relevant set from the seed-42 generator's own output. No retriever is imported or run, and nothing was picked by hand from search results. A query's relevance is a predicate over facts the generator wrote per call:

- `category`, `outcome` and `date`, from `transcripts.json`.
- `member_id`.
- The subject account and its product line (LOB), from `interaction_accounts.json`, `accounts.json` and `products.json`.
- Which template placeholders the call's category speaks aloud, from `CATEGORY_DIALOGUES` in the generator.

`derivation` records that predicate in readable form.

```bash
make seed                                          # deterministic, so the labels are too
python -m retrieval.labels.build_labels            # rewrite queries.jsonl
python -m retrieval.labels.build_labels --check    # exit 1 if queries.jsonl is stale
```

`tests/test_labels.py` re-derives the file and fails if it differs. The tests also check the schema, check that every relevant call passes its query's `where`, and check that every id query's id appears verbatim in each relevant transcript.

## Query types

| type | n | what it tests | how relevance is derived |
|---|---|---|---|
| topical | 11 | Naming a call's subject in the corpus's own words. Four of these add a product facet (home-equity rate, mortgage vs card hardship, fraud on checking) that only the LOB cue words separate. | `category`, plus `lob` for the four product-facet queries |
| filter | 10 | Structured constraints the text cannot carry (outcome and date are never spoken), passed as `where`. Two queries put every constraint in `where` (q12, q13), which tests prefiltering alone. The other eight leave the category to the text, so the backend must rank the right category first inside the filtered pool. | `category` AND the `where` |
| exact | 10 | Verbatim strings, where lexical (FTS) search should win. Six are phrases from exactly one category's template; the builder asserts that no other category's template contains them. Four are ids spoken in the call: two member ids and two "account ending in NNNN" queries. | `category`, or the member or subject account restricted to categories whose template speaks that id |
| paraphrase | 9 | The same intents in other words, where vectors should win. The builder asserts that each query shares at most one content word with its target template; all nine share none. | `category` |

The id queries pick the two members and the two accounts with the most calls that speak the id, breaking ties on the id. That keeps the choice deterministic.

Notes on specific queries:

- "Last week" (q14) means Mon 2026-08-24 to Sun 2026-08-30, the week before the generator's fixed `REFERENCE_DATE` of 2026-09-01.
- "This summer" (q16) means June to August.

## What recall can and cannot measure here

- **The corpus is templated.** With names and figures masked, the 1,250 transcripts reduce to fewer than 30 distinct texts, one to five per category (`docs/design/finetune.md`, "Corpus limits"). Within a category, calls differ only in names, ids, figures and the LOB label words. So:
  - Topical, paraphrase and exact-phrase recall measure category discrimination: whether the top-k are from the right category. Ranking among the many equally relevant calls of one category is arbitrary, and no metric here rewards it.
  - The product-facet queries are the only test of finer distinctions, and those rest on a few label words ("drawn balance", "minimum payment", "ledger balance").
  - Id queries are the only queries with a single right answer per call, and lexical search has a structural edge on them.
- **Outcome is never spoken.** No text-only search can find "escalated" calls. Filter queries therefore rely on `where`, and their scores measure prefiltering plus category ranking inside the filtered pool.
- **Recall is capped.** Most relevant sets are larger than k (up to 179 calls), so recall@k is |relevant ∩ top-k| / min(k, |relevant|), BEIR's capped recall. Under it, a top-k made entirely of relevant calls scores 1.0.
- **The numbers do not transfer.** Real transcripts are not templated. These numbers compare backends and modes on this corpus; they say nothing about absolute quality on real calls.

## Date ranges

Stored `date` metadata is an ISO datetime (`2026-03-31T14:02:11`), and both backends compare it as a string. A date-only inclusive upper bound such as `"lte": "2026-03-31"` would therefore drop calls on the 31st. Label ranges close on `T23:59:59` instead. The `where` contract in `retrieval/base.py` says "date only"; reconciling the two is listed as a follow-up on the R3 PR.
