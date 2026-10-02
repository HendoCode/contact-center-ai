# bootstrap

Applied once, with local state (gitignored). Holds what must outlive `terraform destroy` of `envs/dev`:

- state storage account and `tfstate` container (Entra auth, shared keys off, blob versioning)
- subscription budget: alerts at 50% and 90% actual, 100% forecast
- Key Vault (RBAC mode). API keys go in with `az keyvault secret set`, never through Terraform, so they stay out of state.

Design: [`docs/design/infra.md`](../../../docs/design/infra.md).

## Stephen runs

```bash
cd infra/azure/bootstrap
cp terraform.tfvars.example terraform.tfvars   # fill in; gitignored
terraform init
terraform plan
terraform apply

# state backend config for envs/dev (gitignored)
terraform output -raw backend_hcl_dev > ../envs/dev/backend.hcl

# secrets (names are what envs/dev's apps module expects)
az keyvault secret set --vault-name "$(terraform output -raw key_vault_name)" --name openai-api-key --value "<key>"
```

Check the budget alert email arrives after the first apply. Do not `destroy` this root.
