locals {
  cluster_name = "aks-${var.name_prefix}-${var.environment}"

  # The managed-cluster API version that documents ingressProfile.gatewayAPI and
  # webAppRouting.gatewayAPIImplementations (Microsoft's Gateway API quickstart uses it).
  managed_cluster_api = "Microsoft.ContainerService/managedClusters@2026-03-01"
}

# Registry names are global; the suffix keeps them unique per apply.
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

# ── Registry ──────────────────────────────────────────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_container_registry" "main" {
  name                = "acr${var.name_prefix}${var.environment}${random_string.suffix.result}"
  resource_group_name = var.resource_group_name
  location            = var.location

  sku           = "Basic"
  admin_enabled = false # the kubelet identity pulls

  tags = var.tags
}

# ── Cluster ───────────────────────────────────────────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_kubernetes_cluster" "main" {
  name                = local.cluster_name
  resource_group_name = var.resource_group_name
  location            = var.location
  dns_prefix          = local.cluster_name
  kubernetes_version  = var.kubernetes_version

  # Cost guardrail: the free control plane (no uptime SLA).
  sku_tier = "Free"

  automatic_upgrade_channel = "patch"
  node_os_upgrade_channel   = "NodeImage"

  # Workload identity: pods exchange their service-account token for an Entra token of the app
  # identity, so ADLS (az:// Lance) needs no account key in the cluster.
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  # Entra sign-in and Azure RBAC for kubectl; no local admin kubeconfig.
  local_account_disabled = true
  azure_active_directory_role_based_access_control {
    tenant_id          = var.tenant_id
    azure_rbac_enabled = true
  }

  default_node_pool {
    name                        = "system"
    vm_size                     = var.node_vm_size
    auto_scaling_enabled        = true
    min_count                   = var.node_min_count
    max_count                   = var.node_max_count
    os_disk_size_gb             = var.os_disk_size_gb
    temporary_name_for_rotation = "systemtmp"

    upgrade_settings {
      max_surge = "1"
    }
  }

  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    load_balancer_sku   = "standard"
  }

  # Azure Disk (managed-csi) for the Ollama model cache and Azure Files (azurefile-csi) for a
  # ReadWriteMany Lance volume; both are on by default, stated here because the chart uses them.
  storage_profile {
    disk_driver_enabled = true
    file_driver_enabled = true
  }

  dynamic "oms_agent" {
    for_each = var.log_analytics_workspace_id == null ? [] : [var.log_analytics_workspace_id]

    content {
      log_analytics_workspace_id      = oms_agent.value
      msi_auth_for_monitoring_enabled = true
    }
  }

  tags = var.tags

  lifecycle {
    # The autoscaler owns the node count.
    ignore_changes = [default_node_pool[0].node_count]
  }
}

resource "azurerm_role_assignment" "kubelet_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "operator_cluster_admin" {
  count = var.operator_object_id == null ? 0 : 1

  scope                = azurerm_kubernetes_cluster.main.id
  role_definition_name = "Azure Kubernetes Service RBAC Cluster Admin"
  principal_id         = var.operator_object_id
}

# The chart's service account may act as the app identity, which modules/data grants
# Storage Blob Data Contributor on the ADLS account.
resource "azurerm_federated_identity_credential" "workload" {
  name                      = "fic-${local.cluster_name}-${var.workload_namespace}-${var.workload_service_account}"
  user_assigned_identity_id = var.app_identity_id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = azurerm_kubernetes_cluster.main.oidc_issuer_url
  subject                   = "system:serviceaccount:${var.workload_namespace}:${var.workload_service_account}"
}

# ── Ingress: application routing with Gateway API ─────────────────────────────
# azurerm has no arguments for the managed Gateway API CRDs or the add-on's Gateway API
# implementation, so this sets them through azapi, as Microsoft's quickstart does. The chart
# then creates a Gateway of class approuting-istio and an HTTPRoute.
resource "azapi_update_resource" "app_routing_gateway" {
  count = var.enable_app_routing_gateway ? 1 : 0

  type        = local.managed_cluster_api
  resource_id = azurerm_kubernetes_cluster.main.id

  body = {
    properties = {
      ingressProfile = {
        gatewayAPI = {
          installation = "Standard"
        }
        webAppRouting = {
          gatewayAPIImplementations = {
            appRoutingIstio = {
              mode = "Enabled"
            }
          }
        }
      }
    }
  }
}
