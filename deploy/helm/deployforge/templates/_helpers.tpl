{{- define "deployforge.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "deployforge.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "deployforge.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "deployforge.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "deployforge.labels" -}}
helm.sh/chart: {{ include "deployforge.chart" . }}
app.kubernetes.io/name: {{ include "deployforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "deployforge.selectorLabels" -}}
app.kubernetes.io/name: {{ include "deployforge.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "deployforge.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "deployforge.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "deployforge.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "deployforge.podSecurityContext" -}}
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{- define "deployforge.containerSecurityContext" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  capabilities:
    drop:
      - ALL
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{- define "deployforge.databaseEnv" -}}
- name: DEPLOYFORGE_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret | quote }}
      key: {{ .Values.database.urlKey | quote }}
{{- end -}}
