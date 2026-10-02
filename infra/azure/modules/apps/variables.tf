variable "resource_group_name" {
  description = "Resource group for the apps."
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

variable "log_analytics_workspace_id" {
  description = "Log Analytics workspace that receives Container Apps logs."
  type        = string
}

variable "app_identity_id" {
  description = "Resource ID of the user-assigned identity attached to every container app."
  type        = string
}

variable "app_identity_principal_id" {
  description = "Principal ID of that identity; gets AcrPull on the registry."
  type        = string
}

variable "app_identity_client_id" {
  description = "Client ID of that identity, exposed to the containers as AZURE_CLIENT_ID."
  type        = string
}

variable "entra_tenant_id" {
  description = "Entra tenant ID that issues the tokens Easy Auth accepts."
  type        = string
}

variable "entra_client_id" {
  description = "Client ID of the Entra app registration Stephen creates for the apps (Easy Auth audience)."
  type        = string
}

variable "enable_mcp_server" {
  description = "Create the mcp-server container app. Off until M1 gives the MCP server a streamable-HTTP transport (it is stdio-only today)."
  type        = bool
  default     = false
}

variable "enable_agent_api" {
  description = "Create the agent-api container app. Off until L4 ships the agent HTTP endpoint."
  type        = bool
  default     = false
}

variable "mcp_server_image" {
  description = "Full image reference for mcp-server. Null uses <registry>/mcp-server:latest."
  type        = string
  default     = null
}

variable "agent_api_image" {
  description = "Full image reference for agent-api. Null uses <registry>/agent-api:latest."
  type        = string
  default     = null
}

variable "mcp_server_port" {
  description = "Container port for mcp-server (MCP_SERVER_PORT)."
  type        = number
  default     = 8000
}

variable "agent_api_port" {
  description = "Container port for agent-api."
  type        = number
  default     = 8001
}

variable "database_url" {
  description = "DATABASE_URL injected into both apps as a Container Apps secret."
  type        = string
  sensitive   = true
}

variable "key_vault_secrets" {
  description = "Env var name => versionless Key Vault secret URI (<vault_uri>secrets/<name>). Resolved at runtime with the app identity; values never enter Terraform state."
  type        = map(string)
  default     = {}
}

variable "mcp_server_env" {
  description = "Extra plain (non-secret) env vars for mcp-server."
  type        = map(string)
  default     = {}
}

variable "agent_api_env" {
  description = "Extra plain (non-secret) env vars for agent-api."
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "vCPU per replica."
  type        = number
  default     = 0.5
}

variable "memory" {
  description = "Memory per replica, for example 1Gi."
  type        = string
  default     = "1Gi"
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
