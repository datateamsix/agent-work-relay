#!/usr/bin/env bash
set -euo pipefail

# Creates the dedicated AWR runtime identity, image repository, secrets, and
# least-privilege IAM in the selected GCP project. Does not print secret values.

: "${PROJECT_ID:?Set PROJECT_ID to an existing, billing-enabled AWR GCP project.}"
REGION="${REGION:-us-central1}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-${REGION}}"
CURSOR_SECRET="${CURSOR_SECRET:-awr-cursor-api-key}"
IMAGE_REPO="${IMAGE_REPO:-awr}"
SERVICE="${SERVICE:-agent-work-relay}"

gcloud config set project "${PROJECT_ID}"

PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
[[ -n "${PROJECT_NUMBER}" ]] || {
  echo "Could not resolve project number for ${PROJECT_ID}." >&2
  exit 1
}

RUNTIME_SA="${RUNTIME_SA:-awr-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
gcloud services enable \
  run.googleapis.com \
  firestore.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iam.googleapis.com \
  --project "${PROJECT_ID}"

FIRESTORE_TYPE="$(
  gcloud firestore databases describe \
    --database "${FIRESTORE_DATABASE}" \
    --project "${PROJECT_ID}" \
    --format='value(type)' 2>/dev/null || true
)"
if [[ -z "${FIRESTORE_TYPE}" ]]; then
  gcloud firestore databases create \
    --database "${FIRESTORE_DATABASE}" \
    --location "${FIRESTORE_LOCATION}" \
    --type firestore-native \
    --delete-protection \
    --project "${PROJECT_ID}"
elif [[ "${FIRESTORE_TYPE}" != "FIRESTORE_NATIVE" ]]; then
  echo "Firestore database ${FIRESTORE_DATABASE} is ${FIRESTORE_TYPE}, not FIRESTORE_NATIVE." >&2
  exit 1
else
  FIRESTORE_DELETE_PROTECTION="$(
    gcloud firestore databases describe \
      --database "${FIRESTORE_DATABASE}" \
      --project "${PROJECT_ID}" \
      --format='value(deleteProtectionState)'
  )"
  if [[ "${FIRESTORE_DELETE_PROTECTION}" != "DELETE_PROTECTION_ENABLED" ]]; then
    gcloud firestore databases update \
      --database "${FIRESTORE_DATABASE}" \
      --delete-protection \
      --project "${PROJECT_ID}"
  fi
fi

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

if ! gcloud artifacts repositories describe "${IMAGE_REPO}" \
  --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud artifacts repositories create "${IMAGE_REPO}" \
    --repository-format docker \
    --location "${REGION}" \
    --project "${PROJECT_ID}" \
    --description "Agent Work Relay Cloud Run images"
fi

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

# Cloud Build only pushes images into this AWR Artifact Registry repository.
# The operator identity that runs gcloud run deploy must already be able to
# update agent-work-relay and act as awr-runtime. Do not grant the build
# identity project-wide run.admin, artifactregistry.writer, or
# iam.serviceAccountUser.
for attempt in 1 2 3 4 5; do
  CLOUD_BUILD_SA="$(
    gcloud builds get-default-service-account \
      --project "${PROJECT_ID}" \
      --format='value(serviceAccountEmail)' 2>/dev/null || true
  )"
  if [[ -n "${CLOUD_BUILD_SA}" ]]; then
    break
  fi
  sleep 3
done
[[ -n "${CLOUD_BUILD_SA:-}" ]] || {
  echo "Could not resolve the Cloud Build default service account for ${PROJECT_ID}." >&2
  exit 1
}

gcloud artifacts repositories add-iam-policy-binding "${IMAGE_REPO}" \
  --location "${REGION}" \
  --project "${PROJECT_ID}" \
  --member "serviceAccount:${CLOUD_BUILD_SA}" \
  --role roles/artifactregistry.writer >/dev/null

echo "AWR GCP setup is complete."
echo "Project: ${PROJECT_ID} (${PROJECT_NUMBER})"
echo "Runtime identity: ${RUNTIME_SA}"
echo "Image repository: ${REGION}-docker.pkg.dev/${PROJECT_ID}/${IMAGE_REPO}/${SERVICE}"
echo "Cursor secret: ${CURSOR_SECRET}"
echo "Firestore: ${FIRESTORE_DATABASE} (${FIRESTORE_LOCATION}, FIRESTORE_NATIVE)"
echo "Do not grant Vertex AI, BigQuery, Cloud Storage, or project-wide run.admin to AWR identities."
