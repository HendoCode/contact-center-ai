output "cluster_name" {
  description = "AKS cluster name."
  value       = azurerm_kubernetes_cluster.main.name
}

output "cluster_id" {
  description = "AKS cluster resource ID."
  value       = azurerm_kubernetes_cluster.main.id
}

output "oidc_issuer_url" {
  description = "OIDC issuer the federated credential trusts."
  value       = azurerm_kubernetes_cluster.main.oidc_issuer_url
}

output "acr_name" {
  description = "Registry name for `az acr build --registry`."
  value       = azurerm_container_registry.main.name
}

output "acr_login_server" {
  description = "Registry login server; image.repository is <this>/ccai-app."
  value       = azurerm_container_registry.main.login_server
}

output "get_credentials_command" {
  description = "Writes a kubeconfig entry that signs in with Entra (kubelogin)."
  value       = "az aks get-credentials --resource-group ${var.resource_group_name} --name ${azurerm_kubernetes_cluster.main.name} --overwrite-existing"
}
