variable "name_prefix" {
  description = "Prefix for object names. Snowflake identifiers here are upper case so dbt can reference them unquoted."
  type        = string
  default     = "CCAI"

  validation {
    condition     = can(regex("^[A-Z][A-Z0-9_]*$", var.name_prefix))
    error_message = "name_prefix must be upper case letters, digits and underscores, starting with a letter."
  }
}

variable "dbt_rsa_public_key" {
  description = "The dbt user's RSA public key: one line of base64, without the BEGIN/END PUBLIC KEY lines. Public only; the private key never reaches Terraform."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9+/]+={0,2}$", var.dbt_rsa_public_key))
    error_message = "dbt_rsa_public_key must be a single line of base64 with no PEM header, footer or whitespace."
  }
}

variable "warehouse_size" {
  description = "Warehouse size. XSMALL is the cost guardrail; the dbt build and parity queries fit it."
  type        = string
  default     = "XSMALL"
}

variable "auto_suspend_seconds" {
  description = "Idle seconds before the warehouse suspends (cost guardrail). Snowflake bills a 60-second minimum on every resume."
  type        = number
  default     = 60

  validation {
    condition     = var.auto_suspend_seconds >= 60
    error_message = "auto_suspend_seconds below 60 gains nothing: Snowflake bills a 60-second minimum per resume, and 0 means never suspend."
  }
}

variable "monthly_credit_quota" {
  description = "Credits per month the resource monitor allows before it suspends the warehouse (cost guardrail)."
  type        = number
  default     = 5
}

variable "monitor_start_timestamp" {
  description = "When the monitor starts counting. IMMEDIATELY means the apply time; a monthly frequency requires a start."
  type        = string
  default     = "IMMEDIATELY"
}

variable "dbt_schema" {
  description = "Schema dbt builds into. Only used for the user's default namespace; dbt creates the schema itself."
  type        = string
  default     = "MARTS"
}
