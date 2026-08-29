#!/usr/bin/env bash
# Provision the Agent Work Relay Cloud Run MCP server in an operator-selected
# GCP project.
#
# This is the operator path. It installs the Google Cloud SDK if needed,
# opens a browser for gcloud login, creates the isolated AWR identity, stores
# the Cursor API key in Secret Manager, deploys the broker, fixes the
# Cloud Run URL / OAuth audience chicken-and-egg, imports Firestore indexes,
# and smokes /healthz plus an unauthenticated /mcp 401.
#
# Auth0 (or WorkOS) cannot be created from this script. After the service URL
# exists, finish the authorization-server steps in docs/AUTH.md.
#
# Usage:
#   PROJECT_ID=your-awr-project \
#   AWR_REPOSITORY_URL=https://github.com/your-org/your-repo \
#   ./scripts/provision_cloud_run.sh --issuer https://your-tenant.auth0.com/
#
# In Google Cloud Shell, add --skip-auth to reuse the active gcloud identity.
#
# The Cursor API key is read from the terminal or CURSOR_API_KEY_FILE.
# It is never printed.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-central1}"
FIRESTORE_DATABASE="${FIRESTORE_DATABASE:-(default)}"
FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-${REGION}}"
SERVICE="${SERVICE:-agent-work-relay}"
RUNTIME_SA="${RUNTIME_SA:-awr-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
CURSOR_SECRET="${CURSOR_SECRET:-awr-cursor-api-key}"
REPOSITORY_URL="${AWR_REPOSITORY_URL:-}"
BASE_REF="${AWR_BASE_REF:-main}"
ISSUER="${AWR_OAUTH_ISSUER:-}"
JWKS_URL="${AWR_OAUTH_JWKS_URL:-}"
CURSOR_KEY_FILE="${CURSOR_API_KEY_FILE:-}"
ASSUME_YES=0
SKIP_GCLOUD_INSTALL=0
SKIP_AUTH=0
NO_LAUNCH_BROWSER=0

usage() {
  cat <<'EOF'
Provision Agent Work Relay on Cloud Run.

Required environment:
  PROJECT_ID              Existing, billing-enabled GCP project
  AWR_REPOSITORY_URL      HTTPS GitHub repository Cursor will work against

Options:
  --issuer URL            Auth0/WorkOS issuer, e.g. https://tenant.auth0.com/
  --jwks-url URL          Optional JWKS URL (default: <issuer>/.well-known/jwks.json)
  --cursor-key-file PATH  File containing the Cursor API key (no trailing notes)
  --yes                   Do not prompt except for missing secrets / browser login
  --skip-gcloud-install   Fail if gcloud is missing instead of installing it
  --skip-auth             Reuse the current gcloud account (still checks project)
  --no-launch-browser     Print a login URL instead of opening a browser
  -h, --help              Show this help

Environment:
  PROJECT_ID, REGION, FIRESTORE_DATABASE, FIRESTORE_LOCATION,
  AWR_REPOSITORY_URL, AWR_BASE_REF, AWR_OAUTH_ISSUER, AWR_OAUTH_JWKS_URL,
  CURSOR_API_KEY_FILE
EOF
}

log() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

confirm() {
  local prompt="$1"
  if [[ "${ASSUME_YES}" == "1" ]]; then
    return 0
  fi
  local reply
  read -r -p "${prompt} [y/N] " reply
  [[ "${reply}" == "y" || "${reply}" == "Y" ]]
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issuer)
      ISSUER="${2:-}"
      shift 2
      ;;
    --jwks-url)
      JWKS_URL="${2:-}"
      shift 2
      ;;
    --cursor-key-file)
      CURSOR_KEY_FILE="${2:-}"
      shift 2
      ;;
    --yes)
      ASSUME_YES=1
      shift
      ;;
    --skip-gcloud-install)
      SKIP_GCLOUD_INSTALL=1
      shift
      ;;
    --skip-auth)
      SKIP_AUTH=1
      shift
      ;;
    --no-launch-browser)
      NO_LAUNCH_BROWSER=1
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

