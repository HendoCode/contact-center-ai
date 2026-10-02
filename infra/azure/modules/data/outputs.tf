output "postgres_fqdn" {
  description = "Postgres server FQDN."
  value       = azurerm_postgresql_flexible_server.main.fqdn
}

output "postgres_database_name" {
  description = "Application database name."
  value       = azurerm_postgresql_flexible_server_database.app.name
}

output "postgres_admin_login" {
  description = "Postgres administrator login."
  value       = azurerm_postgresql_flexible_server.main.administrator_login
}

output "database_url" {
  description = "DATABASE_URL for the app (sslmode=require; Azure Postgres rejects plaintext)."
  value       = "postgresql://${azurerm_postgresql_flexible_server.main.administrator_login}:${random_password.postgres_admin.result}@${azurerm_postgresql_flexible_server.main.fqdn}:5432/${azurerm_postgresql_flexible_server_database.app.name}?sslmode=require"
  sensitive   = true
}

output "adls_account_name" {
  description = "ADLS Gen2 storage account name."
  value       = azurerm_storage_account.adls.name
}

output "adls_account_id" {
  description = "ADLS Gen2 storage account resource ID."
  value       = azurerm_storage_account.adls.id
}

output "lance_uri" {
  description = "LANCE_URI for RETRIEVER_BACKEND=lancedb in the cloud profile. Set AZURE_STORAGE_ACCOUNT_NAME to adls_account_name alongside it."
  value       = "az://lance"
}

output "dbt_artifacts_uri" {
  description = "abfss URI of the dbt artifacts filesystem."
  value       = "abfss://dbt-artifacts@${azurerm_storage_account.adls.name}.dfs.core.windows.net/"
}
