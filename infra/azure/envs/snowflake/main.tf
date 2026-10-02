module "snowflake" {
  source = "../../modules/snowflake"

  providers = {
    snowflake.accountadmin  = snowflake.accountadmin
    snowflake.sysadmin      = snowflake.sysadmin
    snowflake.securityadmin = snowflake.securityadmin
  }

  name_prefix          = var.name_prefix
  dbt_rsa_public_key   = var.dbt_rsa_public_key
  warehouse_size       = var.warehouse_size
  auto_suspend_seconds = var.auto_suspend_seconds
  monthly_credit_quota = var.monthly_credit_quota
}
