# infra/k8s

A Helm chart for running the MCP server stack on Kubernetes, with an AKS worked example. It runs
the same image and the same steps as the Compose `app` profile, and it sits beside the Container
Apps path in `infra/azure`. The AKS Terraform (`infra/azure/envs/aks`) is planned and validated
only; nothing has been applied to Azure.

```
ccai/              the chart
values-aks.yaml    AKS values: external Postgres, az:// Lance, workload identity, Gateway API
```

## Why Helm

The stack has several independent switches: in-chart or external Postgres, in-chart Ollama or
none, the Lance dataset on a volume or on object storage, and Ingress or Gateway API in front. A
Helm values file covers each combination with one template set, where Kustomize would need an
overlay per combination. Helm also names the data Job after the release revision, so each
upgrade runs the seed again, and it keeps a release history that `helm rollback` restores.
Kustomize has no release history, and a new Job name per rollout would be a manual edit there.

## What the chart deploys

| Component | Kind | Notes |
|---|---|---|
| Postgres (`pgvector/pgvector:pg16`) | StatefulSet, Service, PVC | `postgresql.enabled=false` uses `externalDatabase` instead |
| Ollama | Deployment, Service, PVC | `ollama.enabled=false` when both providers point elsewhere |
| Data Job (`<release>-ccai-data-r<revision>`) | Job | the Compose `seed` and `dbt` services, in order |
| mcp-server | Deployment, Service | `python -m ccai_mcp.server --http`, probes on `/healthz` |
| Lance dataset | PVC | none when `lance.uri` is an `az://` URI |
| Ingress or Gateway API route | Ingress, or Gateway and HTTPRoute | both off by default |
| `helm test` pod | Pod (test hook) | calls `/healthz` through the Service |

### Ordering

The data Job's pod runs its steps as containers in sequence. It waits for Postgres, pulls the
Ollama models, runs the seed step (OLTP schema, synthetic data, OLTP load, ingest into LanceDB
and then pgvector), and then runs `dbt build` as its main container. Each step that finishes
writes a row to `deploy.job_run` in Postgres.

Each mcp-server pod waits in init containers for a `dbt` row and for a non-empty transcript
store, then runs `dbt parse` to produce its own `target/semantic_manifest.json` for the metric
tools. Only then does the server start, so the probes never see the first seed, which takes
about 45 minutes on 2 CPU cores with Ollama. A later revision's seed with unchanged transcripts
and model reuses the stored vectors and takes under a minute.

Helm hooks were the other option. With `--wait`, Helm waits for the Deployment to be ready
before it runs post-install hooks, and here the Deployment waits for the seed, so the two would
deadlock. A plain Job with a revision-suffixed name avoids that. One Job, with the seed as an
init container before dbt, keeps the order when `helm rollback` re-applies an earlier
revision's Job; two Jobs ordered through Postgres rows could start dbt early on a rollback.

### Auth

The server binds `0.0.0.0` in the pod, so it requires `MCP_AUTH_TOKEN`
(`ccai_mcp/http_transport.py`). This is the same demo-grade static bearer token as the Compose
`mcp-server`. Put TLS in front of it at the Ingress or Gateway. AKS has no Easy Auth equivalent
for this setup, so Entra sign-in in front of the server is listed in
[docs/ROADMAP.md](../../docs/ROADMAP.md).

## Secrets

The chart only references a Secret; it never creates one, and no secret value is committed. Create
it in the release namespace before installing. Every key becomes an environment variable in the
app containers.

| Key | Required | Used for |
|---|---|---|
| `MCP_AUTH_TOKEN` | yes | the server's bearer token (`openssl rand -hex 32`) |
| `POSTGRES_PASSWORD` | yes | the in-chart Postgres, or the external server's password |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `FIREWORKS_API_KEY` | per `LLM_PROVIDER` | chat provider keys |
| `AZURE_STORAGE_ACCOUNT_KEY` | only for `az://` without workload identity | ADLS access for Lance |

`DATABASE_URL` is assembled from `POSTGRES_PASSWORD`, so use URL-safe characters (hex is safe).

