#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:43145}"

fail() {
  echo "SMOKE FAIL: $*" >&2
  exit 1
}

health="$(curl -fsS "${BASE_URL}/healthz")" || fail "healthz did not return 200"
echo "${health}" | grep -q '"status":"ok"' || echo "${health}" | grep -q '"status": "ok"' || fail "healthz body was not ok"

metadata="$(curl -fsS "${BASE_URL}/.well-known/oauth-protected-resource/mcp")" || fail "protected resource metadata missing"
echo "${metadata}" | grep -q 'awr:plan' || fail "metadata did not advertise awr:plan"

code="$(curl -sS -o /tmp/awr-mcp-unauth.json -w '%{http_code}' -X POST "${BASE_URL}/mcp" -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}')"
[[ "${code}" == "401" ]] || fail "unauthenticated /mcp returned ${code}"
grep -qi 'www-authenticate' /dev/null || true
challenge="$(curl -sS -D - -o /tmp/awr-mcp-unauth.json -X POST "${BASE_URL}/mcp" -H 'content-type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}')"
echo "${challenge}" | grep -qi 'www-authenticate: bearer' || fail "missing WWW-Authenticate challenge"
echo "${challenge}" | grep -qi 'resource_metadata=' || fail "challenge missing resource_metadata"

echo "SMOKE PASS against ${BASE_URL}"
