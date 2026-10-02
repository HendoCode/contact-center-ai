# envs/snowflake

Persistent root for the Snowflake side of the stack: the dbt warehouse, database, role, service user and the monthly resource monitor ([`modules/snowflake`](../../modules/snowflake/README.md)). Separate from `envs/dev` so the dbt user and warehouse do not churn when `envs/dev` is destroyed each session. Same azurerm backend (Entra auth), separate state key `snowflake.tfstate`. Design: [`docs/design/infra.md`](../../../../docs/design/infra.md).

Plan only until Stephen runs it. Nothing here connects to Snowflake or Azure in CI: `init -backend=false` and `validate`.

## Stephen runs

**Prerequisites.** `bootstrap` applied and `envs/dev/backend.hcl` in place. A Snowflake user for Terraform (`admin_user`) with key-pair auth and the `ACCOUNTADMIN`, `SYSADMIN` and `SECURITYADMIN` roles granted; this root does not create it.

**1. Generate the dbt user's key pair.** The private key stays on your machine; only the public key goes to Terraform.

```bash
mkdir -p ~/.snowflake && cd ~/.snowflake
# encrypted PKCS#8 private key (prompts for a passphrase); add -nocrypt instead to skip it
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -v2 aes-256-cbc -out ccai_dbt_key.p8
openssl rsa -in ccai_dbt_key.p8 -pubout -out ccai_dbt_key.pub
chmod 600 ccai_dbt_key.p8

# public key as one line without the BEGIN/END lines
export TF_VAR_dbt_rsa_public_key="$(grep -v 'PUBLIC KEY' ccai_dbt_key.pub | tr -d '\n')"
```

**2. Point Terraform at the account and sign in as the admin user.**

```bash
cd infra/azure/envs/snowflake
sed 's/dev.tfstate/snowflake.tfstate/' ../dev/backend.hcl > backend.hcl      # gitignored
cp terraform.tfvars.example terraform.tfvars                                 # gitignored; fill in
# admin user's PKCS#8 private key, read by the provider (never written to state)
export SNOWFLAKE_PRIVATE_KEY="$(cat /path/to/admin_key.p8)"
export SNOWFLAKE_PRIVATE_KEY_PASSPHRASE="..."    # only if that key is encrypted
az login
```

**3. Init, plan, apply.**

```bash
terraform init -backend-config=backend.hcl
terraform plan
terraform apply
```

The first plan should create 9 resources: monitor, warehouse, database, role, two privilege grants, the role to `SYSADMIN`, the service user, and the role to the user. Check the plan for a perpetual diff on the monitor's `start_timestamp`. The warehouse is created suspended and idle credits stop after 60 seconds.

**4. Point dbt at it.** `terraform output dbt_env` prints the `SNOWFLAKE_*` values for `olap/dbt/profiles.yml`. Add `SNOWFLAKE_PRIVATE_KEY_PATH=~/.snowflake/ccai_dbt_key.p8` (and the passphrase if encrypted) yourself.

Do not `destroy` this root as routine; it is not part of the end-of-session teardown.
