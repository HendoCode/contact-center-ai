output "resource_group_name" {
  description = "Name of the environment resource group."
  value       = azurerm_resource_group.main.name
}

output "resource_group_id" {
  description = "Resource ID of the environment resource group."
  value       = azurerm_resource_group.main.id
}

output "location" {
  description = "Region of the resource group."
  value       = azurerm_resource_group.main.location
}

output "log_analytics_workspace_id" {
  description = "Resource ID of the Log Analytics workspace."
  value       = azurerm_log_analytics_workspace.main.id
}

output "app_identity_id" {
  description = "Resource ID of the app user-assigned identity."
  value       = azurerm_user_assigned_identity.app.id
}

output "app_identity_principal_id" {
  description = "Principal (object) ID of the app identity, for role assignments."
  value       = azurerm_user_assigned_identity.app.principal_id
}

output "app_identity_client_id" {
  description = "Client ID of the app identity, for AZURE_CLIENT_ID in containers."
  value       = azurerm_user_assigned_identity.app.client_id
}
