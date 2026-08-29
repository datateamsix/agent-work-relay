@response
---
awr:
  schema: awr.response/v1
  response_type: receipt.accepted
  work_order_id: <broker-issued-id>
  in_reply_to: <submitted-message-id>
  idempotency_key: awr:<work-order-id>:receipt.accepted:v1
  source_input_sha256: sha256:<digest>
  created_at: <rfc3339-timestamp>
  authority: report_only
  content_sha256: sha256:<canonical-packet-digest>
  receipt_type: <receipt_type>
  status: <accepted-status>
  body_sha256: sha256:<payload-digest>
---

# Work accepted

Required protocol: `schema`, `response_type`, `work_order_id`, `in_reply_to`,
`idempotency_key`, `source_input_sha256`, `created_at`, `authority`.
Required lifecycle evidence: receipt type, status, payload fingerprint.

- Receipt type: <receipt_type>
- Status: <accepted-status>
- Ledger sequence: <sequence>
- Duplicate replay: <true|false>
- Next expected action: <action>

This receipt reports broker state. It grants no execution or merge authority.
