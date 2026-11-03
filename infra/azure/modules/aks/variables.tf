variable "resource_group_name" {
  description = "Resource group for the cluster and the registry."
  type        = string
}

variable "location" {
  description = "Azure region for the cluster and the registry."
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
  default     = "aks"
}

variable "tenant_id" {
  description = "Microsoft Entra tenant ID, for Entra-integrated cluster RBAC."
  type        = string
}

variable "kubernetes_version" {
  description = "Kubernetes minor version, for example \"1.35\". Null takes the region's default version."
  type        = string
  default     = null
}

variable "node_vm_size" {
  description = "VM size of the single system node pool. 4 vCPU / 16 GiB fits Ollama embeddings next to the server and the Jobs."
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

  validation {
    condition     = var.node_max_count >= var.node_min_count
    error_message = "node_max_count must be at least node_min_count."
  }
}

variable "os_disk_size_gb" {
  description = "Node OS disk size in GB; it holds the container images (the app image is about 2 GB)."
  type        = number
  default     = 64
}

variable "app_identity_id" {
  description = "Resource ID of the user-assigned app identity (modules/core); the chart's service account is federated with it."
  type        = string
}

variable "workload_namespace" {
  description = "Namespace the Helm release is installed into."
  type        = string
  default     = "ccai"
}

variable "workload_service_account" {
  description = "Service account the chart creates (serviceAccount.name in infra/k8s/values-aks.yaml)."
  type        = string
  default     = "ccai"
}

variable "operator_object_id" {
  description = "Entra object ID that gets Azure Kubernetes Service RBAC Cluster Admin on the cluster. Null skips it."
  type        = string
  default     = null
}

variable "enable_app_routing_gateway" {
  description = "Turn on the managed Gateway API CRDs and the application routing add-on's Gateway API implementation (GatewayClass approuting-istio)."
  type        = bool
  default     = true
}

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace for Container Insights. Null leaves monitoring off (each GB ingested is billed)."
  type        = string
  default     = null
}

variable "tags" {
  description = "Tags applied to every resource, including session_expires."
  type        = map(string)
  default     = {}
}