require_https_url() {
  local value="$1"
  local name="$2"
  [[ "${value}" == https://* ]] || die "${name} must be an https:// URL."
}

host_from_url() {
  local url="$1"
  url="${url#https://}"
  url="${url#http://}"
  printf '%s\n' "${url%%/*}"
}

validate_operator_config() {
  [[ -n "${PROJECT_ID}" ]] || die "Set PROJECT_ID to an existing, billing-enabled AWR GCP project."
  [[ -n "${REPOSITORY_URL}" ]] || die "Set AWR_REPOSITORY_URL to the GitHub repository Cursor will work against."
  require_https_url "${REPOSITORY_URL}" "AWR_REPOSITORY_URL"
  [[ "${REPOSITORY_URL}" == https://github.com/* ]] || die "AWR_REPOSITORY_URL must be an https://github.com/ URL."
}

ensure_gcloud() {
  if command -v gcloud >/dev/null 2>&1; then
    log "gcloud is already on PATH: $(command -v gcloud)"
    return 0
  fi
  [[ "${SKIP_GCLOUD_INSTALL}" == "1" ]] && die "gcloud is not installed."

  log "gcloud is not installed."
  if ! confirm "Install the Google Cloud SDK for this user?"; then
    die "Install gcloud from https://cloud.google.com/sdk/docs/install and re-run."
  fi

  local os arch tarball
  os="$(uname -s)"
  arch="$(uname -m)"
  case "${os}-${arch}" in
    Linux-x86_64) tarball="google-cloud-cli-linux-x86_64.tar.gz" ;;
    Linux-aarch64) tarball="google-cloud-cli-linux-arm.tar.gz" ;;
    Darwin-arm64) tarball="google-cloud-cli-darwin-arm.tar.gz" ;;
    Darwin-x86_64) tarball="google-cloud-cli-darwin-x86_64.tar.gz" ;;
    *) die "Unsupported platform ${os} ${arch}. Install gcloud manually." ;;
  esac

  local sdk_parent="${HOME}/.local"
  mkdir -p "${sdk_parent}"
  local archive
  archive="$(mktemp)"
  log "Downloading ${tarball}"
  curl -fsSL "https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/${tarball}" -o "${archive}"
  rm -rf "${sdk_parent}/google-cloud-sdk"
  tar -C "${sdk_parent}" -xzf "${archive}"
  rm -f "${archive}"
  "${sdk_parent}/google-cloud-sdk/install.sh" --quiet --usage-reporting false --path-update false --command-completion false
  export PATH="${sdk_parent}/google-cloud-sdk/bin:${PATH}"
  command -v gcloud >/dev/null 2>&1 || die "gcloud install finished but the binary is not on PATH."
  log "Installed gcloud to ${sdk_parent}/google-cloud-sdk"
  log "Add this to your shell profile: export PATH=\"${sdk_parent}/google-cloud-sdk/bin:\$PATH\""
}

authenticate() {
  if [[ "${SKIP_AUTH}" == "1" ]]; then
    log "Skipping login; using the current gcloud account."
  else
    local login_flags=(--brief --update-adc)
    if [[ "${NO_LAUNCH_BROWSER}" == "1" ]]; then
      login_flags+=(--no-launch-browser)
      log "Opening a device-code login. Complete it in a browser, then return here."
    else
      log "Opening a browser for Google Cloud login. Use an owner or editor of ${PROJECT_ID}."
    fi
    gcloud auth login "${login_flags[@]}"
  fi
  local account
  account="$(gcloud config get-value account 2>/dev/null || true)"
  [[ -n "${account}" && "${account}" != "(unset)" ]] || die "No active gcloud account. Re-run without --skip-auth."
  log "Authenticated as ${account}"
}

