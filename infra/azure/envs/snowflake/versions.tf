terraform {
  required_version = "~> 1.16"

  # Partial config: pass the rest with `terraform init -backend-config=backend.hcl`
  # (gitignored; see backend.hcl.example). Same storage as envs/dev, separate key, so
  # `terraform destroy` of envs/dev never touches this root. CI uses `init -backend=false`.
  backend "azurerm" {}

  required_providers {
    snowflake = {
      source  = "snowflakedb/snowflake"
      version = "~> 2.21"
    }
  }
}
