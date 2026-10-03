locals {
  tags = merge(
    {
      project         = "contact-center-ai"
      environment     = "dev"
      managed_by      = "terraform"
      session_expires = var.session_expires
    },
    var.tags,
  )
}

module "core" {
  source = "../../modules/core"

  location     = var.location
  key_vault_id = var.key_vault_id
  tags         = local.tags
}

module "data" {
  source = "../../modules/data"

  resource_group_name       = module.core.resource_group_name
  location                  = coalesce(var.data_location, module.core.location)
  operator_ip               = var.operator_ip
  operator_object_id        = var.operator_object_id
  app_identity_principal_id = module.core.app_identity_principal_id
  tags                      = local.tags
}

module "apps" {
  source = "../../modules/apps"

  resource_group_name        = module.core.resource_group_name
  location                   = coalesce(var.apps_location, module.core.location)
  log_analytics_workspace_id = module.core.log_analytics_workspace_id
  app_identity_id            = module.core.app_identity_id
  app_identity_principal_id  = module.core.app_identity_principal_id
  app_identity_client_id     = module.core.app_identity_client_id
  entra_tenant_id            = var.tenant_id
  entra_client_id            = var.entra_client_id
  enable_mcp_server          = var.enable_mcp_server
  enable_agent_api           = var.enable_agent_api
  database_url               = module.data.database_url
  key_vault_secrets          = var.key_vault_secrets
  tags                       = local.tags
}

module "compute_gpu" {
  source = "../../modules/compute-gpu"
  count  = var.enable_gpu ? 1 : 0

  resource_group_name  = module.core.resource_group_name
  location             = coalesce(var.gpu_location, module.core.location)
  operator_ip          = var.operator_ip          # validated non-null when enable_gpu
  admin_ssh_public_key = var.admin_ssh_public_key # validated non-null when enable_gpu
  cloud_init           = var.gpu_cloud_init       # null falls back to the module's placeholder
  tags                 = local.tags
}

module "databricks" {
  source = "../../modules/databricks"
  count  = var.enable_databricks ? 1 : 0

  resource_group_name = module.core.resource_group_name
  location            = coalesce(var.databricks_location, module.core.location)
  tags                = local.tags
}
