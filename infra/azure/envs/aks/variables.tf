variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.subscription_id))
    error_message = "subscription_id must be a GUID."
  }
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$", var.tenant_id))
    error_message = "tenant_id must be a GUID."
  }
}

variable "key_vault_id" {
  description = "Resource ID of the bootstrap Key Vault (bootstrap output key_vault_id)."
  type        = string
}

variable "session_expires" {
  description = "Date (YYYY-MM-DD) by which this session's resources should be destroyed. Tagged on everything."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.session_expires))
    error_message = "session_expires must be formatted YYYY-MM-DD."
  }
}

variable "location" {
  description = "Azure region for the resource group, Log Analytics and the app identity; aks_location and data_location default to it."
  type        = string
  default     = "eastus2"
}

variable "aks_location" {
  description = "Region for the AKS cluster and its registry, when location lacks VM quota for the node pool. Null uses location."
  type        = string
  default     = null
}

variable "data_location" {
  description = "Region for Postgres Flexible and the ADLS account (see envs/dev: some subscriptions allow Postgres Flexible in one region only). Null uses location."
  type        = string
  default     = null
}

variable "operator_ip" {
  description = "Your public IPv4 address, for the Postgres firewall. Null skips the rule."
  type        = string
  default     = null

  validation {
    condition     = var.operator_ip == null || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", coalesce(var.operator_ip, "x")))
    error_message = "operator_ip must be a single IPv4 address, for example 203.0.113.10."
  }
}

variable "operator_object_id" {
  description = "Your Entra object ID: blob access to ADLS and cluster admin on AKS. Null skips both."
  type        = string
  default     = null
}

# ── Cluster ───────────────────────────────────────────────────────────────────

variable "kubernetes_version" {
  description = "Kubernetes minor version for AKS, for example \"1.35\". Null takes the region's default."
  type        = string
  default     = null
}

variable "node_vm_size" {
  description = "VM size of the node pool."
  type        = string
  default     = "Standard_D4s_v5"
}

variable "node_min_count" {
  description = "Autoscaler floor for the node pool."
  type        = number
  default     = 1
}

variable "node_max_count" {
  description = "Autoscaler ceiling for the node pool (cost guardrail)."
  type        = number
  default     = 2
}

variable "workload_namespace" {
  description = "Namespace of the Helm release; the federated credential names it."
  type        = string
  default     = "ccai"
}

variable "workload_service_account" {
  description = "Service account of the Helm release (serviceAccount.name in infra/k8s/values-aks.yaml)."
  type        = string
  default     = "ccai"
}

variable "enable_app_routing_gateway" {
  description = "Turn on the application routing add-on's Gateway API implementation (GatewayClass approuting-istio) that values-aks.yaml uses."
  type        = bool
  default     = true
}

variable "enable_container_insights" {
  description = "Send container logs and metrics to the env's Log Analytics workspace (billed per GB; the workspace keeps its 0.5 GB/day cap)."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
