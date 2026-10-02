output "acr_login_server" {
  description = "Registry login server, for `az acr build` and image references."
  value       = azurerm_container_registry.main.login_server
}

output "acr_name" {
  description = "Registry name, for `az acr build --registry`."
  value       = azurerm_container_registry.main.name
}

output "container_app_environment_id" {
  description = "Resource ID of the Container Apps environment."
  value       = azurerm_container_app_environment.main.id
}

output "app_urls" {
  description = "HTTPS URL per enabled container app (empty until enable_mcp_server / enable_agent_api)."
  value       = { for name, app in azurerm_container_app.this : name => "https://${app.ingress[0].fqdn}" }
}
