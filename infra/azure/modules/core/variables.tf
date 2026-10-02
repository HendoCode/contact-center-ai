variable "location" {
  description = "Azure region for the resource group and its resources."
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

variable "key_vault_id" {
  description = "Resource ID of the bootstrap Key Vault. The app identity gets Key Vault Secrets User on it."
  type        = string
}

variable "log_analytics_daily_quota_gb" {
  description = "Daily ingestion cap for Log Analytics, in GB (cost guardrail)."
  type        = number
  default     = 0.5
}

variable "log_analytics_retention_days" {
  description = "Log Analytics retention in days (30 is the free tier floor)."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Tags applied to every resource, including session_expires."
  type        = map(string)
  default     = {}
}
