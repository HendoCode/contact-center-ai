# apps

ACR Basic, a Container Apps environment, and the `mcp-server` and `agent-api` container apps (0–1 replicas, scale to zero), each with Easy Auth.

**Containers are gated off by count.** `enable_mcp_server` waits for M1 (streamable-HTTP transport; the MCP server is stdio-only today) and `enable_agent_api` waits for L4 (agent HTTP endpoint). With both `false`, only the registry and environment exist.

**Easy Auth** is an `azapi_resource` (`Microsoft.App/containerApps/authConfigs`, `Return401`, Entra v2 issuer), because azurerm 4.81 has no Container Apps auth-config resource. It validates bearer tokens only; there is no client secret.

**Secrets.** `DATABASE_URL` is a Container Apps secret (the Postgres password is in state anyway). API keys come from Key Vault: pass `key_vault_secrets = { OPENAI_API_KEY = "<vault_uri>secrets/openai-api-key" }` and the app identity resolves them at runtime, so the values never enter state.

## Stephen runs (before enabling a container app)

```bash
# 1. Register an Entra app for the API: set accessTokenAcceptedVersion = 2 in its manifest,
#    expose an API scope, and put its client ID in entra_client_id.
# 2. Build the image into the registry the apply created:
az acr build --registry "$(terraform output -raw acr_name)" --image mcp-server:latest .
```
