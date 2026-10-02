# Azure infrastructure (Terraform)

D0.3, 2026-10-02. Feeds I1, I-SF, F4.

Checked 2026-10-02 against:
- Terraform 1.16.4
- Registry: azurerm 4.81.0 / 5.8.0, azapi 2.13.0, random 3.9.1, databricks 1.135.0, snowflakedb/snowflake 2.21.0
- tflint-ruleset-azurerm 0.32.0
- The azurerm 5.0 upgrade guide, Azure Retail Prices, and the N-series driver page

## Roots

**Three roots, split by lifecycle:**
- `infra/azure/bootstrap` (applied once; local, gitignored state): state storage, the subscription budget, and Key Vault. The budget must outlive `destroy`. API keys set with `az keyvault secret set` must survive across sessions and never enter state.
- `infra/azure/envs/dev` (destroyed each session, §8; azurerm backend with Entra auth): `core`, `data`, `apps`, `compute-gpu`, `databricks`.
- `infra/azure/envs/snowflake` (persistent; same backend, separate key): `snowflake`. The dbt user should not churn every session.

Each root commits `.terraform.lock.hcl` (the `uv.lock` counterpart). `backend.hcl` and `*.tfvars` are gitignored, with `.example` copies. Rejected: HCP Terraform (one more account) and a single root (`destroy` would delete the budget).

## Modules

- **`core`:** resource group, Log Analytics with a 0.5 GB/day cap, and an app identity with `Key Vault Secrets User`.
- **`data`:** Postgres Flexible 16 on B1ms with `azure.extensions = VECTOR`, firewalled to Azure and the operator IP. ADLS Gen2 for Lance and dbt artifacts.
- **`apps`:** ACR Basic, plus Container Apps `mcp-server` and `agent-api` at 0–1 replicas. azurerm (4.81 and `main`) has no Container Apps auth-config resource, so Easy Auth is an `azapi_resource` of type `Microsoft.App/containerApps/authConfigs@2026-01-01` (`Return401`, Entra v2 issuer). The apps module pins `2026-01-01`, the newest version in azapi 2.13's embedded schema; `2026-07-01` is not in it. Stephen registers the Entra app. The MCP server is stdio-only today, so `count` gates `mcp-server` on M1 and `agent-api` on L4 (`agent-graph.md`).
- **`compute-gpu`:** `Standard_NV36ads_A10_v5` Spot (one A10, 24 GB). Secure Boot and vTPM are off, as the GRID driver requires. SSH is allowed only from the operator IP, and vLLM is reached over an SSH tunnel. T4 is rejected: 16 GB, no bf16.
- **`databricks`:** Premium workspace with a single-node all-purpose cluster that auto-terminates after 10 minutes. It is all-purpose rather than the hand-off's job cluster because dbt, `mf`, and S2's connector need an interactive `http_path`.
- **`snowflake`:** XS warehouse, database, role, and `snowflake_service_user` with `rsa_public_key`.

Private networking is a follow-up.

## Providers

```hcl
required_version = "~> 1.16"
azurerm    = { source = "hashicorp/azurerm", version = "~> 4.81" }   # < 5.0
azapi      = { source = "azure/azapi", version = "~> 2.13" }
random     = { source = "hashicorp/random", version = "~> 3.9" }
databricks = { source = "databricks/databricks", version = "~> 1.135" }
snowflake  = { source = "snowflakedb/snowflake", version = "~> 2.21" }
```

**azurerm stays on 4.x, per the hand-off. Stephen to confirm.** 4.81.0 (2026-07-14) is the last 4.x release. 5.0.0 shipped on 2026-07-28, and 5.8.0 is current. The code is written 5.x-clean, so the upgrade is a constraint bump:
- No properties deprecated in 4.81.
- `resource_provider_registrations = "none"` plus an explicit `resource_providers_to_register` list.
- An explicit `logs_destination`.
- `rbac_authorization_enabled` on Key Vault.

Snowflake authenticates with `SNOWFLAKE_JWT` through three role aliases:
- `ACCOUNTADMIN` for the resource monitor, which its docs require.
- `SYSADMIN` for the warehouse and database.
- `SECURITYADMIN` for users and grants.

Terraform only sees the public key.

## Regions

**Azure: East US 2**, with a `gpu_location` override for Central US. NV36ads A10 v5 Spot costs the same $0.59136/hour in both (on demand is $3.20), and East US 2 is the hand-off's first option.

**Quota:** Spot uses the regional Spot vCPU quota. Stephen requests 36 vCPUs in East US 2, with Central US as the fallback.

**Snowflake** stays in its existing AWS region. Terraform never creates or moves the account, and cross-cloud traffic is a few MB.

## Cost guardrails

- **Budget:** $50/month at subscription scope, since Databricks' managed resource group sits outside `envs/dev`. Alerts at 50% and 90% actual and 100% forecast. Budgets only notify; the items below stop spend.
- **Session:** destroy `envs/dev` every session; tag resources `session_expires`.
- **GPU:**
  - `enable_gpu = false` by default.
  - Spot price capped at $1.00/hour, evicted to `Deallocate`.
  - Nightly shutdown.
  - Idle deallocation through the VM's identity after 30 minutes. A guest `shutdown` alone keeps billing.
- **Apps and data:** scale to zero, ACR Basic, a 0.5 GB/day log cap, and B1ms Postgres without HA.
- **Databricks:** `enable_databricks = false` by default; auto-terminate after 10 minutes; no SQL warehouse.
- **Snowflake:** 60-second auto-suspend, with a 5-credit monthly resource monitor that suspends at 100%.
- **Process:** Stephen runs every `apply` and `destroy` (§3). CI runs `fmt -check`, `validate` with `-backend=false`, and `tflint`.

## Tickets

I1 splits into one PR per root or module.

- **I1a · `bootstrap`** (**new** split). **Stephen runs:** `apply` and the vault secrets.
- **I1b · `core`**, then **I1c · `data`**.
- **I1d · `apps`.** Containers wait on M1 and L4. **Stephen runs:** the Entra app registration and `az acr build`.
- **I1e · `compute-gpu`.** Depends on F4's cloud-init. **Stephen runs:** the quota request, plus VM start and stop.
- **I1f · `databricks`.**
- **I1g · `envs/dev`.** Composition, `.gitignore`, and a Terraform CI job. Deprecate `infra/terraform/` once `apps` lands.
- **I-SF** (refined). The `envs/snowflake` root, role aliases, and the resource monitor. Plan only.
- **I-UP · azurerm 5.x** (**new**, unscheduled). Upgrade when Stephen approves.
