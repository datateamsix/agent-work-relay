#!/usr/bin/env bash
set -euo pipefail

# Deploys Agent Work Relay to Cloud Run. Secret values stay in Secret Manager.

PROJECT_ID="${PROJECT_ID:-modelready-m3}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-agent-work-relay}"
RUNTIME_SA="${RUNTIME_SA:-awr-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
CURSOR_SECRET="${CURSOR_SECRET:-awr-cursor-api-key}"
PUBLIC_BASE_URL="${AWR_PUBLIC_BASE_URL:-}"
OAUTH_ISSUER="${AWR_OAUTH_ISSUER:-}"
OAUTH_AUDIENCE="${AWR_OAUTH_AUDIENCE:-}"
OAUTH_JWKS_URL="${AWR_OAUTH_JWKS_URL:-}"
REPOSITORY_URL="${AWR_REPOSITORY_URL:-https://github.com/datateamsix/engineering-work-broker}"
BASE_REF="${AWR_BASE_REF:-main}"
ALLOWED_HOSTS="${AWR_ALLOWED_HOSTS:-}"

if [[ -z "${OAUTH_ISSUER}" || -z "${OAUTH_AUDIENCE}" ]]; then
  echo "Set AWR_OAUTH_ISSUER and AWR_OAUTH_AUDIENCE before deploying." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

gcloud config set project "${PROJECT_ID}"

ENV_VARS="AWR_ENV=production,AWR_AUTH_MODE=oauth,AWR_STORAGE=firestore,AWR_EXECUTOR=cursor_cloud,AWR_REPOSITORY_URL=${REPOSITORY_URL},AWR_BASE_REF=${BASE_REF},AWR_OAUTH_ISSUER=${OAUTH_ISSUER},AWR_OAUTH_AUDIENCE=${OAUTH_AUDIENCE},GOOGLE_CLOUD_PROJECT=${PROJECT_ID},FIRESTORE_DATABASE=(default)"
if [[ -n "${PUBLIC_BASE_URL}" ]]; then
  ENV_VARS="${ENV_VARS},AWR_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}"
fi
if [[ -n "${OAUTH_JWKS_URL}" ]]; then
  ENV_VARS="${ENV_VARS},AWR_OAUTH_JWKS_URL=${OAUTH_JWKS_URL}"
fi
if [[ -n "${ALLOWED_HOSTS}" ]]; then
  ENV_VARS="${ENV_VARS},AWR_ALLOWED_HOSTS=${ALLOWED_HOSTS}"
fi

# Network reachability is public so ChatGPT can complete OAuth and call /mcp.
# Application authentication is still required: /mcp and state-bearing routes
# reject missing, invalid, expired, wrong-issuer, and insufficient-scope tokens.
# Do not treat --allow-unauthenticated as an unauthenticated MCP server.
gcloud run deploy "${SERVICE}" \
  --source "${ROOT}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --service-account "${RUNTIME_SA}" \
  --allow-unauthenticated \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 20 \
  --min-instances 0 \
  --max-instances 2 \
  --timeout 300 \
  --set-env-vars "${ENV_VARS}" \
  --set-secrets "CURSOR_API_KEY=${CURSOR_SECRET}:latest"

SERVICE_URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --project "${PROJECT_ID}" --format='value(status.url)')"
REVISION="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --project "${PROJECT_ID}" --format='value(status.latestReadyRevisionName)')"

echo "Deployed ${SERVICE}"
echo "URL: ${SERVICE_URL}"
echo "Revision: ${REVISION}"
echo "MCP: ${SERVICE_URL}/mcp"
echo "Health: ${SERVICE_URL}/healthz"
echo "If AWR_PUBLIC_BASE_URL was empty, redeploy with AWR_PUBLIC_BASE_URL=${SERVICE_URL}"
