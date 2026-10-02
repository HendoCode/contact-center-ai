variable "organization_name" {
  description = "Snowflake organization name: the part before the dash in the account identifier org-account."
  type        = string
}

variable "account_name" {
  description = "Snowflake account name: the part after the dash in org-account."
  type        = string
}

variable "admin_user" {
  description = "Existing Snowflake user that Terraform signs in as, with a key pair and the ACCOUNTADMIN, SYSADMIN and SECURITYADMIN roles. Not created here."
  type        = string
}

variable "dbt_rsa_public_key" {
  description = "dbt service user's RSA public key: one line of base64, no BEGIN/END lines. Public only."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for Snowflake object names (upper case)."
  type        = string
  default     = "CCAI"
}

variable "warehouse_size" {
  description = "Warehouse size."
  type        = string
  default     = "XSMALL"
}

variable "auto_suspend_seconds" {
  description = "Idle seconds before the warehouse suspends."
  type        = number
  default     = 60
}

variable "monthly_credit_quota" {
  description = "Monthly credits before the resource monitor suspends the warehouse."
  type        = number
  default     = 5
}
