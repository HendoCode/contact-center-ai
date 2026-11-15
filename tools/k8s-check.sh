#!/usr/bin/env bash
# Offline checks for the Helm chart in infra/k8s/ccai, the ones CI runs (`make k8s-check`):
# helm lint, then helm template and kubeconform schema validation for each values set
# (defaults, AKS, laptop, Ollama, Ingress, cloudflared and stopped mode).
# Needs helm and kubeconform on PATH; no cluster and no Azure account. kubeconform fetches the
# Kubernetes schemas and, for the Gateway API kinds, the CRD catalog, so it needs the network.
#
# KUBERNETES_VERSION picks the schema version (default 1.35.0).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$REPO_ROOT/infra/k8s/ccai"
KUBERNETES_VERSION="${KUBERNETES_VERSION:-1.35.0}"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

for tool in helm kubeconform; do
  command -v "$tool" >/dev/null || { echo "k8s-check: $tool is not on PATH" >&2; exit 1; }
done

# name|values files (comma-separated; empty = chart defaults)|extra --set flags
cases=(
  "default||"
  "aks|$REPO_ROOT/infra/k8s/values-aks.yaml|"
  "local|$REPO_ROOT/infra/k8s/values-local.yaml|"
  "local-ollama|$REPO_ROOT/infra/k8s/values-local.yaml,$REPO_ROOT/infra/k8s/values-local-ollama.yaml|"
  "ingress-external-db||--set ingress.enabled=true --set postgresql.enabled=false --set externalDatabase.host=db.example.com"
  "cloudflared||--set cloudflared.enabled=true"
  "stopped|$REPO_ROOT/infra/k8s/values-local.yaml|--set enabled=false --set cloudflared.enabled=true --set ollama.enabled=true"
)

for case in "${cases[@]}"; do
  IFS='|' read -r name values extra <<<"$case"
  args=()
  if [[ -n "$values" ]]; then
    IFS=',' read -ra files <<<"$values"
    for f in "${files[@]}"; do args+=(-f "$f"); done
  fi
  # shellcheck disable=SC2206
  [[ -n "$extra" ]] && args+=($extra)

  echo "== $name: helm lint"
  helm lint --strict "$CHART" "${args[@]}"

  echo "== $name: helm template | kubeconform (Kubernetes $KUBERNETES_VERSION)"
  helm template ccai "$CHART" --namespace ccai "${args[@]}" \
    | kubeconform -strict -summary -kubernetes-version "$KUBERNETES_VERSION" \
        -schema-location default -schema-location "$CRD_SCHEMAS"
done
