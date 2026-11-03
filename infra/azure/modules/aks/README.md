# aks

A small AKS cluster for the Helm chart in [`infra/k8s`](../../../k8s/README.md), plus the registry it pulls from. Composed by [`envs/aks`](../../envs/aks/main.tf).

- **Cluster.** Free control plane, one autoscaled system pool (`Standard_D4s_v5`, 1 to 2 nodes), Azure CNI overlay, automatic patch upgrades. Entra sign-in with Azure RBAC and no local admin account; `operator_object_id` gets `Azure Kubernetes Service RBAC Cluster Admin`. Container Insights is off unless `log_analytics_workspace_id` is set.
- **Workload identity.** OIDC issuer and workload identity are on. A federated credential lets the chart's service account (`ccai/ccai` by default) act as the app identity from `modules/core`, which `modules/data` grants `Storage Blob Data Contributor` on ADLS. The Lance dataset at `az://lance` therefore needs no account key in the cluster.
- **Storage.** The Azure Disk and Azure Files CSI drivers (`managed-csi`, `azurefile-csi`) are on. The chart's AKS values put the Ollama model cache on `managed-csi`; `azurefile-csi` is the ReadWriteMany option for a Lance volume.
- **Registry.** ACR Basic, no admin user; the kubelet identity has `AcrPull`.
- **Ingress.** `enable_app_routing_gateway` turns on the managed Gateway API CRDs and the application routing add-on's Gateway API implementation (GatewayClass `approuting-istio`) through `azapi_update_resource`, because azurerm 4.81 has no arguments for them. The add-on's managed NGINX Ingress is not used: Microsoft ends its support after November 2026.

Plan and validate only; nothing here has been applied.
