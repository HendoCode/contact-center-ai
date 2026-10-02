variable "resource_group_name" {
  description = "Resource group for the GPU VM and its network."
  type        = string
}

variable "location" {
  description = "Region for the GPU VM. The env passes gpu_location, which can differ from the resource group's region."
  type        = string
}

variable "name_prefix" {
  description = "Prefix for resource names."
  type        = string
  default     = "ccai"
}

variable "environment" {
  description = "Environment label used in resource names."
  type        = string
  default     = "dev"
}

variable "operator_ip" {
  description = "Public IPv4 address allowed to SSH to the VM. vLLM is reached over an SSH tunnel, so nothing else is opened."
  type        = string

  validation {
    condition     = can(regex("^([0-9]{1,3}\\.){3}[0-9]{1,3}$", var.operator_ip))
    error_message = "operator_ip must be a single IPv4 address, for example 203.0.113.10."
  }
}

variable "admin_ssh_public_key" {
  description = "SSH public key for the admin user (password login is disabled)."
  type        = string
}

variable "admin_username" {
  description = "Admin user on the VM."
  type        = string
  default     = "azureuser"
}

variable "vm_size" {
  description = "One A10 (24 GB), bf16-capable. T4 was rejected: 16 GB and no bf16."
  type        = string
  default     = "Standard_NV36ads_A10_v5"
}

variable "spot_max_price" {
  description = "Spot price cap in USD/hour (cost guardrail)."
  type        = number
  default     = 1.0
}

variable "os_disk_size_gb" {
  description = "OS disk size; model weights and the vLLM cache live here."
  type        = number
  default     = 256
}

variable "shutdown_time" {
  description = "Nightly auto-shutdown time, HHmm in shutdown_timezone."
  type        = string
  default     = "2200"
}

variable "shutdown_timezone" {
  description = "Windows time zone name for shutdown_time."
  type        = string
  default     = "Central Standard Time"
}

# TODO(F4): F4's cloud-init (models/finetune/serve/) does not exist yet. When it
# lands, pass its contents here (driver, vLLM, systemd unit, idle auto-shutdown).
variable "cloud_init" {
  description = "cloud-config user data. The default is a placeholder that installs nothing; null also selects it."
  type        = string
  nullable    = false
  default     = <<-EOT
    #cloud-config
    # TODO(F4): placeholder. Replace with F4's cloud-init: NVIDIA GRID driver,
    # vLLM, systemd unit, and idle deallocation through the VM identity.
    runcmd:
      - echo "placeholder cloud-init: F4 has not landed" > /var/log/ccai-cloud-init-placeholder.log
  EOT
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
