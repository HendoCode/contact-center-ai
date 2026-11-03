# The Kubernetes path beside envs/dev's Container Apps: the same core and data modules (resource
# group, Log Analytics, app identity, Postgres Flexible with pgvector, ADLS for Lance) under the
# environment label "aks", plus a small AKS cluster and its registry. The Helm chart in
# infra/k8s deploys the stack onto it; infra/k8s/README.md has the steps.

locals {
  tags = merge(
    {
      project         = "contact-center-ai"
      environment     = "aks"
      managed_by      = "terraform"
      session_expires = var.session_expires
    },
    var.tags,
  )
}

module "core" {
  source = "../../modules/core"

  location     = var.location
  environment  = "aks"
  key_vault_id = var.key_vault_id
  tags         = local.tags
}

module "data" {
  source = "../../modules/data"

  resource_group_name       = module.core.resource_group_name
  location                  = coalesce(var.data_location, module.core.location)
  environment               = "aks"
  operator_ip               = var.operator_ip
  operator_object_id        = var.operator_object_id
  app_identity_principal_id = module.core.app_identity_principal_id
  tags                      = local.tags
}

module "aks" {
  source = "../../modules/aks"

  resource_group_name        = module.core.resource_group_name
  location                   = coalesce(var.aks_location, module.core.location)
  environment                = "aks"
  tenant_id                  = var.tenant_id
  kubernetes_version         = var.kubernetes_version
  node_vm_size               = var.node_vm_size
  node_min_count             = var.node_min_count
  node_max_count             = var.node_max_count
  app_identity_id            = module.core.app_identity_id
  workload_namespace         = var.workload_namespace
  workload_service_account   = var.workload_service_account
  operator_object_id         = var.operator_object_id
  enable_app_routing_gateway = var.enable_app_routing_gateway
  log_analytics_workspace_id = var.enable_container_insights ? module.core.log_analytics_workspace_id : null
  tags                       = local.tags
}