select_project() {
  gcloud config set project "${PROJECT_ID}" >/dev/null
  local project_number
  project_number="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' 2>/dev/null || true)"
  [[ -n "${project_number}" ]] || die "Cannot read project ${PROJECT_ID}. Check that this account has access."
  log "Using project ${PROJECT_ID} (${project_number}) in ${REGION}"
}

prompt_issuer() {
  if [[ -z "${ISSUER}" ]]; then
    if [[ "${ASSUME_YES}" == "1" ]]; then
      die "Set --issuer or AWR_OAUTH_ISSUER. AWR will not start in production without OAuth."
    fi
    cat <<'EOF'

AWR is an OAuth 2.1 resource server. It does not host login.
Create a dedicated Auth0 (or WorkOS) tenant that is not the PreM3 app, then
enter the issuer URL. See docs/AUTH.md.

EOF
    read -r -p "OAuth issuer (https://your-tenant.auth0.com/): " ISSUER
  fi
  ISSUER="${ISSUER%/}/"
  require_https_url "${ISSUER}" "OAuth issuer"
  if [[ -z "${JWKS_URL}" ]]; then
    JWKS_URL="${ISSUER}.well-known/jwks.json"
  fi
  require_https_url "${JWKS_URL}" "JWKS URL"
  log "OAuth issuer: ${ISSUER}"
}

secret_has_version() {
  local names
  names="$(gcloud secrets versions list "${CURSOR_SECRET}" --project "${PROJECT_ID}" --format='value(name)' 2>/dev/null || true)"
  [[ -n "${names}" ]]
}

store_cursor_secret() {
  if secret_has_version; then
    log "Secret ${CURSOR_SECRET} already has a version. Not replacing it."
    return 0
  fi
  local tmp
  tmp="$(mktemp)"
  chmod 600 "${tmp}"
  cleanup_tmp() { rm -f "${tmp}"; }
  trap cleanup_tmp EXIT
  if [[ -n "${CURSOR_KEY_FILE}" ]]; then
    [[ -f "${CURSOR_KEY_FILE}" ]] || die "Cursor key file not found: ${CURSOR_KEY_FILE}"
    tr -d '\r\n' <"${CURSOR_KEY_FILE}" >"${tmp}"
  else
    [[ "${ASSUME_YES}" == "1" ]] && die "Secret ${CURSOR_SECRET} is empty. Pass --cursor-key-file or create a version first."
    log "Paste the Cursor API key, then press Enter. It will not be echoed."
    read -r -s key
    printf '\n'
    [[ -n "${key}" ]] || die "Cursor API key was empty."
    printf '%s' "${key}" >"${tmp}"
    unset key
  fi
  [[ -s "${tmp}" ]] || die "Cursor API key file was empty."
  gcloud secrets versions add "${CURSOR_SECRET}" --data-file="${tmp}" --project "${PROJECT_ID}" >/dev/null
  rm -f "${tmp}"
  trap - EXIT
  log "Stored a new version of ${CURSOR_SECRET}."
}

existing_service_url() {
  gcloud run services describe "${SERVICE}" \
    --region "${REGION}" \
    --project "${PROJECT_ID}" \
    --format='value(status.url)' 2>/dev/null || true
}

deploy_revision() {
  local public_base="$1"
  local audience="$2"
  local allowed_hosts="$3"
  log "Deploying ${SERVICE} (source build). This can take several minutes."
  PROJECT_ID="${PROJECT_ID}" \
    REGION="${REGION}" \
    FIRESTORE_DATABASE="${FIRESTORE_DATABASE}" \
    RUNTIME_SA="${RUNTIME_SA}" \
    CURSOR_SECRET="${CURSOR_SECRET}" \
    AWR_PUBLIC_BASE_URL="${public_base}" \
    AWR_OAUTH_ISSUER="${ISSUER}" \
    AWR_OAUTH_AUDIENCE="${audience}" \
    AWR_OAUTH_JWKS_URL="${JWKS_URL}" \
    AWR_REPOSITORY_URL="${REPOSITORY_URL}" \
    AWR_BASE_REF="${BASE_REF}" \
    AWR_ALLOWED_HOSTS="${allowed_hosts}" \
    "${ROOT}/deploy/gcp_deploy.sh"
}

