# The databricks provider is configured by the calling root (host from this
# module's workspace_id output); this module only declares the requirement.

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_databricks_workspace" "this" {
  name                = "dbw-${var.name_prefix}-${var.environment}"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "premium"

  # Databricks' managed resource group sits outside envs/dev's resource group,
  # which is why the budget lives at subscription scope.
  managed_resource_group_name = "rg-${var.name_prefix}-${var.environment}-dbw-managed"

  tags = var.tags
}

data "databricks_spark_version" "lts" {
  long_term_support = true

  depends_on = [azurerm_databricks_workspace.this]
}

# All-purpose, not a job cluster: dbt, `mf` and S2's connector need an
# interactive http_path (docs/design/infra.md). No SQL warehouse is created.
resource "databricks_cluster" "dev" {
  cluster_name  = "${var.name_prefix}-${var.environment}-single-node"
  spark_version = data.databricks_spark_version.lts.id
  node_type_id  = var.node_type_id

  num_workers             = 0
  autotermination_minutes = var.autotermination_minutes

  spark_conf = {
    "spark.databricks.cluster.profile" = "singleNode"
    "spark.master"                     = "local[*]"
  }

  custom_tags = {
    ResourceClass = "SingleNode"
  }
}
