# infra/azure

Terraform for the Azure side of the stack, following [`docs/design/infra.md`](../../docs/design/infra.md). Snowflake stays on AWS and its root (`envs/snowflake`) is a separate ticket (I-SF).

```
bootstrap/        applied once, local state: state storage, subscription budget, Key Vault
envs/dev/         destroyed each session, azurerm backend: composes the modules below
modules/core        resource group, Log Analytics (0.5 GB/day cap), app identity
modules/data        Postgres Flexible 16 + pgvector, ADLS Gen2 (Lance, dbt artifacts)
modules/apps        ACR, Container Apps (mcp-server, agent-api) + Easy Auth via azapi
modules/compute-gpu A10 Spot VM, nightly shutdown, cloud-init variable (F4 TODO)
modules/databricks  Premium workspace + single-node all-purpose cluster, 10 min auto-terminate
```

Each root commits its `.terraform.lock.hcl` (the `uv.lock` counterpart). `backend.hcl` and `*.tfvars` are gitignored; copy the `.example` files. Everything that costs money is off by default (`enable_gpu`, `enable_databricks`, `enable_mcp_server`, `enable_agent_api`).

## Checks (what CI runs, no Azure account needed)

```bash
cd infra/azure
terraform fmt -check -recursive
for root in bootstrap envs/dev; do
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

Never `destroy` `bootstrap`.
