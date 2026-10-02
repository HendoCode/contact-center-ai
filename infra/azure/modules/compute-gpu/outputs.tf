output "vm_id" {
  description = "Resource ID of the GPU VM."
  value       = azurerm_linux_virtual_machine.gpu.id
}

output "vm_name" {
  description = "Name of the GPU VM, for `az vm start|deallocate`."
  value       = azurerm_linux_virtual_machine.gpu.name
}

output "public_ip" {
  description = "Public IP, reachable on port 22 from operator_ip only."
  value       = azurerm_public_ip.gpu.ip_address
}

output "ssh_tunnel_command" {
  description = "SSH command that forwards vLLM's port to localhost:8000 (VLLM_BASE_URL=http://localhost:8000/v1)."
  value       = "ssh -L 8000:localhost:8000 ${var.admin_username}@${azurerm_public_ip.gpu.ip_address}"
}
