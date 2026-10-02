# Everything olap/dbt/profiles.yml (target: snowflake) needs besides the private key.
# Values are names, not secrets. SNOWFLAKE_PRIVATE_KEY_PATH is the dbt user's key file.
output "dbt_env" {
  description = "Env vars for `dbt build --target snowflake` (see .env.example)."
  value = {
    SNOWFLAKE_ACCOUNT   = "${var.organization_name}-${var.account_name}"
    SNOWFLAKE_USER      = module.snowflake.user_name
    SNOWFLAKE_ROLE      = module.snowflake.role_name
    SNOWFLAKE_WAREHOUSE = module.snowflake.warehouse_name
    SNOWFLAKE_DATABASE  = module.snowflake.database_name
    SNOWFLAKE_SCHEMA    = module.snowflake.schema_name
  }
}