import_indexes() {
  log "Applying Firestore indexes and field exemptions"
  PROJECT_ID="${PROJECT_ID}" \
    FIRESTORE_DATABASE="${FIRESTORE_DATABASE}" \
    "${ROOT}/deploy/apply_firestore_indexes.sh"
}

smoke_public() {
  local base="$1"
  log "Running public smoke checks against ${base}"
  "${ROOT}/scripts/smoke_test.sh" "${base}"
}

print_finish() {
  local url="$1"
  cat <<EOF

Provisioned ${SERVICE}

  Health:    ${url}/healthz
  MCP:       ${url}/mcp
  Metadata:  ${url}/.well-known/oauth-protected-resource/mcp
  Identity:  ${RUNTIME_SA}
  Issuer:    ${ISSUER}
  Audience:  ${url}/mcp

Auth0 / ChatGPT (operator, not this script)
  1. Create an Auth0 API whose identifier is exactly:
       ${url}/mcp
  2. Enable RBAC and add scopes:
       awr:plan awr:read awr:refresh awr:response awr:decide awr:execute
  3. Put that identifier in the access-token aud claim.
  4. Register a ChatGPT connector client (CIMD, DCR, or predefined + PKCE S256).
  5. In ChatGPT, add a remote MCP server at ${url}/mcp and complete OAuth.
  6. Submit examples/AWR-GT-001.md through submit_prompt_for_planning.

Rollback
  gcloud run services update-traffic ${SERVICE} --region ${REGION} --to-revisions PREVIOUS=100

Do not grant awr-runtime Vertex AI, BigQuery, Cloud Storage, or project-wide
administrative roles.
EOF
}

main() {
  cd "${ROOT}"
  log "Agent Work Relay Cloud Run provisioner"
  validate_operator_config
  ensure_gcloud
  authenticate
  select_project
  prompt_issuer

  if ! confirm "Create or update AWR resources in ${PROJECT_ID} and deploy ${SERVICE}?"; then
    die "Aborted."
  fi

  log "Creating runtime identity and least-privilege IAM"
  PROJECT_ID="${PROJECT_ID}" \
    REGION="${REGION}" \
    FIRESTORE_DATABASE="${FIRESTORE_DATABASE}" \
    FIRESTORE_LOCATION="${FIRESTORE_LOCATION}" \
    RUNTIME_SA="${RUNTIME_SA}" \
    CURSOR_SECRET="${CURSOR_SECRET}" \
    "${ROOT}/deploy/gcp_setup.sh"
  store_cursor_secret

  local url audience allowed
  url="$(existing_service_url)"
  if [[ -n "${url}" ]]; then
    log "Existing service URL: ${url}"
    require_https_url "${url}" "Cloud Run service URL"
    audience="${url}/mcp"
    allowed="$(host_from_url "${url}")"
    deploy_revision "${url}" "${audience}" "${allowed}"
  else
    log "No existing Cloud Run service. First revision uses a temporary HTTPS base URL,"
    log "then the script redeploys with the assigned *.run.app host."
    local pending="https://awr-pending.invalid"
    deploy_revision "${pending}" "${pending}/mcp" "awr-pending.invalid"
    url="$(existing_service_url)"
    [[ -n "${url}" ]] || die "Deploy finished but the service URL is empty."
    require_https_url "${url}" "Cloud Run service URL"
    audience="${url}/mcp"
    allowed="$(host_from_url "${url}")"
    log "Assigned URL ${url}. Redeploying with the real public base URL and Host allow-list."
    deploy_revision "${url}" "${audience}" "${allowed}"
  fi

  import_indexes
  smoke_public "${url}"
  print_finish "${url}"
}

main "$@"
