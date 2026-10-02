resource "azurerm_resource_group" "main" {
  name     = "rg-${var.name_prefix}-${var.environment}"
  location = var.location
  tags     = var.tags
}

# The 0.5 GB/day cap is a cost guardrail (docs/design/infra.md): ingestion
# stops for the day once it is hit.
resource "azurerm_log_analytics_workspace" "main" {
  name                = "log-${var.name_prefix}-${var.environment}"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location

  sku               = "PerGB2018"
  retention_in_days = var.log_analytics_retention_days
  daily_quota_gb    = var.log_analytics_daily_quota_gb

  tags = var.tags
}

# One user-assigned identity shared by the Container Apps. It reads secrets
# from the bootstrap Key Vault; the apps and data modules grant it ACR pull
# and storage access.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${var.name_prefix}-${var.environment}-app"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  tags                = var.tags
}

resource "azurerm_role_assignment" "app_key_vault_secrets_user" {
  scope                = var.key_vault_id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}
