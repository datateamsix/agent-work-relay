@response
---
awr:
  schema: awr.response/v1
  response_type: receipt.accepted
  work_order_id: <broker-issued-id>
  in_reply_to: <submitted-message-id>
  executor_run_id: null
  source_input_sha256: sha256:<digest>
  idempotency_key: <original-idempotency-key>
---

# Work accepted

- Receipt: <receipt-id>
- Work order: <work-order-id>
- Status: <accepted-status>
- Effective route: <authenticated sender → bound recipient>
- Effective authority: <plan_only|review_only|approved_execution>
- Repository and base: <URL and ref>
- Payload fingerprint: <SHA-256>
- Template and wrapper: <IDs, versions, and SHA-256 values>
- Ledger sequence: <sequence>
- Duplicate replay: <true|false>
- Next expected action: <action>
