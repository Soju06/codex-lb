{{/*
Expand the name of the chart.
*/}}
{{- define "codex-lb.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "codex-lb.fullname" -}}
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

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "codex-lb.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "codex-lb.labels" -}}
helm.sh/chart: {{ include "codex-lb.chart" . }}
{{ include "codex-lb.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels — IMMUTABLE after first deploy (name + instance ONLY, never version/chart)
*/}}
{{- define "codex-lb.selectorLabels" -}}
app.kubernetes.io/name: {{ include "codex-lb.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
ServiceAccount name resolution
*/}}
{{- define "codex-lb.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "codex-lb.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret name — returns existingSecret or generated name
*/}}
{{- define "codex-lb.secretName" -}}
{{- if .Values.auth.existingSecret }}
{{- .Values.auth.existingSecret }}
{{- else }}
{{- include "codex-lb.fullname" . }}
{{- end }}
{{- end }}

{{/*
Database URL secret name — may differ from the app secret when using a dedicated external DB secret.
*/}}
{{- define "codex-lb.databaseUrlSecretName" -}}
{{- if and (not .Values.postgresql.enabled) .Values.externalDatabase.existingSecret }}
{{- .Values.externalDatabase.existingSecret }}
{{- else }}
{{- include "codex-lb.secretName" . }}
{{- end }}
{{- end }}

{{/*
Database URL — TWO code paths:
  1. postgresql.enabled: synthesize URL from sub-chart values
  2. external: use externalDatabase.url or synthesize from discrete fields
This is used in secret.yaml to populate the database-url secret key.
*/}}
{{- define "codex-lb.databaseUrl" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "postgresql+asyncpg://%s:%s@%s-postgresql:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password .Release.Name .Values.postgresql.auth.database }}
{{- else if .Values.externalDatabase.url }}
{{- .Values.externalDatabase.url }}
{{- else if and .Values.externalDatabase.host .Values.externalDatabase.user .Values.externalDatabase.database }}
{{- printf "postgresql+asyncpg://%s@%s:%v/%s" .Values.externalDatabase.user .Values.externalDatabase.host (.Values.externalDatabase.port | default 5432) .Values.externalDatabase.database }}
{{- else }}
{{- "" }}
{{- end }}
{{- end }}

{{/*
Migration hook phases — default to pre-install when DB credentials are already available without ExternalSecrets materialization.
*/}}
{{- define "codex-lb.migrationHookPhases" -}}{{- if or .Values.externalSecrets.enabled .Values.postgresql.enabled -}}post-install,pre-upgrade{{- else -}}pre-install,pre-upgrade{{- end -}}{{- end }}

{{/*
Migration job service account — pre-install hooks cannot rely on chart-created ServiceAccounts.
Use an operator-provided existing SA when explicitly configured; otherwise fall back to default.
*/}}
{{- define "codex-lb.migrationServiceAccountName" -}}{{- if and .Values.externalSecrets.enabled .Values.serviceAccount.create -}}{{- include "codex-lb.serviceAccountName" . -}}{{- else if .Values.serviceAccount.name -}}{{- .Values.serviceAccount.name -}}{{- else -}}default{{- end -}}{{- end }}

{{/*
Image string — resolves registry/repository:tag with optional digest override
*/}}
{{- define "codex-lb.image" -}}
{{- $registry := .Values.global.imageRegistry | default .Values.image.registry }}
{{- $repository := .Values.image.repository }}
{{- $tag := .Values.image.tag | default .Chart.AppVersion }}
{{- if .Values.image.digest }}
{{- printf "%s/%s@%s" $registry $repository .Values.image.digest }}
{{- else }}
{{- printf "%s/%s:%s" $registry $repository $tag }}
{{- end }}
{{- end }}