```bash
kubectl create namespace ccai
kubectl -n ccai create secret generic ccai-secrets \
  --from-literal=MCP_AUTH_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -hex 24)"
```

## Install on any cluster

The chart expects the repo's image. No workflow publishes it yet, so build it and either push it
to a registry the cluster can pull from or load it into a local cluster.

```bash
docker build -t ccai-app:local .
kind create cluster --name ccai
kind load docker-image ccai-app:local --name ccai     # about 2.6 GB

# the Secret from "Secrets" above, then:
helm upgrade --install ccai infra/k8s/ccai -n ccai
kubectl -n ccai logs -f job/ccai-data-r1 -c seed
kubectl -n ccai rollout status deploy/ccai-mcp --timeout=3h

kubectl -n ccai port-forward svc/ccai-mcp 8000:8000
MCP_SERVER_URL=http://127.0.0.1:8000/mcp MCP_AUTH_TOKEN=<token> \
  uv run python -m ccai_mcp.http_smoke --call get_transcript '{"call_id": "CALL-00042"}'
helm test ccai -n ccai
```

The defaults mirror `make demo`: chat and embeddings on the in-chart Ollama, which pulls
`nomic-embed-text` and `llama3.2` into its volume. The Ollama image is about 3.4 GB compressed.
To use an OpenAI-compatible chat endpoint instead, put the key in the Secret and set
`config.LLM_PROVIDER=openai` (plus `LLM_BASE_URL` and `LLM_MODEL` if they differ from the
defaults). Any non-secret variable from `.env.example` goes under `config`.

## Upgrade

```bash
helm upgrade ccai infra/k8s/ccai -n ccai -f my-values.yaml
kubectl -n ccai wait --for=condition=complete job/ccai-data-r<new revision> --timeout=3h
```

Each upgrade creates `ccai-data-r<revision>` and Helm deletes the previous revision's Job. The
seed and dbt steps are idempotent. `--set seed.enabled=false` skips the seed on that revision,
and the readers start on the data already in place. The mcp-server Deployment rolls only when
its pod spec or the ConfigMap changes (the pods carry a checksum of the ConfigMap).

## Rollback

```bash
helm history ccai -n ccai
helm rollback ccai <revision> -n ccai
```

A rollback re-applies that revision's manifests, including its data Job, which runs again with
that revision's values. `helm rollback` restores manifests and settings. It does not restore
data. To return the transcript store to an earlier ingest, use the Lance version tags in the
pod:

```bash
kubectl -n ccai exec deploy/ccai-mcp -- python -m retrieval.lance_versions list
kubectl -n ccai exec deploy/ccai-mcp -- python -m retrieval.lance_versions rollback seed-<sha>
```

To move the readers off LanceDB, set `retrieverBackend=pgvector`. The seed step keeps
pgvector filled (`seed.pgvector`), as in Compose.

## Uninstall

```bash
helm uninstall ccai -n ccai
```

The data stays. The Lance PVC carries `helm.sh/resource-policy: keep`, and the Postgres volume
belongs to the StatefulSet's claim template, which Kubernetes never deletes with the StatefulSet.
A later install into the same namespace with the same Secret finds both. Delete them by hand to
start over (`kubectl -n ccai delete pvc ccai-lance data-ccai-postgresql-0`).

## Where data lives

| Data | Default chart | AKS values |
|---|---|---|
| OLTP tables, dbt marts, CSAT fact, pgvector table | PVC `data-<release>-ccai-postgresql-0` | Azure Database for PostgreSQL Flexible Server (`modules/data`) |
| Lance dataset (the transcript store) | PVC `<release>-ccai-lance` at `/app/data/lance` | ADLS Gen2 filesystem `lance` (`az://lance`) |
| Ollama models | PVC `<release>-ccai-ollama` | same, on Azure Disk (`managed-csi`) |
| dbt `target/` | an `emptyDir` per mcp-server pod, from `dbt parse` | same |
| Generated synthetic JSON | the data Job's container filesystem, regenerated each run | same |
| Job ordering rows | `deploy.job_run` in Postgres | same |

The Lance PVC is `ReadWriteOnce` by default, so the data Job and the mcp-server pods must run on
the same node. On a multi-node cluster, use a `ReadWriteMany` class (Azure Files on AKS) or an
object-store URI.

