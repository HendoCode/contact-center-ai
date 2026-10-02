locals {
  # Both apps are cost-guarded the same way: scale to zero, one replica max.
  # mcp-server runs the streamable-HTTP transport (/mcp, /healthz); the image has no CMD, so
  # `command` is set. Easy Auth (below) is the only way in, so MCP_AUTH_UPSTREAM lets the
  # server bind 0.0.0.0 without a static token; an Entra token would not match one anyway.
  apps = {
    mcp-server = {
      enabled = var.enable_mcp_server
      image   = coalesce(var.mcp_server_image, "${azurerm_container_registry.main.login_server}/mcp-server:latest")
      port    = var.mcp_server_port
      command = ["python", "-m", "ccai_mcp.server", "--http"]
      env = merge(
        {
          MCP_TRANSPORT     = "http"
          MCP_SERVER_HOST   = "0.0.0.0"
          MCP_SERVER_PORT   = tostring(var.mcp_server_port)
          MCP_AUTH_UPSTREAM = "true"
        },
        var.mcp_server_env,
      )
    }
    agent-api = {
      enabled = var.enable_agent_api
      image   = coalesce(var.agent_api_image, "${azurerm_container_registry.main.login_server}/agent-api:latest")
      port    = var.agent_api_port
      command = null # the image default
      env     = var.agent_api_env
    }
  }

  enabled_apps = { for name, app in local.apps : name => app if app.enabled }

  # Container App secret names must be lowercase alphanumerics and dashes.
  kv_secret_names = { for env_name, _ in var.key_vault_secrets : env_name => lower(replace(env_name, "_", "-")) }

  # Version pinned to what azapi's embedded schema knows (2.13); the design
  # note's 2026-07-01 is not in it yet.
  auth_config_type = "Microsoft.App/containerApps/authConfigs@2026-01-01"
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# ── Registry ──────────────────────────────────────────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_container_registry" "main" {
  name                = "acr${var.name_prefix}${var.environment}${random_string.suffix.result}"
  resource_group_name = var.resource_group_name
  location            = var.location

  sku           = "Basic"
  admin_enabled = false # images are pulled with the app identity

  tags = var.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.main.id
  role_definition_name = "AcrPull"
  principal_id         = var.app_identity_principal_id
}

# ── Container Apps ────────────────────────────────────────────────────────────

resource "azurerm_container_app_environment" "main" {
  name                = "cae-${var.name_prefix}-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location

  logs_destination           = "log-analytics"
  log_analytics_workspace_id = var.log_analytics_workspace_id

  tags = var.tags
}

resource "azurerm_container_app" "this" {
  for_each = local.enabled_apps

  name                         = each.key
  resource_group_name          = var.resource_group_name
  container_app_environment_id = azurerm_container_app_environment.main.id
  revision_mode                = "Single"

  identity {
    type         = "UserAssigned"
    identity_ids = [var.app_identity_id]
  }

  registry {
    server   = azurerm_container_registry.main.login_server
    identity = var.app_identity_id
  }

  secret {
    name  = "database-url"
    value = var.database_url
  }

  dynamic "secret" {
    for_each = var.key_vault_secrets

    content {
      name                = local.kv_secret_names[secret.key]
      key_vault_secret_id = secret.value
      identity            = var.app_identity_id
    }
  }

  ingress {
    external_enabled = true # Easy Auth (below) rejects unauthenticated calls
    target_port      = each.value.port

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name    = each.key
      image   = each.value.image
      command = each.value.command
      cpu     = var.cpu
      memory  = var.memory

      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }

      env {
        name  = "AZURE_CLIENT_ID"
        value = var.app_identity_client_id
      }

      dynamic "env" {
        for_each = var.key_vault_secrets

        content {
          name        = env.key
          secret_name = local.kv_secret_names[env.key]
        }
      }

      dynamic "env" {
        for_each = each.value.env

        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }

  tags = var.tags

  depends_on = [azurerm_role_assignment.acr_pull]
}

# ── Easy Auth (Entra ID) ──────────────────────────────────────────────────────
# azurerm has no Container Apps auth-config resource (4.81), so this goes
# through azapi. Bearer tokens only: no client secret, no login redirect.
resource "azapi_resource" "easy_auth" {
  for_each = local.enabled_apps

  type      = local.auth_config_type
  name      = "current"
  parent_id = azurerm_container_app.this[each.key].id

  body = {
    properties = {
      platform = {
        enabled = true
      }
      globalValidation = {
        unauthenticatedClientAction = "Return401"
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            openIdIssuer = "https://login.microsoftonline.com/${var.entra_tenant_id}/v2.0"
            clientId     = var.entra_client_id
          }
          validation = {
            # v2 tokens carry the bare client ID as `aud`; v1 tokens carry the
            # api:// URI. Accept both.
            allowedAudiences = [var.entra_client_id, "api://${var.entra_client_id}"]
          }
        }
      }
    }
  }
}
