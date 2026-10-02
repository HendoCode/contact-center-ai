# snowflake

An XS warehouse (60-second auto-suspend), a database, a dbt role, and a dbt `snowflake_service_user` that signs in with an RSA key pair. A 5-credit monthly resource monitor suspends the warehouse at 100%. Plan only until Stephen applies.

Provider: `snowflakedb/snowflake` `~> 2.21` (2.21.0, checked against its v2.21.0 docs on 2026-10-02; the newest 2.x on the registry, so no bump was needed). Every resource used is in the provider's "Stable" category, so no `preview_features_enabled`.

The module takes three provider aliases from the root (`configuration_aliases`), one per Snowflake role:

| Alias | Creates | Why this role |
|---|---|---|
| `accountadmin` | resource monitor, warehouse | Monitors are ACCOUNTADMIN-only, and so is assigning a warehouse to one ([Snowflake docs](https://docs.snowflake.com/en/user-guide/security-access-control-privileges)). The provider sets `resource_monitor` on the warehouse resource, so the warehouse comes from this alias too and is owned by ACCOUNTADMIN. |
| `sysadmin` | database | Object creation role. |
| `securityadmin` | role, user, all grants | Holds `MANAGE GRANTS` and `CREATE USER`/`CREATE ROLE`. |

## What the dbt role can do

| Grant | On | Why |
|---|---|---|
| `USAGE` | the warehouse | Run queries. `auto_resume = true`, so it needs no `OPERATE`. |
| `USAGE`, `CREATE SCHEMA` | the database | dbt creates its target schema (`MARTS`) and its test-audit schema; S2's loaders create their own raw schema. The role then owns what it creates and can create tables, views and stages there. |
| the role itself, to `SYSADMIN` | account role hierarchy | Standard practice, so SYSADMIN can see and manage what dbt builds. |

Nothing else: no grants on the `PUBLIC` schema, no account-level privileges, no ownership of the database or warehouse, and the user has no password. The resource monitor and warehouse stay owned by ACCOUNTADMIN, so the dbt role cannot resize the warehouse or loosen the monitor.

## Inputs worth knowing

`dbt_rsa_public_key` is the one required input: the user's public key as one line of base64, without the `BEGIN`/`END PUBLIC KEY` lines (validated). Terraform never sees the private key. `auto_suspend_seconds` rejects values below 60: Snowflake bills a 60-second minimum on each resume, and `0` means never suspend.

## Known limits

- The resource monitor is attached to the warehouse, not to the account. The provider cannot attach to the account (use `snowflake_execute` if ever wanted), so a second warehouse created outside Terraform is not capped.
- `monitor_start_timestamp = "IMMEDIATELY"` is the provider's special value; it is unverified against a live account here, so check the first plan for a diff on it.
- No network policy and no notification users on the monitor; both are follow-ups.
