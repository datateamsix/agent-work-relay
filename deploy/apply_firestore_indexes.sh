#!/usr/bin/env bash
# Apply AWR Firestore composite indexes and field exemptions with real
# gcloud create/update commands. There is no supported bulk-import
# command for composite indexes.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-modelready-m3}"
DATABASE="${FIRESTORE_DATABASE:-(default)}"

already_exists() {
  local output="$1"
  grep -qiE 'already exists|ALREADY_EXISTS|conflicts with an existing' <<<"${output}"
}

create_composite() {
  local group="$1"
  shift
  local output
  set +e
  output="$(
    gcloud firestore indexes composite create \
      --project "${PROJECT_ID}" \
      --database "${DATABASE}" \
      --collection-group "${group}" \
      --query-scope COLLECTION \
      --async \
      "$@" \
      2>&1
  )"
  local status=$?
  set -e
  if [[ "${status}" -eq 0 ]] || already_exists "${output}"; then
    echo "Index ready or already present: ${group} $*"
    return 0
  fi
  echo "${output}" >&2
  return "${status}"
}

exempt_field() {
  local group="$1"
  local field="$2"
  local output
  set +e
  output="$(
    gcloud firestore indexes fields update "${field}" \
      --project "${PROJECT_ID}" \
      --database "${DATABASE}" \
      --collection-group "${group}" \
      --disable-indexes \
      2>&1
  )"
  local status=$?
  set -e
  if [[ "${status}" -eq 0 ]] || already_exists "${output}"; then
    echo "Field exemption applied: ${group}.${field}"
    return 0
  fi
  echo "${output}" >&2
  return "${status}"
}

create_composite ledger --field-config=field-path=sequence,order=ASCENDING
create_composite awr_work_orders --field-config=field-path=created_at,order=ASCENDING

exempt_field awr_work_orders markdown
exempt_field awr_work_orders content_sha256
exempt_field awr_work_orders wrapper_sha256
exempt_field awr_work_orders idempotency_key
exempt_field ledger payload

echo "AWR Firestore indexes and field exemptions applied."
