provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  storage_use_azuread = true

  # Register only what this env uses, instead of the provider's default set.
  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.App",
    "Microsoft.Compute",
    "Microsoft.ContainerRegistry",
    "Microsoft.Databricks",
    "Microsoft.DBforPostgreSQL",
    "Microsoft.DevTestLab",
    "Microsoft.KeyVault",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.OperationalInsights",
    "Microsoft.Storage",
  ]
}

provider "azapi" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}

# Configured from the workspace this env creates. With enable_databricks = false
# the module has no instances and the provider is never used.
provider "databricks" {
  azure_workspace_resource_id = try(module.databricks[0].workspace_id, null)
}
