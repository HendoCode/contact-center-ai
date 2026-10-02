variable "subscription_id" {
  description = "Azure subscription ID. The budget is scoped to the whole subscription."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a GUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID that owns the subscription."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "tenant_id must be a GUID."
  }
}

variable "operator_object_id" {
  description = "Entra object ID of the person who runs apply. Gets blob access to the state container and Key Vault Secrets Officer."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.operator_object_id))
    error_message = "operator_object_id must be a GUID."
  }
}

variable "location" {
  description = "Azure region for the bootstrap resources."
  type        = string
  default     = "eastus2"
}

variable "budget_amount" {
  description = "Monthly subscription budget in USD. Budgets only notify; they do not stop spend."
  type        = number
  default     = 50
}

variable "budget_start_date" {
  description = "First day of a month, RFC 3339 (Azure requires the budget period to start on the 1st)."
  type        = string
  default     = "2026-10-01T00:00:00Z"

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-01T00:00:00Z$", var.budget_start_date))
    error_message = "budget_start_date must be the first of a month, formatted YYYY-MM-01T00:00:00Z."
  }
}

variable "budget_contact_emails" {
  description = "Addresses that receive the budget alerts."
  type        = list(string)

  validation {
    condition     = length(var.budget_contact_emails) > 0
    error_message = "Set at least one budget contact email; an unwatched budget is not a guardrail."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
