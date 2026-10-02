locals {
  name = "${var.name_prefix}-${var.environment}-gpu"
}

# ── Network: one subnet, SSH from the operator IP only ────────────────────────

resource "azurerm_virtual_network" "gpu" {
  name                = "vnet-${local.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  address_space       = ["10.20.0.0/24"]
  tags                = var.tags
}

resource "azurerm_subnet" "gpu" {
  name                 = "snet-${local.name}"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.gpu.name
  address_prefixes     = ["10.20.0.0/26"]
}

resource "azurerm_network_security_group" "gpu" {
  name                = "nsg-${local.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  security_rule {
    name                       = "ssh-from-operator"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "${var.operator_ip}/32"
    destination_address_prefix = "*"
  }
}

resource "azurerm_subnet_network_security_group_association" "gpu" {
  subnet_id                 = azurerm_subnet.gpu.id
  network_security_group_id = azurerm_network_security_group.gpu.id
}

resource "azurerm_public_ip" "gpu" {
  name                = "pip-${local.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_network_interface" "gpu" {
  name                = "nic-${local.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  ip_configuration {
    name                          = "primary"
    subnet_id                     = azurerm_subnet.gpu.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.gpu.id
  }
}

# ── Spot VM ───────────────────────────────────────────────────────────────────

# tflint-ignore: azurerm_resources_missing_prevent_destroy
resource "azurerm_linux_virtual_machine" "gpu" {
  name                = "vm-${local.name}"
  resource_group_name = var.resource_group_name
  location            = var.location
  size                = var.vm_size

  # Spot, capped, evicted to Deallocate so the disk (and cached weights) survive.
  priority        = "Spot"
  eviction_policy = "Deallocate"
  max_bid_price   = var.spot_max_price

  admin_username                  = var.admin_username
  disable_password_authentication = true

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.admin_ssh_public_key
  }

  network_interface_ids = [azurerm_network_interface.gpu.id]

  # The NVIDIA GRID driver does not load with Secure Boot or vTPM on.
  secure_boot_enabled = false
  vtpm_enabled        = false

  # The identity lets the VM deallocate itself when idle (see the role below).
  identity {
    type = "SystemAssigned"
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = var.os_disk_size_gb
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  custom_data = base64encode(var.cloud_init)

  tags = var.tags
}

# Idle deallocation: a guest `shutdown` leaves the VM allocated and billing, so
# F4's idle watcher calls `az vm deallocate` with this identity. Scoped to the
# VM itself.
resource "azurerm_role_assignment" "self_deallocate" {
  scope                = azurerm_linux_virtual_machine.gpu.id
  role_definition_name = "Virtual Machine Contributor"
  principal_id         = azurerm_linux_virtual_machine.gpu.identity[0].principal_id
}

# Nightly shutdown deallocates, so compute billing stops too.
resource "azurerm_dev_test_global_vm_shutdown_schedule" "gpu" {
  virtual_machine_id    = azurerm_linux_virtual_machine.gpu.id
  location              = var.location
  enabled               = true
  daily_recurrence_time = var.shutdown_time
  timezone              = var.shutdown_timezone

  notification_settings {
    enabled = false
  }

  tags = var.tags
}
