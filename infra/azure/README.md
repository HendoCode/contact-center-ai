# infra/azure

Terraform for the Azure side of the stack, following [`docs/design/infra.md`](../../docs/design/infra.md). Snowflake stays on AWS; its objects live in the separate persistent root `envs/snowflake` (module `snowflake`).

```
bootstrap/        applied once, local state: state storage, subscription budget, Key Vault
envs/dev/         destroyed each session, azurerm backend: composes the modules below
envs/snowflake/   persistent, same backend with its own state key: composes modules/snowflake
modules/core        resource group, Log Analytics (0.5 GB/day cap), app identity
modules/data        Postgres Flexible 16 + pgvector, ADLS Gen2 (Lance, dbt artifacts)
modules/apps        ACR, Container Apps (mcp-server, agent-api) + Easy Auth via azapi
modules/compute-gpu A10 Spot VM, nightly shutdown, cloud-init variable (F4 TODO)
modules/databricks  Premium workspace + single-node all-purpose cluster, 10 min auto-terminate
modules/snowflake   XS warehouse (60 s auto-suspend), database, dbt role + key-pair service user, 5-credit monitor
```

Each root commits its `.terraform.lock.hcl` (the `uv.lock` counterpart). `backend.hcl` and `*.tfvars` are gitignored; copy the `.example` files. Everything that costs money is off by default (`enable_gpu`, `enable_databricks`, `enable_mcp_server`, `enable_agent_api`).

## Regions

`location` places the resource group, Log Analytics and the app identity (`modules/core`; none of them is capacity-sensitive). When a region runs out of capacity or quota for one module, move just that module with its override in `terraform.tfvars`. Each one is null by default and falls back to `location`, so leaving them unset changes nothing.

| Variable | Moves | Why it might need to |
|---|---|---|
| `apps_location` | ACR, Container Apps environment, container apps | `ManagedEnvironmentCapacityHeavyUsageError` when a region is busy |
| `data_location` | Postgres Flexible, ADLS | Postgres Flexible is restricted per subscription and region (this one allows only centralus), so this usually stays centralus |
| `databricks_location` | workspace, its managed resource group, cluster VMs | regional VM-family vCPU quota (`Standard_DS3_v2`) |
| `gpu_location` | A10 Spot VM and its network | Spot vCPU quota is per region ("Total Regional Spot vCPUs", default 20) |

Cross-region effects, from the current config and Microsoft's docs:

- Container Apps and Log Analytics: the environment ships logs to the workspace by ID and key, and Microsoft documents no same-region rule for Container Apps, so a workspace in another region is expected to work, but that is not verified here. Cross-region log traffic may add bandwidth charges.
- Container Apps and Postgres: the `allow-azure-services` firewall rule (0.0.0.0) admits Azure IPs from any region, so apps in another region still connect. Every query pays the inter-region round trip, and the traffic is billed as inter-region egress. The `operator_ip` rule is unaffected.
- ACR stays beside the Container Apps environment (both follow `apps_location`), so image pulls stay in-region.
- Databricks: Azure creates the managed resource group's resources (cluster VMs, storage, network) in the workspace's region, so `databricks_location` decides where the cluster's vCPU quota is drawn.
- GPU: the VM, VNet, NIC and public IP all follow `gpu_location`. Its Spot quota is separate from regular vCPU quota.

## Checks (what CI runs, no Azure account needed)

```bash
cd infra/azure
terraform fmt -check -recursive
for root in bootstrap envs/dev envs/snowflake; do
  (cd "$root" && terraform init -backend=false -input=false && terraform validate)
done
tflint --init --config="$PWD/.tflint.hcl"
tflint --recursive --config="$PWD/.tflint.hcl"
```

`terraform plan` cannot run offline: the azurerm provider authenticates when it is configured, before it reads any resource. Plan needs `az login`.

## Stephen runs

Agents never run these (`docs/AGENT_HANDOFF.md` §3).

```bash
# once: see bootstrap/README.md
cd infra/azure/bootstrap && terraform init && terraform apply

# each session
cd infra/azure/envs/dev
cp terraform.tfvars.example terraform.tfvars   # fill in
terraform init -backend-config=backend.hcl
terraform plan
terraform apply

# end of session
terraform destroy
```

Never `destroy` `bootstrap`. `envs/snowflake` is persistent too and not part of the session teardown; its steps, including the dbt key-pair generation, are in [`envs/snowflake/README.md`](envs/snowflake/README.md).
