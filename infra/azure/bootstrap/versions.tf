terraform {
  required_version = "~> 1.16"

  # Applied once; state stays local (gitignored). It holds the state storage
  # for envs/*, the subscription budget and Key Vault, which must outlive every
  # `terraform destroy` of envs/dev (docs/design/infra.md).
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.81" # < 5.0; see docs/design/infra.md (I-UP)
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id

  # State storage disables shared keys, so data-plane calls use Entra.
  storage_use_azuread = true

  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.Consumption",
    "Microsoft.KeyVault",
    "Microsoft.Storage",
  ]
}
