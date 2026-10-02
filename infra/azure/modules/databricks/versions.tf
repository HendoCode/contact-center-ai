terraform {
  required_version = "~> 1.16"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.135"
    }
  }
}
