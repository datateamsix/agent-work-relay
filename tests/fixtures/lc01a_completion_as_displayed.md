@response
---
awr:
  schema: awr.response/v1
  response_type: execution.completed
  packet_id: MSG-completion-displayed
  work_order_id: AWR-00000000-0000-4000-8000-000000000001
  in_reply_to: MSG-execution-parent
  source_input_sha256: sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  sender: cursor:worker
  created_at: 2026-08-29T00:00:00+00:00
  idempotency_key: lc01a-completion-displayed
---

# Execution completion: Ship the health endpoint

## Outcome

The worker finished the requested change and the checks looked green.

## Changes

- `src/app.py` — added the handler
- `tests/test_app.py` — covered the success path

## Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| Health endpoint returns 200 | PASS | `pytest tests/test_app.py` |

## Verification

| Command or check | Result |
|---|---|
| `pytest` | passed |

## Git and delivery references

- Repository: https://github.com/example/project
- Branch: feature/health
- Pull request: none
- Deployment: not performed

## Security confirmation

No credentials or secret values are included in this packet.
