# Authorization server selection

AWR is an OAuth 2.1 **resource server**. It does not mint ChatGPT access tokens
and it does not host a login UI. ChatGPT (or Codex) talks to a managed
authorization server, then presents the resulting bearer token to `/mcp`.

## ChatGPT MCP requirements

The current OpenAI MCP authentication contract requires:

- RFC 9728 protected-resource metadata on the MCP server
- RFC 8414 or OpenID discovery on the authorization server
- authorization-code + PKCE `S256`
- the `resource` parameter echoed into the access token audience
- CIMD, DCR, or a predefined OAuth client
- `WWW-Authenticate` challenges that point at the resource metadata URL

AWR publishes metadata at `/.well-known/oauth-protected-resource` and
`/.well-known/oauth-protected-resource/mcp`. It verifies signature, issuer,
audience/resource, expiration, and scopes. It never treats a custom API key as
ChatGPT authentication.

## Why not PreM3 Clerk

Clerk is a good user-login product for PreM3. It is not a drop-in MCP
authorization server for this slice:

- Clerk access tokens are issued for the Clerk Frontend API, not an MCP
  resource URL. There is no first-class RFC 8707 `resource` echo into `aud`.
- Clerk does not advertise Client ID Metadata Documents or RFC 7591 dynamic
  client registration in the form ChatGPT expects.
- Reusing PreM3 Clerk application code or tenants would couple AWR to PreM3
  identity, logs, and secrets, which this project explicitly forbids.

AWR therefore keeps a provider-neutral JWT verifier and a configurable issuer.

## Recommended managed authorization server

Use **Auth0** (or WorkOS AuthKit if the operator already standardizes on it).

Minimum Auth0 operator setup:

1. Create a dedicated Auth0 tenant or application that is not the PreM3 app.
2. Create an API whose identifier is the AWR MCP resource URL,
   for example `https://agent-work-relay-xxxxx.run.app/mcp`.
3. Enable RBAC and add scopes `awr:plan`, `awr:read`, `awr:refresh`,
   `awr:response`, `awr:decide`, and `awr:execute`.
4. Create a ChatGPT connector client:
   - prefer CIMD if the tenant supports `client_id_metadata_document_supported`;
   - otherwise enable DCR or register the ChatGPT redirect URI as a predefined
     confidential/public client.
5. Require PKCE `S256` and authorization-code only. Disable implicit grant.
6. Configure the API to put the resource identifier in the access-token `aud`
   claim.
7. Point AWR at:

```text
AWR_AUTH_MODE=oauth
AWR_OAUTH_ISSUER=https://<tenant>.auth0.com/
AWR_OAUTH_AUDIENCE=https://<awr-service>/mcp
AWR_OAUTH_JWKS_URL=https://<tenant>.auth0.com/.well-known/jwks.json
AWR_PUBLIC_BASE_URL=https://<awr-service>
```

Do not invent a self-hosted identity provider inside AWR.

## Local tests

`AWR_AUTH_MODE=static` exists only for local tests and recording demos. The
production profile refuses to start if a static token is configured.
