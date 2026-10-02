# Storage and server names are global; the suffix keeps them unique per apply.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# The admin password lands in this root's remote state (Entra-authenticated,
# shared keys off). API keys are different: they stay in Key Vault only.
resource "random_password" "postgres_admin" {
  length  = 32
  special = false
}

# ── Postgres Flexible Server with pgvector ────────────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_postgresql_flexible_server" "main" {
  name                = "psql-${var.name_prefix}-${var.environment}-${random_string.suffix.result}"
  resource_group_name = var.resource_group_name
  location            = var.location
  version             = "16"

  sku_name   = var.postgres_sku_name
  storage_mb = var.postgres_storage_mb

  administrator_login    = var.postgres_admin_login
  administrator_password = random_password.postgres_admin.result

  # Cost guardrail: no HA, no geo-redundant backup.
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  public_network_access_enabled = true # private networking is a follow-up

  tags = var.tags

  lifecycle {
    # Azure assigns the zone; pinning it would fight the service.
    ignore_changes = [zone]
  }
}

# pgvector must be allow-listed before `CREATE EXTENSION vector` works.
resource "azurerm_postgresql_flexible_server_configuration" "extensions" {
  name      = "azure.extensions"
  server_id = azurerm_postgresql_flexible_server.main.id
  value     = "VECTOR"
}

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_postgresql_flexible_server_database" "app" {
  name      = var.postgres_database_name
  server_id = azurerm_postgresql_flexible_server.main.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# 0.0.0.0 - 0.0.0.0 is Azure's "allow Azure services" convention; it admits
# the Container Apps (no static egress IP without a VNet).
resource "azurerm_postgresql_flexible_server_firewall_rule" "azure_services" {
  name             = "allow-azure-services"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = "0.0.0.0"
  end_ip_address   = "0.0.0.0"
}

resource "azurerm_postgresql_flexible_server_firewall_rule" "operator" {
  count = var.operator_ip == null ? 0 : 1

  name             = "allow-operator"
  server_id        = azurerm_postgresql_flexible_server.main.id
  start_ip_address = var.operator_ip
  end_ip_address   = var.operator_ip
}

# ── ADLS Gen2: Lance datasets and dbt artifacts ───────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_storage_account" "adls" {
  name                = "stccai${var.environment}${random_string.suffix.result}"
  resource_group_name = var.resource_group_name
  location            = var.location

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"
  is_hns_enabled           = true
  min_tls_version          = "TLS1_2"

  allow_nested_items_to_be_public = false

  tags = var.tags
}

resource "azurerm_storage_data_lake_gen2_filesystem" "this" {
  for_each = var.adls_filesystems

  name               = each.key
  storage_account_id = azurerm_storage_account.adls.id
}

resource "azurerm_role_assignment" "app_blob" {
  scope                = azurerm_storage_account.adls.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.app_identity_principal_id
}

resource "azurerm_role_assignment" "operator_blob" {
  count = var.operator_object_id == null ? 0 : 1

  scope                = azurerm_storage_account.adls.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.operator_object_id
}
