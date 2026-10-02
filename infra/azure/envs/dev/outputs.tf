output "resource_group_name" {
  description = "Resource group for this session."
  value       = module.core.resource_group_name
}

output "postgres_fqdn" {
  description = "Postgres server FQDN."
  value       = module.data.postgres_fqdn
}

output "database_url" {
  description = "DATABASE_URL for the cloud Postgres (sensitive; `terraform output -raw database_url`)."
  value       = module.data.database_url
  sensitive   = true
}

output "lance_uri" {
  description = "LANCE_URI for RETRIEVER_BACKEND=lancedb."
  value       = module.data.lance_uri
}

output "adls_account_name" {
  description = "ADLS Gen2 account (set AZURE_STORAGE_ACCOUNT_NAME to this for Lance)."
  value       = module.data.adls_account_name
}

output "acr_name" {
  description = "Registry name for `az acr build --registry`."
  value       = module.apps.acr_name
}

output "app_urls" {
  description = "HTTPS URL per enabled container app."
  value       = module.apps.app_urls
}

output "gpu_ssh_tunnel_command" {
  description = "SSH tunnel to vLLM on the GPU VM; null when enable_gpu is false."
  value       = one(module.compute_gpu[*].ssh_tunnel_command)
}

output "gpu_vm_name" {
  description = "GPU VM name for `az vm start|deallocate`; null when enable_gpu is false."
  value       = one(module.compute_gpu[*].vm_name)
}

output "databricks_workspace_url" {
  description = "Databricks workspace URL; null when enable_databricks is false."
  value       = one(module.databricks[*].workspace_url)
}

output "databricks_http_path" {
  description = "http_path for dbt-databricks; null when enable_databricks is false."
  value       = one(module.databricks[*].http_path)
}
