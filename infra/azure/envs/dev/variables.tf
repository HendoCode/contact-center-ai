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
  description = "Azure region (docs/design/infra.md: East US 2). The resource group, Log Analytics and the app identity stay here; each *_location below defaults to it."
  type        = string
  default     = "eastus2"
}

# Per-module region overrides for regional capacity or quota errors. Null uses location,
# so leaving them unset changes nothing. Cross-region notes: infra/azure/README.md.

variable "apps_location" {
  description = "Region for ACR, the Container Apps environment and its apps. Container Apps environments can fail with ManagedEnvironmentCapacityHeavyUsageError in a busy region. Null uses location."
  type        = string
  default     = null
}

variable "data_location" {
  description = "Region for Postgres Flexible and the ADLS account. Postgres Flexible is offered per region and some subscriptions are restricted to one (this one: centralus), so this usually stays there. Null uses location."
  type        = string
  default     = null
}

variable "databricks_location" {
  description = "Region for the Databricks workspace, its managed resource group and cluster VMs. Cluster VM vCPU quota is per region and VM family. Null uses location."
  type        = string
  default     = null
}

variable "gpu_location" {
  description = "Region for the GPU VM. GPU Spot vCPU quota is per region (Central US is the Spot-quota fallback). Null uses location."
  type        = string
  default     = null
}

variable "operator_ip" {
  description = "Your public IPv4 address, for the Postgres firewall and GPU SSH. Required when enable_gpu is true."
  type        = string
  default     = null

  validation {
    condition     = var.operator_ip == null || can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", coalesce(var.operator_ip, "x")))
    error_message = "operator_ip must be a single IPv4 address, for example 203.0.113.10."
  }

  validation {
    condition     = !var.enable_gpu || var.operator_ip != null
    error_message = "operator_ip is required when enable_gpu is true (SSH is allowed only from it)."
  }
}

variable "operator_object_id" {
  description = "Your Entra object ID; gets blob access to the ADLS account. Null skips it."
  type        = string
  default     = null
}

# ── Feature gates (cost guardrails) ───────────────────────────────────────────

variable "enable_gpu" {
  description = "Create the A10 Spot VM. Off by default; needs Spot vCPU quota."
  type        = bool
  default     = false
}

variable "enable_databricks" {
  description = "Create the Databricks workspace and cluster. Off by default."
  type        = bool
  default     = false
}

variable "enable_mcp_server" {
  description = "Create the mcp-server container app (streamable HTTP behind Easy Auth)."
  type        = bool
  default     = false
}

variable "enable_agent_api" {
  description = "Create the agent-api container app. Wait for L4 (agent HTTP endpoint)."
  type        = bool
  default     = false
}

# ── Apps ──────────────────────────────────────────────────────────────────────

variable "entra_client_id" {
  description = "Client ID of the Entra app registration Easy Auth validates tokens for (Stephen registers it)."
  type        = string
}

variable "key_vault_secrets" {
  description = "Env var name => versionless Key Vault secret URI, resolved by the apps at runtime. Set values with `az keyvault secret set`."
  type        = map(string)
  default     = {}
}

# ── GPU ───────────────────────────────────────────────────────────────────────

variable "admin_ssh_public_key" {
  description = "SSH public key for the GPU VM. Required when enable_gpu is true."
  type        = string
  default     = null

  validation {
    condition     = !var.enable_gpu || var.admin_ssh_public_key != null
    error_message = "admin_ssh_public_key is required when enable_gpu is true."
  }
}

variable "gpu_cloud_init" {
  description = "cloud-config for the GPU VM. Null uses the module's placeholder; replace with F4's cloud-init when it lands."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
