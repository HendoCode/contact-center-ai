# envs/snowflake

Persistent root for the Snowflake side of the stack: the dbt warehouse, database, role, service user and the monthly resource monitor ([`modules/snowflake`](../../modules/snowflake/README.md)). Separate from `envs/dev` so the dbt user and warehouse do not churn when `envs/dev` is destroyed each session. Same azurerm backend (Entra auth), separate state key `snowflake.tfstate`. Design: [`docs/design/infra.md`](../../../../docs/design/infra.md).

Plan only until Stephen runs it. Nothing here connects to Snowflake or Azure in CI: `init -backend=false` and `validate`.

## Stephen runs

**Prerequisites.** `bootstrap` applied and `envs/dev/backend.hcl` in place (`az login` done). A Snowflake user for Terraform (`admin_user`, default `TERRAFORM_ADMIN`) with the `ACCOUNTADMIN`, `SYSADMIN` and `SECURITYADMIN` roles granted; this root does not create it. `openssl`, `terraform` and, for the last step, the 1Password CLI `op` signed in.

### Primary path: `tools/bootstrap-snowflake.sh`

```bash
tools/bootstrap-snowflake.sh --dry-run                                    # every step, nothing changed
tools/bootstrap-snowflake.sh --org MYORG --account MYACCOUNT --admin-user TERRAFORM_ADMIN
```

It is idempotent: existing keys, `terraform.tfvars` and `backend.hcl` are reused, never overwritten (`--force-new-keys` replaces the keys). In order, it:

1. Takes the organization and account names, or prints `SELECT CURRENT_ORGANIZATION_NAME(), CURRENT_ACCOUNT_NAME();` to run in Snowsight and asks. These are the two halves of `org-account`, not the account locator.
2. Generates `~/.snowflake/ccai_tf_admin_key.p8` (Terraform admin) and `~/.snowflake/ccai_dbt_key.p8` (dbt user), PKCS#8, mode 600, with **no passphrase** by default. `--passphrase` encrypts both with one prompted passphrase, which is then stored in 1Password.
3. Prints the one-line `ALTER USER ... SET RSA_PUBLIC_KEY='...'` for the admin user to paste in Snowsight as `ACCOUNTADMIN`, plus `DESC USER` and the `RSA_PUBLIC_KEY_FP` it must show. The dbt user's key goes to Terraform, which creates that user.
4. Writes the gitignored `terraform.tfvars` (names only) and `backend.hcl` (from `../dev/backend.hcl`). The dbt public key (`TF_VAR_dbt_rsa_public_key`) and the admin private key (`SNOWFLAKE_PRIVATE_KEY`) are exported only inside the script.
5. Runs `terraform init` and `plan`, shows the plan summary, and applies only after an explicit `y` (or `--yes`).
6. Stores `SNOWFLAKE_ACCOUNT`, `_USER`, `_ROLE`, `_WAREHOUSE`, `_DATABASE`, `_PRIVATE_KEY_PEM` (dbt key) and `_PRIVATE_KEY_PASSPHRASE` (only with `--passphrase`) in the 1Password item `snowflake-ccai`, vault `CMW`. Values go to `op` through a mode-600 temp template, never argv, and are never printed. `--skip-1password` skips this step.

No network rule or network policy is needed for this setup. Do not create one: network rules are schema objects, and creating them in a personal database is a dead end.

The first plan should create 9 resources: monitor, warehouse, database, role, two privilege grants, the role to `SYSADMIN`, the service user, and the role to the user. Check the plan for a perpetual diff on the monitor's `start_timestamp`. The warehouse is created suspended and idle credits stop after 60 seconds.

### Fallback: by hand

**1. Key pairs** for the admin user and the dbt user. Unencrypted is the default; add a passphrase only if you will store it (then use `-v2 aes-256-cbc` in place of `-nocrypt` and set `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`).

```bash
mkdir -p ~/.snowflake && chmod 700 ~/.snowflake && cd ~/.snowflake
for k in ccai_tf_admin_key ccai_dbt_key; do
  openssl genrsa 2048 | openssl pkcs8 -topk8 -nocrypt -out $k.p8
  openssl rsa -in $k.p8 -pubout -out $k.pub
  chmod 600 $k.p8
done
# public key as one line without the BEGIN/END lines
echo "ALTER USER TERRAFORM_ADMIN SET RSA_PUBLIC_KEY='$(grep -v -- ----- ccai_tf_admin_key.pub | tr -d '\n')';"
export TF_VAR_dbt_rsa_public_key="$(grep -v -- ----- ccai_dbt_key.pub | tr -d '\n')"
```

Run the printed `ALTER USER` line in Snowsight as `ACCOUNTADMIN`, then `DESC USER TERRAFORM_ADMIN;` to confirm `RSA_PUBLIC_KEY_FP` is set.

**2. Point Terraform at the account.**

```bash
cd infra/azure/envs/snowflake
sed 's/dev.tfstate/snowflake.tfstate/' ../dev/backend.hcl > backend.hcl      # gitignored
cp terraform.tfvars.example terraform.tfvars                                 # gitignored; fill in
export SNOWFLAKE_PRIVATE_KEY="$(cat ~/.snowflake/ccai_tf_admin_key.p8)"     # read by the provider, never in state
```

**3. Init, plan, apply.**

```bash
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

**4. Point dbt at it.** `terraform output dbt_env` prints the `SNOWFLAKE_*` values for `olap/dbt/profiles.yml`. Add `SNOWFLAKE_PRIVATE_KEY_PATH=~/.snowflake/ccai_dbt_key.p8` yourself (and the passphrase only if you encrypted the key).

Do not `destroy` this root as routine; it is not part of the end-of-session teardown.
