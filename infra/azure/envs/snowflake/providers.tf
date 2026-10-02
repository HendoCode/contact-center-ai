# Three connections to one account, one per role, all key-pair (SNOWFLAKE_JWT).
# The admin user's private key is not a Terraform variable: the provider reads it from
# SNOWFLAKE_PRIVATE_KEY (and SNOWFLAKE_PRIVATE_KEY_PASSPHRASE if encrypted) at run time,
# so it is never in a tfvars file or in state. The admin user needs all three roles granted.
#
# Each alias is used by exactly the resources that need that role; see modules/snowflake.

provider "snowflake" {
  alias = "accountadmin"

  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.admin_user
  authenticator     = "SNOWFLAKE_JWT"
  role              = "ACCOUNTADMIN"
}

provider "snowflake" {
  alias = "sysadmin"

  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.admin_user
  authenticator     = "SNOWFLAKE_JWT"
  role              = "SYSADMIN"
}

provider "snowflake" {
  alias = "securityadmin"

  organization_name = var.organization_name
  account_name      = var.account_name
  user              = var.admin_user
  authenticator     = "SNOWFLAKE_JWT"
  role              = "SECURITYADMIN"
}
