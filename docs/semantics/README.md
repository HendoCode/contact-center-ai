# Semantic layer export: Apache Ossie

`ossie/ccai_semantic.ossie.yaml` is our dbt/MetricFlow semantic layer
(`olap/dbt/models/marts/semantic/semantic_models.yml` and `metrics.yml`) converted to
[Apache Ossie](https://github.com/apache/ossie). It is converter output, committed as-is. Nobody
edited it by hand. Ossie (incubating) is the Apache project that used to be called Open Semantic
Interchange (OSI). It defines a vendor-neutral YAML/JSON format for semantic models (datasets,
fields, relationships, metrics) so they can move between BI tools, semantic layers, and agents.

Checked on **2026-10-02** against these primary sources:

| What | Where |
|---|---|
| Repository | https://github.com/apache/ossie at commit [`b6c702e`](https://github.com/apache/ossie/tree/b6c702ed1c07e91382a69e870c875cbd19570828) (2026-10-01) |
| Spec version | **`0.2.0.dev0`**, marked *DRAFT, schema may change before 0.2.0 is released* ([`core-spec/spec.md`](https://github.com/apache/ossie/blob/b6c702ed1c07e91382a69e870c875cbd19570828/core-spec/spec.md), [`core-spec/ossie-schema.json`](https://github.com/apache/ossie/blob/b6c702ed1c07e91382a69e870c875cbd19570828/core-spec/ossie-schema.json)) |
| Last tagged spec | `0.1.1`. The spec's version history dates it 2025-12-11. The repo has tag [`osi-0.1.1-rc1`](https://github.com/apache/ossie/tree/osi-0.1.1-rc1) and no GitHub release. |
| Converter used | [`converters/dbt`](https://github.com/apache/ossie/tree/b6c702ed1c07e91382a69e870c875cbd19570828/converters/dbt) (`apache-ossie-dbt 0.2.0.dev0`, CLI `ossie-dbt`). Not on PyPI yet, so it runs from the checkout. |
| Announcement | [Apache Ossie (Incubating): The New Name for Open Semantic Interchange](https://www.snowflake.com/en/blog/apache-ossie-open-semantic-interchange-incubator/) |

## Which converter, and why

Two dbt-to-Ossie converters exist today:

1. **`ossie-dbt msi-to-ossie`** from the Apache repo. It reads dbt's `target/semantic_manifest.json`
   and writes spec `0.2.0.dev0` (one model at the document root). **This is the one committed here.**
2. **dbt-core's built-in writer.** On every `dbt parse`, dbt-core 1.12.5 (our pin) calls MetricFlow
   0.213.0's `MSIToOSIConverter` and writes `olap/dbt/target/osi_document.json` (gitignored) in spec
   `0.1.1` (a `semantic_model` array). It needs no extra install. On our models, though, it emits
   37 relationships where the Apache converter emits 17. The extra 20 join two facts (or a fact and
   a dimension's foreign key) on a shared foreign key, e.g. `account_snapshot__transactions__product`.
   These are many-to-many fan-out joins, not the many-to-one foreign keys that an Ossie relationship
   describes (`from` = "many side", `to` = "one side"). Upstream removed them in
   [apache/ossie#379](https://github.com/apache/ossie/pull/379) ("Skip FOREIGN<->FOREIGN entity
   pairs when building relationships").

Apart from that, the two outputs agree: the same 11 datasets with identical fields, and the same
43 metrics with identical expressions. Both pass the upstream validator for their own spec version
(`validation/validate.py` at `b6c702e` and at `osi-0.1.1-rc1`).

## Reproduce

Converting is offline. Network is only needed once, to fetch the converter and its dependencies.

```bash
REPO=$(pwd)   # the contact-center-ai checkout

# 1. Semantic manifest from our dbt project (offline; needs no database)
uv sync --group dbt
(cd olap/dbt && uv run --group dbt dbt parse --profiles-dir .)
# -> olap/dbt/target/semantic_manifest.json (and dbt's own target/osi_document.json, spec 0.1.1)

# 2. The Apache converter, pinned to the commit we used
git clone https://github.com/apache/ossie.git /tmp/ossie
git -C /tmp/ossie checkout b6c702ed1c07e91382a69e870c875cbd19570828
cd /tmp/ossie/converters/dbt && uv sync
uv run ossie-dbt msi-to-ossie \
  -i "$REPO/olap/dbt/target/semantic_manifest.json" \
  -o "$REPO/docs/semantics/ossie/ccai_semantic.ossie.yaml" \
  --model-name ccai_semantic
# no conversion warnings on our models

# 3. Validate against the spec (JSON Schema, unique names, references, SQL syntax)
cd /tmp/ossie && uv run --project converters/dbt --with jsonschema \
  python validation/validate.py "$REPO/docs/semantics/ossie/ccai_semantic.ossie.yaml"
# Validation PASSED
```

Our run: dbt-core 1.12.5 / dbt-metricflow 0.15.0 for the parse. The converter environment resolved
metricflow 0.211.0, sqlglot 30.12.0 and uv 0.12.22. Re-run steps 1–2 after any change to
`semantic_models.yml` or `metrics.yml`. `tests/test_ossie_export.py` fails when the committed export
no longer covers every semantic model, entity, dimension, measure and metric.

## What carries over, and what doesn't

The table below maps each construct our YAML uses to the spec `0.2.0.dev0` field the converter
writes it into. Field names are quoted exactly from `core-spec/spec.md`. "No equivalent" means the
spec has no field for it and the converter drops it.

| Our YAML (MetricFlow) | Ossie `0.2.0.dev0` | Status |
|---|---|---|
| `semantic_models[].name` | dataset `name` | mapped |
| `semantic_models[].description` | dataset `description` | mapped |
| `semantic_models[].model: ref('…')` | dataset `source` | partial: the relation dbt rendered for the `dev` (Postgres) target, `"contactcenter"."marts"."d_product"`. A Snowflake or Databricks consumer needs its own relation name. |
| `entities[]` `type: primary`, `expr` | dataset `primary_key` (the column) plus a field named after the entity | mapped |
| `entities[]` `type: foreign` | `relationships[]`: `from`, `to`, `from_columns`, `to_columns`, plus a field | mapped (17 relationships, all many-to-one) |
| `dimensions[]` `type: categorical` | field with `dimension: {is_time: false}` | mapped |
| `dimensions[]` `type: time` | field with `dimension: {is_time: true}` | mapped |
| `dimensions[].type_params.time_granularity: day` | none | no equivalent |
| `dimensions[].expr` | field `expression.dialects[]` (`dialect: ANSI_SQL`) | mapped |
| `defaults.agg_time_dimension` | none | no equivalent: `metric_time` is a MetricFlow concept |
| `measures[]` (name, `expr`, `description`) | a plain field (no `dimension`) with the measure's `expr` and `description` | mapped |
| `measures[].agg` | folded into each metric's `expression` (`SUM`, `AVG`, `COUNT(DISTINCT …)`) | partial: see "Known issues" below |
| `metrics[].name` | metric `name` | mapped |
| `metrics[].description` | metric `description` | mapped |
| `metrics[].label` | none (the spec's Metric object has no `label`) | no equivalent: dropped |
| `metrics[].type` (`simple`, `ratio`, `derived`) and `type_params` | inlined into one metric `expression` | partial: the result is right but the structure is gone (e.g. `net_member_liquidity` no longer references its two input metrics) |
| `metrics[].filter` (`{{ Dimension('product__lob') }} = '…'`) | `CASE WHEN … THEN … END` inside the metric `expression` | partial: see "Known issues" below |
| (not used by us) | `datatype`, `ai_context`, `unique_keys`, `custom_extensions` | the converter doesn't emit them |

## Known issues in the export

These come from the conversion, not from our models. They are why the export is an interchange
artifact, not a replacement for MetricFlow. A consumer that executes the exported expressions
directly will get different numbers from `mf query`:

1. **The LOB filter points at a field that doesn't exist.** All 21 LOB-filtered metrics (and
   `weighted_mortgage_portfolio_rate` and `net_member_liquidity`, built from them) filter on
   `product__lob`. That is a MetricFlow join path (entity `product`, then dimension `lob` on
   `products`), not a field of `account_snapshot`. Ossie has the `account_snapshot → products`
   relationship, but the expression doesn't say `products.lob`. This is the filter that keeps the
   seven "interest rate" metrics apart.
2. **The three row counts look identical.** `call_volume`, `responses` and `rate_locks_count` all
   export as `SUM(1)`, with no dataset qualifier. `first_contact_resolved_calls` and
   `note_rate_times_balance` are also unqualified. A consumer cannot tell which dataset these count.
3. **Ratios lose MetricFlow's float division.** MetricFlow renders a ratio as
   `CAST(num AS DOUBLE) / CAST(NULLIF(den, 0) AS DOUBLE)` (`metricflow/sql/render/expr_renderer.py`,
   `visit_ratio_computation_expr`). The export writes `(num) / (den)`. On engines with integer
   division (Postgres among them), `first_contact_resolution_rate` and `rate_lock_fallout_pct`
   would truncate to 0, and a zero denominator errors instead of returning null.

## Not done

- The reverse direction (`ossie-dbt ossie-to-msi`) was not run.
- No exporter script or Make target. The converter isn't on PyPI, and the steps above are three commands.
- The known issues above are not reported upstream yet.
