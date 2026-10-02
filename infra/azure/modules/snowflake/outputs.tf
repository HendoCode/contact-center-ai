output "warehouse_name" {
  description = "Warehouse name (SNOWFLAKE_WAREHOUSE)."
  value       = snowflake_warehouse.dbt.name
}

output "database_name" {
  description = "Database name (SNOWFLAKE_DATABASE)."
  value       = snowflake_database.this.name
}

output "role_name" {
  description = "dbt role name (SNOWFLAKE_ROLE)."
  value       = snowflake_account_role.dbt.name
}

output "user_name" {
  description = "dbt service user name (SNOWFLAKE_USER)."
  value       = snowflake_service_user.dbt.name
}

output "schema_name" {
  description = "Schema dbt builds into (SNOWFLAKE_SCHEMA); dbt creates it."
  value       = var.dbt_schema
}

output "resource_monitor_name" {
  description = "Monthly resource monitor attached to the warehouse."
  value       = snowflake_resource_monitor.monthly.name
}
