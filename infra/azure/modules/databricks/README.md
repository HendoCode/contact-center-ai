# databricks

Premium workspace plus one single-node **all-purpose** cluster that auto-terminates after 10 minutes. All-purpose rather than the hand-off's job cluster because dbt, `mf` and S2's connector need an interactive `http_path` (output). No SQL warehouse.

The calling root configures the `databricks` provider from the `workspace_id` output (see `envs/dev/providers.tf`). The env gates the module behind `enable_databricks` (default `false`).

## Stephen runs

**Primary path: `tools/bootstrap-databricks.sh`.** It sets up what dbt and the S2 loaders need on a workspace, either this module's (after `terraform apply` with `enable_databricks = true`) or any existing one, including Databricks Free Edition.

```bash
tools/bootstrap-databricks.sh --dry-run --from-terraform              # every step, nothing sent
tools/bootstrap-databricks.sh --from-terraform                         # this module's workspace
tools/bootstrap-databricks.sh --host dbc-xxxxxxxx-xxxx.cloud.databricks.com --catalog workspace   # Free Edition
```

It asks for a personal access token without echo (or reads it from stdin), never from argv. Then it:

1. Checks the token with `GET /api/2.0/preview/scim/v2/Me`.
2. Finds the SQL warehouse `ccai-sql` or creates it (serverless, 2X-Small, 10-minute auto-stop), and records its HTTP path.
3. Runs `CREATE CATALOG`, `CREATE SCHEMA` (`raw`, `marts`) and `CREATE VOLUME` (`raw.ccai_loader_stage`), each `IF NOT EXISTS`, through the SQL statements API. When a step is denied, it prints the exact `GRANT` an admin must run (and, for the catalog, the catalogs you can already use) instead of failing midway.
4. Stores `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` and `DATABRICKS_CATALOG` in the 1Password item `databricks-ccai`, vault `CMW`, through a mode-600 temp template. Values are never printed. `--skip-1password` skips this step.

Rerunning is safe: every step is find-or-create.

**Free Edition limits that matter here**, from [Databricks' limitations page](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations), checked 2026-10-03:

- One SQL warehouse, 2X-Small only. When creating `ccai-sql` hits that limit (`RESOURCE_EXHAUSTED`) and exactly one warehouse exists (on Free Edition, `Serverless Starter Warehouse`), the script uses it and says so. Otherwise pass `--warehouse-name '<its name>'` (spaces are fine) or `--warehouse-id <id>`, which skips the name lookup.
- Serverless compute only. This module's all-purpose cluster and its `http_path` output do not exist there; use the warehouse's HTTP path, which the script records.
- A daily compute quota. Past it, compute is shut down for the rest of the day, so a large `make load-databricks` may have to resume the next day.
- Outbound internet is limited to trusted domains. That does not matter here: the loaders upload from your machine.

Not verified, because no Free Edition workspace was reachable:
- whether `CREATE CATALOG` is allowed. If it is denied and exactly one usable catalog exists (not `system`, `samples` or `hive_metastore`; on Free Edition typically `workspace`), the script uses that one and says so. Otherwise it lists your catalogs and asks for `--catalog`. An explicit `--catalog` or `--warehouse-name` is never swapped;
- whether personal access tokens and the SQL statements API are enabled by default.

**Fallback: by hand.** Start the cluster from the workspace UI or CLI when needed, or create a SQL warehouse and copy its HTTP path. Create a personal access token under User settings > Developer, then in a SQL editor run:

```sql
CREATE CATALOG IF NOT EXISTS ccai;   -- or use an existing catalog
CREATE SCHEMA IF NOT EXISTS ccai.raw;
CREATE SCHEMA IF NOT EXISTS ccai.marts;
CREATE VOLUME IF NOT EXISTS ccai.raw.ccai_loader_stage;
```

Then export `DATABRICKS_HOST` (hostname only), `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` and `DATABRICKS_CATALOG`.
