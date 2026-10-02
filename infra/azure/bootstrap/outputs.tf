output "state_resource_group_name" {
  description = "Resource group holding the state storage account."
  value       = azurerm_resource_group.bootstrap.name
}

output "state_storage_account_name" {
  description = "Storage account for envs/* remote state."
  value       = azurerm_storage_account.tfstate.name
}

output "state_container_name" {
  description = "Blob container for envs/* remote state."
  value       = azurerm_storage_container.tfstate.name
}

output "key_vault_id" {
  description = "Key Vault resource ID; pass to envs/dev as key_vault_id."
  value       = azurerm_key_vault.main.id
}

output "key_vault_name" {
  description = "Key Vault name, for `az keyvault secret set --vault-name`."
  value       = azurerm_key_vault.main.name
}

output "key_vault_uri" {
  description = "Key Vault URI; secret references use <uri>secrets/<name>."
  value       = azurerm_key_vault.main.vault_uri
}

output "backend_hcl_dev" {
  description = "Paste into infra/azure/envs/dev/backend.hcl (gitignored)."
  value       = <<-EOT
    tenant_id            = "${var.tenant_id}"
    subscription_id      = "${var.subscription_id}"
    resource_group_name  = "${azurerm_resource_group.bootstrap.name}"
    storage_account_name = "${azurerm_storage_account.tfstate.name}"
    container_name       = "${azurerm_storage_container.tfstate.name}"
    key                  = "dev.tfstate"
    use_azuread_auth     = true
  EOT
}
