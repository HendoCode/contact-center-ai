variable "resource_group_name" {
  description = "Resource group for the data resources."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "ccai"
}

variable "environment" {
  description = "Environment label used in resource names."
  type        = string
  default     = "dev"
}

variable "operator_ip" {
  description = "Public IPv4 address of the operator, allowed through the Postgres firewall. Null opens only the Azure-services rule."
  type        = string
  default     = null

  validation {
    condition     = var.operator_ip == null || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", coalesce(var.operator_ip, "x")))
    error_message = "operator_ip must be a single IPv4 address, for example 203.0.113.10."
  }
}

variable "app_identity_principal_id" {
  description = "Principal ID of the app identity; gets Storage Blob Data Contributor on the ADLS account."
  type        = string
}

variable "operator_object_id" {
  description = "Entra object ID of the operator; gets Storage Blob Data Contributor on the ADLS account (for `az` and Lance access). Null skips it."
  type        = string
  default     = null
}

variable "postgres_sku_name" {
  description = "Postgres Flexible Server SKU. B1ms burstable, no HA, per docs/design/infra.md."
  type        = string
  default     = "B_Standard_B1ms"
}

variable "postgres_storage_mb" {
  description = "Postgres storage in MB."
  type        = number
  default     = 32768
}

variable "postgres_database_name" {
  description = "Application database created on the server (matches DATABASE_URL in .env.example)."
  type        = string
  default     = "contactcenter"
}

variable "postgres_admin_login" {
  description = "Postgres administrator login."
  type        = string
  default     = "pgadmin"
}

variable "adls_filesystems" {
  description = "ADLS Gen2 filesystems (containers): Lance datasets and dbt artifacts."
  type        = set(string)
  default     = ["lance", "dbt-artifacts"]
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
