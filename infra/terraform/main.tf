terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
  required_version = ">= 1.5.0"
}

provider "azurerm" {
  features {}
}

# ── Variables ─────────────────────────────────────────────────────────────────

variable "resource_group_name" {
  description = "Name of the Azure resource group"
  default     = "contact-center-ai-rg"
}

variable "location" {
  description = "Azure region"
  default     = "eastus"
}

variable "environment" {
  description = "Environment tag (dev, staging, prod)"
  default     = "dev"
}

variable "entra_tenant_id" {
  description = "Microsoft Entra tenant ID (for App Service auth)"
  sensitive   = true
}

variable "entra_client_id" {
  description = "Microsoft Entra client/app ID (for App Service auth)"
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key"
  sensitive   = true
}

# ── Resource Group ────────────────────────────────────────────────────────────

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location
  tags     = { environment = var.environment }
}

# ── PostgreSQL with pgvector ──────────────────────────────────────────────────

resource "azurerm_postgresql_flexible_server" "main" {
  name                   = "contact-center-ai-pg"
  resource_group_name    = azurerm_resource_group.main.name
  location               = azurerm_resource_group.main.location
  version                = "16"
  administrator_login    = "pgadmin"
  administrator_password = random_password.pg_password.result
  sku_name               = "B_Standard_B1ms"
  storage_mb             = 32768
  tags                   = { environment = var.environment }
}

resource "random_password" "pg_password" {
  length  = 24
  special = true
}

resource "azurerm_postgresql_flexible_server_database" "main" {
  name      = "contactcenter"
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# Enable pgvector extension
resource "azurerm_postgresql_flexible_server_configuration" "pgvector" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

# ── App Service Plan ──────────────────────────────────────────────────────────

resource "azurerm_service_plan" "main" {
  name                = "contact-center-ai-plan"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  os_type             = "Linux"
  sku_name            = "B2"
  tags                = { environment = var.environment }
}

# ── App Service (MCP Server) ──────────────────────────────────────────────────

resource "azurerm_linux_web_app" "mcp_server" {
  name                = "contact-center-ai-mcp"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  service_plan_id     = azurerm_service_plan.main.id
  https_only          = true

  site_config {
    application_stack {
      python_version = "3.11"
    }
  }

  app_settings = {
    "OPENAI_API_KEY"  = var.openai_api_key
    "DATABASE_URL"    = "postgresql://pgadmin:${random_password.pg_password.result}@${azurerm_postgresql_flexible_server.main.fqdn}/contactcenter"
    "SCM_DO_BUILD_DURING_DEPLOYMENT" = "true"
  }

  # ── Entra auth (Easy Auth for MCP) ────────────────────────────────────────
  # Implements: https://learn.microsoft.com/en-us/azure/app-service/configure-authentication-mcp
  auth_settings_v2 {
    auth_enabled = true
    unauthenticated_action = "Return401"

    active_directory_v2 {
      tenant_auth_endpoint = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
      client_id            = var.entra_client_id
      allowed_audiences    = ["api://${var.entra_client_id}"]
    }

    login {
      token_store_enabled = true
    }
  }

  tags = { environment = var.environment }
}

# ── Outputs ───────────────────────────────────────────────────────────────────

output "mcp_server_url" {
  description = "URL of the deployed MCP server"
  value       = "https://${azurerm_linux_web_app.mcp_server.default_hostname}"
}

output "database_fqdn" {
  description = "PostgreSQL server FQDN"
  value       = azurerm_postgresql_flexible_server.main.fqdn
}
