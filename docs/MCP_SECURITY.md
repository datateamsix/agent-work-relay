# MCP server hardening

This is Agent Work Relay's mapping of common MCP hardening advice onto a
ChatGPT-reachable Cloud Run resource server. The 2026 checklist at
[clawdcontext.com](https://clawdcontext.com/en/blog/mcp-security-best-practices-complete-guide-2026)
is useful as a prompt, and wrong if applied verbatim.

AWR is not a localhost stdio sidecar. ChatGPT must reach `https://<service>/mcp`
on the public internet. Hardening is **application authentication**, not
network hiding.

## Checklist vs AWR

| Article item | AWR posture | Notes |
|---|---|---|
| Bind MCP to localhost / Unix socket | **Rejected for hosted** | Would make ChatGPT unreachable. Local stdio (`awr mcp`) stays loopback. Hosted binds `0.0.0.0` behind Cloud Run TLS. |
| OAuth 2.1 required | **Implemented** | Production refuses static tokens. JWT must be RS256, issuer, audience, exp, and an AWR scope. |
| Per-tool least privilege | **Implemented** | `TOOL_SCOPES` on every MCP tool; `awr:read` cannot plan, decide, or execute. |
| TLS / mTLS | **TLS yes, mTLS no** | Cloud Run terminates HTTPS. ChatGPT cannot present a client certificate. mTLS is not a ChatGPT connector control. |
| Rate limiting | **Implemented** | Sliding window per actor and tool. Failed auth is limited by client IP. Defaults: 30 anon, 120 auth, 20 plan, 10 execute per minute. |
| Regex "ignore previous" filters | **Rejected** | String filters are bypassable and block legitimate specs. AWR uses typed directives, wrappers, and "artifacts cannot grant authority." |
| Sanitize tool output with regex | **Rejected as primary** | Receipts already omit bytes, paths, and secrets. Regex-stripping `system:` from plan text would corrupt legitimate content. |
| Second-LLM semantic firewall | **Rejected** | AWR is a deterministic broker. Another model is a new injection surface. |
| Immutable audit log | **Implemented** | Work-order ledger plus `mcp.tool_call` / `auth.challenge` / `auth.rate_limited` JSON logs. Raw markdown is redacted. |
| Anomaly detection | **Partial** | Rate-limit and ledger sequences exist. Sequence ML is out of scope. |
| Read-only container / no-new-privileges | **Partial** | Non-root UID 1001, multi-stage image without `uv`, Cloud Run 1 CPU / 512 MiB / max 2 instances. Cloud Run does not expose Docker `security_opt`. |
| Pin supply chain | **Implemented** | `uv.lock` + `uv sync --frozen`. Skill templates are SHA-256 manifested. |
| Incident runbook | **Documented below** | |

## What the hosted server enforces

1. `/healthz` and OAuth metadata are the only public routes. They return no
   work-order data and no secrets.
2. `/mcp` and every state-bearing REST route require a Bearer token.
3. Production `AWR_AUTH_MODE=static` will not start.
4. JWT `alg` must be `RS256`. `none` and HMAC algorithms fail closed.
5. JSON/MCP bodies are capped (`AWR_JSON_BODY_MAX_BYTES`, default 768 KiB).
   Artifact bytes use the separate upload ticket and `AWR_ARTIFACT_MAX_BYTES`.
6. Security headers: `nosniff`, `DENY` framing, `no-referrer`, empty
   permissions policy, restrictive CSP. Production adds HSTS.
7. DNS-rebinding protection is on in production (`allowed_hosts`).
8. Production does not attach a local-disk artifact store. `AWR_ARTIFACT_STORAGE=gcs`
   fails closed until AWR-AS-04 ships a real GCS adapter. Artifact tools stay
   disabled in production until then.
9. Logs redact tokens, upload tickets, signed URLs, and raw prompts.

`--allow-unauthenticated` on Cloud Run means **the load balancer accepts TCP**.
It does not mean MCP tools are anonymous. See `deploy/gcp_deploy.sh`.

## Incident response

1. **Isolate** — send Cloud Run traffic to the previous revision:
   `gcloud run services update-traffic agent-work-relay --to-revisions PREVIOUS=100`.
2. **Preserve** — export Cloud Logging for `awr` JSON events and the Firestore
   `awr_` collections. Do not wipe the ledger.
3. **Analyze** — follow `mcp.tool_call` actor/tool pairs, `auth.challenge`,
   `auth.rate_limited`, and work-order ledger sequences.
4. **Contain** — rotate `awr-cursor-api-key`, revoke the Auth0 client, and
   rotate any leaked planner tokens.
5. **Remediate** — patch, redeploy, and keep the failed revision for forensics.
6. **Report** — work-order IDs, actor subjects, tool names, and timestamps.
   Never paste raw prompts, tokens, or artifact bytes into the ticket.

## Explicitly not copied from the article

- Admin panels. AWR has none.
- Shell/file tools on the MCP server. AWR exposes work-order tools only.
- Internal-only Docker networks that would hide `/mcp` from ChatGPT.
- ClawdContext / Caddy / UFW copy-paste configs. Cloud Run + OAuth is the
  hosted control.
