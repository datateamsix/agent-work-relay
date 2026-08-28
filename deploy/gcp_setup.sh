#!/usr/bin/env bash
set -euo pipefail

# Creates the dedicated AWR runtime identity, secrets, and least-privilege IAM
# in modelready-m3. Does not print or require secret values.

PROJECT_ID="${PROJECT_ID:-modelready-m3}"
PROJECT_NUMBER="${PROJECT_NUMBER:-912257136465}"
REGION="${REGION:-us-central1}"
RUNTIME_SA="${RUNTIME_SA:-awr-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
CURSOR_SECRET="${CURSOR_SECRET:-awr-cursor-api-key}"

gcloud config set project "${PROJECT_ID}"

gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  --project "${PROJECT_ID}"

if ! gcloud iam service-accounts describe "${RUNTIME_SA}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create awr-runtime \
    --display-name "Agent Work Relay runtime" \
    --project "${PROJECT_ID}"
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/datastore.user \
  --condition=None >/dev/null

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/logging.logWriter \
  --condition=None >/dev/null

if ! gcloud secrets describe "${CURSOR_SECRET}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${CURSOR_SECRET}" \
    --replication-policy=automatic \
    --project "${PROJECT_ID}"
  echo "Created empty secret ${CURSOR_SECRET}. Add the Cursor API key with:"
  echo "  gcloud secrets versions add ${CURSOR_SECRET} --data-file=-"
fi

gcloud secrets add-iam-policy-binding "${CURSOR_SECRET}" \
  --member "serviceAccount:${RUNTIME_SA}" \
  --role roles/secretmanager.secretAccessor \
  --project "${PROJECT_ID}" >/dev/null

echo "AWR GCP setup is complete."
echo "Runtime identity: ${RUNTIME_SA}"
echo "Cursor secret: ${CURSOR_SECRET}"
echo "Do not grant Vertex AI, BigQuery, or Cloud Storage roles to awr-runtime."