## AKS worked example

`infra/azure/envs/aks` composes the existing `core` and `data` modules (under the environment
label `aks`) with a new `modules/aks`. Together they create a Free-tier cluster with one autoscaled
`Standard_D4s_v5` pool (1 to 2 nodes), an ACR the nodes pull from, Postgres Flexible with
pgvector, and ADLS. They also enable workload identity for the chart's service account and the
application routing add-on's Gateway API implementation. GPU node pools are out of scope.

These choices are in `values-aks.yaml`.

- **Storage.** The Lance dataset is on ADLS (`az://lance`), as on Container Apps, so no pod is
  tied to one node's disk. The Ollama cache is on Azure Disk (`managed-csi`). Azure Files
  (`azurefile-csi`, ReadWriteMany) is the alternative for a Lance volume, shown commented in the
  file.
- **ADLS access.** Workload identity. The service account `ccai/ccai` is federated with the app
  identity from `modules/core`, which `modules/data` grants `Storage Blob Data Contributor`.
  LanceDB reads the `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` and `AZURE_FEDERATED_TOKEN_FILE`
  variables that the webhook injects. The alternative is `AZURE_STORAGE_ACCOUNT_KEY` in the
  Secret. Neither has been tried on a live cluster.
- **Ingress.** A Gateway of class `approuting-istio` with an HTTPS listener, and an HTTPRoute to
  the server. Microsoft ends support for the add-on's managed NGINX Ingress after November 2026
  and recommends this Gateway API implementation in its place. The TLS certificate is the Secret
  `ccai-mcp-tls`, which you create.
- **Models.** Chat on the repo's default OpenAI-compatible endpoint (OpenRouter, key in the
  Secret) and embeddings on the in-chart Ollama. No Azure OpenAI.

### Apply steps (run by hand; agents do not run these)

```bash
cd infra/azure/envs/aks
cp terraform.tfvars.example terraform.tfvars              # fill in
sed 's/dev.tfstate/aks.tfstate/' ../dev/backend.hcl > backend.hcl
terraform init -backend-config=backend.hcl
terraform plan
terraform apply

$(terraform output -raw get_credentials_command)
az acr build --registry "$(terraform output -raw acr_name)" --image ccai-app:"$(git rev-parse --short HEAD)" ../../../..

kubectl create namespace ccai
kubectl -n ccai create secret generic ccai-secrets \
  --from-literal=MCP_AUTH_TOKEN="$(openssl rand -hex 32)" \
  --from-literal=POSTGRES_PASSWORD="$(terraform output -raw postgres_admin_password)" \
  --from-literal=OPENAI_API_KEY=<scoped key>
kubectl -n ccai create secret tls ccai-mcp-tls --cert=<cert.pem> --key=<key.pem>

helm upgrade --install ccai ../../../k8s/ccai -n ccai -f ../../../k8s/values-aks.yaml \
  $(terraform output -raw helm_set_flags) --set image.tag="$(git rev-parse --short HEAD)"

terraform destroy    # end of session
```

The Postgres firewall keeps the `allow-azure-services` rule from `modules/data`, which admits
the cluster's outbound address along with every other Azure address. Private networking is a
follow-up, as it is for Container Apps.

## Checks

CI runs these, and they need no cluster or Azure account:

```bash
make k8s-check      # helm lint + helm template | kubeconform for default, AKS and Ingress values
cd infra/azure && terraform fmt -check -recursive
(cd infra/azure/envs/aks && terraform init -backend=false && terraform validate)
```

`make k8s-check` needs `helm` and `kubeconform` on PATH. kubeconform downloads the Kubernetes
schemas, and the Gateway API schemas from the CRD catalog, so it needs network access.
`tests/test_helm_chart.py` (part of `make test`) checks that the chart's seed and server
commands match the Compose services.

## Not in the chart

The Compose `langgraph-api` (a dev server), `jupyter`, `demo`, `evals` and `cloudflared`
services are not in the chart. Each would need its own auth or exposure decisions. They are
listed in [docs/ROADMAP.md](../../docs/ROADMAP.md).
