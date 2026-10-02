# databricks

Premium workspace plus one single-node **all-purpose** cluster that auto-terminates after 10 minutes. All-purpose rather than the hand-off's job cluster because dbt, `mf` and S2's connector need an interactive `http_path` (output). No SQL warehouse.

The calling root configures the `databricks` provider from the `workspace_id` output (see `envs/dev/providers.tf`). The env gates the module behind `enable_databricks` (default `false`).

## Stephen runs

`terraform apply` (workspace and cluster) after `az login`, then start the cluster from the workspace UI or CLI when needed. Create any token dbt needs yourself; none is created here.
