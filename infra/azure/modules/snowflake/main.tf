# Which role creates what (docs/design/infra.md):
#   ACCOUNTADMIN   resource monitor, and the warehouse that points at it. Snowflake lets
#                  only ACCOUNTADMIN assign a warehouse to a monitor, and the provider
#                  sets resource_monitor through the same connection that owns the warehouse.
#   SYSADMIN       database.
#   SECURITYADMIN  role, user and every grant (it holds MANAGE GRANTS and CREATE USER/ROLE).

locals {
  warehouse_name = "${var.name_prefix}_DBT_WH"
  database_name  = var.name_prefix
  role_name      = "${var.name_prefix}_DBT_ROLE"
  user_name      = "${var.name_prefix}_DBT"
  monitor_name   = "${var.name_prefix}_MONTHLY_MONITOR"
}

resource "snowflake_resource_monitor" "monthly" {
  provider = snowflake.accountadmin

  name         = local.monitor_name
  credit_quota = var.monthly_credit_quota

  frequency       = "MONTHLY"
  start_timestamp = var.monitor_start_timestamp

  # Lets running queries finish, then blocks new ones until the quota resets or is raised.
  suspend_trigger = 100
}

resource "snowflake_warehouse" "dbt" {
  provider = snowflake.accountadmin

  name           = local.warehouse_name
  warehouse_size = var.warehouse_size

  auto_suspend        = var.auto_suspend_seconds
  auto_resume         = "true"
  initially_suspended = true # creating it must not start billing

  resource_monitor = snowflake_resource_monitor.monthly.fully_qualified_name
  comment          = "dbt and loader warehouse. Managed by Terraform (envs/snowflake)."
}

resource "snowflake_database" "this" {
  provider = snowflake.sysadmin

  name    = local.database_name
  comment = "contact-center-ai marts. Managed by Terraform (envs/snowflake)."
}

resource "snowflake_account_role" "dbt" {
  provider = snowflake.securityadmin

  name    = local.role_name
  comment = "dbt and loader role. Managed by Terraform (envs/snowflake)."
}

# Least privilege for S1 (dbt build) and S2 (loaders); see README.md for the reasoning.
resource "snowflake_grant_privileges_to_account_role" "warehouse_usage" {
  provider = snowflake.securityadmin

  account_role_name = snowflake_account_role.dbt.name
  privileges        = ["USAGE"]

  on_account_object {
    object_type = "WAREHOUSE"
    object_name = snowflake_warehouse.dbt.name
  }
}

resource "snowflake_grant_privileges_to_account_role" "database" {
  provider = snowflake.securityadmin

  account_role_name = snowflake_account_role.dbt.name
  privileges        = ["USAGE", "CREATE SCHEMA"]

  on_account_object {
    object_type = "DATABASE"
    object_name = snowflake_database.this.name
  }
}

# Standard hierarchy: SYSADMIN can see and manage what the dbt role creates.
resource "snowflake_grant_account_role" "dbt_to_sysadmin" {
  provider = snowflake.securityadmin

  role_name        = snowflake_account_role.dbt.name
  parent_role_name = "SYSADMIN"
}

# Key-pair only: a service user has no password and cannot log in interactively.
resource "snowflake_service_user" "dbt" {
  provider = snowflake.securityadmin

  name    = local.user_name
  comment = "dbt and loader service user (key-pair auth). Managed by Terraform (envs/snowflake)."

  default_role      = snowflake_account_role.dbt.name
  default_warehouse = snowflake_warehouse.dbt.name
  default_namespace = "${snowflake_database.this.name}.${var.dbt_schema}"

  rsa_public_key = var.dbt_rsa_public_key
}

resource "snowflake_grant_account_role" "dbt_to_user" {
  provider = snowflake.securityadmin

  role_name = snowflake_account_role.dbt.name
  user_name = snowflake_service_user.dbt.name
}
