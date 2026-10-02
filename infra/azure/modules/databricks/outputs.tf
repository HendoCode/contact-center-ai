output "workspace_id" {
  description = "Azure resource ID of the workspace (databricks provider's azure_workspace_resource_id)."
  value       = azurerm_databricks_workspace.this.id
}

output "workspace_url" {
  description = "Workspace URL (DATABRICKS_HOST without the scheme)."
  value       = azurerm_databricks_workspace.this.workspace_url
}

output "cluster_id" {
  description = "ID of the single-node all-purpose cluster."
  value       = databricks_cluster.dev.id
}

output "http_path" {
  description = "http_path for dbt-databricks and the SQL connector against the cluster."
  value       = "sql/protocolv1/o/${azurerm_databricks_workspace.this.workspace_id}/${databricks_cluster.dev.id}"
}
