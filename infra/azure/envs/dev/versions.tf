terraform {
  required_version = "~> 1.16"

  # Partial config: pass the rest with `terraform init -backend-config=backend.hcl`
  # (gitignored; `terraform output backend_hcl_dev` in ../../bootstrap prints it).
  # CI uses `init -backend=false`.
  backend "azurerm" {}

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81" # < 5.0; see docs/design/infra.md (I-UP)
    }
    azapi = {
      source  = "azure/azapi"
      version = "~> 2.13"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
    databricks = {
      source  = "databricks/databricks"
      version = "~> 1.135"
    }
  }
}
