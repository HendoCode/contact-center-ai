{{/* Names */}}

{{- define "ccai.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ccai.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* <fullname>-<suffix>, kept within the 63-character name limit. */}}
{{- define "ccai.component" -}}
{{- printf "%s-%s" (include "ccai.fullname" .root | trunc (int (sub 62 (len .name)))) .name | trimSuffix "-" }}
{{- end }}

{{- define "ccai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "ccai.labels" -}}
helm.sh/chart: {{ include "ccai.chart" . }}
{{ include "ccai.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "ccai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ccai.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "ccai.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "ccai.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "ccai.image" -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag }}
{{- end }}

{{/* Where things are */}}

{{- define "ccai.postgresHost" -}}
{{- if .Values.postgresql.enabled }}
{{- include "ccai.component" (dict "root" . "name" "postgresql") }}
{{- else }}
{{- required "externalDatabase.host is required when postgresql.enabled is false" .Values.externalDatabase.host }}
{{- end }}
{{- end }}

{{- define "ccai.lanceOnPvc" -}}
{{- if not .Values.lance.uri }}true{{ end }}
{{- end }}

{{- define "ccai.lanceUri" -}}
{{- default "/app/data/lance" .Values.lance.uri }}
{{- end }}

{{- define "ccai.ollamaUrl" -}}
{{- if .Values.ollama.enabled }}
{{- printf "http://%s:%d" (include "ccai.component" (dict "root" . "name" "ollama")) (int .Values.ollama.port) }}
{{- else }}
{{- .Values.config.OLLAMA_BASE_URL | default "" }}
{{- end }}
{{- end }}

{{/*
Database settings for every app container. dbt reads DBT_*; the Python code reads DATABASE_URL,
which Kubernetes assembles from the variables before it ($(VAR) expansion).
*/}}
{{- define "ccai.dbEnv" -}}
{{- $pg := .Values.postgresql }}
{{- $ext := .Values.externalDatabase -}}
- name: DBT_HOST
  value: {{ include "ccai.postgresHost" . | quote }}
- name: DBT_PORT
  value: {{ (ternary $pg.port $ext.port $pg.enabled) | toString | quote }}
- name: DBT_USER
  value: {{ (ternary $pg.user $ext.user $pg.enabled) | quote }}
- name: DBT_DBNAME
  value: {{ (ternary $pg.database $ext.database $pg.enabled) | quote }}
- name: DBT_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.name }}
      key: POSTGRES_PASSWORD
{{- if and (not $pg.enabled) $ext.sslmode }}
- name: PGSSLMODE
  value: {{ $ext.sslmode | quote }}
- name: DATABASE_URL
  value: "postgresql://$(DBT_USER):$(DBT_PASSWORD)@$(DBT_HOST):$(DBT_PORT)/$(DBT_DBNAME)?sslmode={{ $ext.sslmode }}"
{{- else }}
- name: DATABASE_URL
  value: "postgresql://$(DBT_USER):$(DBT_PASSWORD)@$(DBT_HOST):$(DBT_PORT)/$(DBT_DBNAME)"
{{- end }}
- name: DBT_PROFILES_DIR
  value: /app/olap/dbt
{{- end }}

{{/* ConfigMap and Secret, then the database variables. */}}
{{- define "ccai.appEnv" -}}
envFrom:
  - configMapRef:
      name: {{ include "ccai.component" (dict "root" . "name" "config") }}
  - secretRef:
      name: {{ .Values.secrets.name }}
env:
{{- include "ccai.dbEnv" . | nindent 2 }}
{{- end }}

{{/* Pod-level settings shared by every app pod. */}}
{{- define "ccai.appPodSpec" -}}
serviceAccountName: {{ include "ccai.serviceAccountName" . }}
automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.podSecurityContext }}
securityContext:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}

{{- define "ccai.appPodMeta" -}}
labels:
  {{- include "ccai.selectorLabels" .root | nindent 2 }}
  app.kubernetes.io/component: {{ .component }}
  {{- with .root.Values.podLabels }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
annotations:
  checksum/config: {{ include (print .root.Template.BasePath "/configmap.yaml") .root | sha256sum }}
  {{- with .root.Values.podAnnotations }}
  {{- toYaml . | nindent 2 }}
  {{- end }}
{{- end }}

{{/* An init container that waits until Postgres accepts connections. */}}
{{- define "ccai.waitForPostgres" -}}
- name: wait-for-postgres
  image: {{ include "ccai.image" . }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  command:
    - sh
    - -c
    - |
      until pg_isready -q -d "$DATABASE_URL"; do echo "waiting for Postgres at $DBT_HOST:$DBT_PORT"; sleep 3; done
  env:
    {{- include "ccai.dbEnv" . | nindent 4 }}
  {{- with .Values.securityContext }}
  securityContext:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}

{{/*
A row per finished data Job step and release revision in deploy.job_run. The mcp-server pods wait
for a dbt row before they start, so a fresh install's readers start on built marts.
*/}}
{{- define "ccai.markJob" -}}
PGOPTIONS='-c client_min_messages=warning' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q <<SQL
CREATE SCHEMA IF NOT EXISTS deploy;
CREATE TABLE IF NOT EXISTS deploy.job_run (
  job text NOT NULL,
  revision integer NOT NULL,
  finished_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (job, revision)
);
INSERT INTO deploy.job_run (job, revision) VALUES ('{{ .job }}', {{ .root.Release.Revision }})
  ON CONFLICT (job, revision) DO UPDATE SET finished_at = now();
SQL
echo "recorded {{ .job }} for revision {{ .root.Release.Revision }} in deploy.job_run"
{{- end }}

{{/* Lance dataset volume and mount, when the dataset is on the PVC. */}}
{{- define "ccai.lanceVolume" -}}
{{- if include "ccai.lanceOnPvc" . -}}
- name: lance
  persistentVolumeClaim:
    claimName: {{ include "ccai.component" (dict "root" . "name" "lance") }}
{{- end }}
{{- end }}

{{- define "ccai.lanceMount" -}}
{{- if include "ccai.lanceOnPvc" . -}}
- name: lance
  mountPath: /app/data/lance
{{- end }}
{{- end }}

{{/*
The seed step of the data Job: a container spec without its `name`, so it runs as an init
container before dbt, or as the main container when dbt is disabled.
*/}}
{{- define "ccai.seedStep" -}}
image: {{ include "ccai.image" . }}
imagePullPolicy: {{ .Values.image.pullPolicy }}
command:
  - sh
  - -ec
  - |
    olap/oltp/apply.sh
    python data/synthetic/generate_data.py
    python olap/seed.py
    RETRIEVER_BACKEND=lancedb python -m rag.pipeline --ingest
    {{- if .Values.seed.pgvector }}
    RETRIEVER_BACKEND=pgvector python -m rag.pipeline --ingest
    {{- end }}
    {{- include "ccai.markJob" (dict "root" . "job" "seed") | nindent 4 }}
{{ include "ccai.appEnv" . }}
{{- with .Values.seed.resources }}
resources:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .Values.securityContext }}
securityContext:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with concat (include "ccai.lanceMount" . | fromYamlArray) .Values.dataJob.extraVolumeMounts }}
volumeMounts:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end }}
