terraform {
  required_version = "~> 1.16"

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 2.21"

      # The calling root passes one provider per Snowflake role (see envs/snowflake/providers.tf).
      configuration_aliases = [
        snowflake.accountadmin,
        snowflake.sysadmin,
        snowflake.securityadmin,
      ]
    }
  }
}
