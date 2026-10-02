# Shared tflint config for every root and module under infra/azure.
# Run from this directory:
#   tflint --init --config="$PWD/.tflint.hcl"
#   tflint --recursive --config="$PWD/.tflint.hcl"

plugin "terraform" {
  enabled = true
  preset  = "recommended"
}

plugin "azurerm" {
  enabled = true
  version = "0.32.0"
  source  = "github.com/terraform-linters/tflint-ruleset-azurerm"
}
