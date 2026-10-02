variable "resource_group_name" {
  description = "Resource group for the workspace."
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

variable "node_type_id" {
  description = "VM size for the single-node cluster."
  type        = string
  default     = "Standard_DS3_v2"
}

variable "autotermination_minutes" {
  description = "Idle minutes before the cluster terminates (cost guardrail)."
  type        = number
  default     = 10
}

variable "tags" {
  description = "Tags applied to the workspace."
  type        = map(string)
  default     = {}
}
