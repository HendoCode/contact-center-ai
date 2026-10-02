locals {
  tags = merge(
    {
      project    = "contact-center-ai"
      root       = "bootstrap"
      managed_by = "terraform"
    },
    var.tags,
  )

  state_container = "tfstate"
}

# Storage account and vault names are global; the suffix keeps them unique and
# stable (it lives in this root's local state).
resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "bootstrap" {
  name     = "rg-ccai-bootstrap"
  location = var.location
  tags     = local.tags
}

# ── Remote state for envs/* ───────────────────────────────────────────────────

resource "azurerm_storage_account" "tfstate" {
  name                = "stccaitf${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location

  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"

  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }
  }

  tags = local.tags

  # Must outlive `terraform destroy`; remove deliberately, never by accident.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_storage_container" "tfstate" {
  name                  = local.state_container
  storage_account_id    = azurerm_storage_account.tfstate.id
  container_access_type = "private"

  # Must outlive `terraform destroy`; remove deliberately, never by accident.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_role_assignment" "operator_state" {
  scope                = azurerm_storage_account.tfstate.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = var.operator_object_id
}

# ── Key Vault ─────────────────────────────────────────────────────────────────
# API keys are set with `az keyvault secret set`, never through Terraform, so
# they stay out of state and survive `destroy` of envs/dev.

resource "azurerm_key_vault" "main" {
  name                = "kv-ccai-${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.bootstrap.name
  location            = azurerm_resource_group.bootstrap.location
  tenant_id           = var.tenant_id
  sku_name            = "standard"

  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false

  tags = local.tags

  # Must outlive `terraform destroy`; remove deliberately, never by accident.
  lifecycle {
    prevent_destroy = true
  }
}

resource "azurerm_role_assignment" "operator_vault" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = var.operator_object_id
}

# ── Budget ────────────────────────────────────────────────────────────────────
# Subscription scope on purpose: Databricks' managed resource group sits
# outside envs/dev. Budgets only notify; the guardrails in
# docs/design/infra.md stop spend.

resource "azurerm_consumption_budget_subscription" "monthly" {
  name            = "budget-ccai-monthly"
  subscription_id = "/subscriptions/${var.subscription_id}"

  amount     = var.budget_amount
  time_grain = "Monthly"

  time_period {
    start_date = var.budget_start_date
  }

  notification {
    enabled        = true
    threshold      = 50
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 90
    threshold_type = "Actual"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    threshold_type = "Forecasted"
    operator       = "GreaterThanOrEqualTo"
    contact_emails = var.budget_contact_emails
  }
}
