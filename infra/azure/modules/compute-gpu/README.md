# compute-gpu

One `Standard_NV36ads_A10_v5` Spot VM (one A10, 24 GB). Secure Boot and vTPM are off because the GRID driver requires it. SSH is allowed only from the operator IP; vLLM is reached over an SSH tunnel (`ssh_tunnel_command` output).

Cost guardrails (see [`docs/design/infra.md`](../../../../docs/design/infra.md)): spot price capped at `spot_max_price`, evicted to `Deallocate`, nightly auto-shutdown (`shutdown_time`, default 22:00 Central), and a system identity with `Virtual Machine Contributor` on the VM itself so an idle watcher can deallocate it. The env gates the whole module behind `enable_gpu` (default `false`).

**TODO(F4):** `cloud_init` defaults to a placeholder that installs nothing. F4's cloud-init (`models/finetune/serve/`: GRID driver, vLLM, systemd unit, idle deallocation) does not exist yet. Pass it in when it lands.

## Stephen runs

- Request Spot vCPU quota first: 36 vCPUs in East US 2 (Central US as the fallback; set `gpu_location`).
- Start and stop: `az vm start|deallocate -g <rg> -n <vm_name>`.
