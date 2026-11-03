output "resource_group_name" {
  description = "Resource group for this session."
  value       = module.core.resource_group_name
}

output "cluster_name" {
  description = "AKS cluster name."
  value       = module.aks.cluster_name
}

output "get_credentials_command" {
  description = "Writes a kubeconfig entry for the cluster (Entra sign-in through kubelogin)."
  value       = module.aks.get_credentials_command
}

output "acr_name" {
  description = "Registry name for `az acr build --registry`."
  value       = module.aks.acr_name
}

output "acr_login_server" {
  description = "Registry login server; the chart's image.repository is <this>/ccai-app."
  value       = module.aks.acr_login_server
}

output "app_identity_client_id" {
  description = "Client ID for the chart's service-account annotation azure.workload.identity/client-id."
  value       = module.core.app_identity_client_id
}

output "postgres_fqdn" {
  description = "externalDatabase.host for the chart."
  value       = module.data.postgres_fqdn
}

output "postgres_admin_login" {
  description = "externalDatabase.user for the chart."
  value       = module.data.postgres_admin_login
}

output "postgres_admin_password" {
  description = "POSTGRES_PASSWORD for the chart's Secret (sensitive; `terraform output -raw postgres_admin_password`)."
  value       = module.data.postgres_admin_password
  sensitive   = true
}

output "adls_account_name" {
  description = "AZURE_STORAGE_ACCOUNT_NAME for the chart's config."
  value       = module.data.adls_account_name
}

output "lance_uri" {
  description = "lance.uri for the chart."
  value       = module.data.lance_uri
}

output "helm_set_flags" {
  description = "The --set flags that point infra/k8s/values-aks.yaml at this env (append --set image.tag=<tag>)."
  value = join(" ", [
    "--set image.repository=${module.aks.acr_login_server}/ccai-app",
    "--set serviceAccount.annotations.azure\\.workload\\.identity/client-id=${module.core.app_identity_client_id}",
    "--set config.AZURE_STORAGE_ACCOUNT_NAME=${module.data.adls_account_name}",
    "--set externalDatabase.host=${module.data.postgres_fqdn}",
    "--set externalDatabase.user=${module.data.postgres_admin_login}",
  ])
}
