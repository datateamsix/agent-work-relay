#!/usr/bin/env python3
"""Generate internally consistent AWR-GT-003 static fixtures.

Response packets are built through `parse_response_packet` and rendered with
the AS-03 runtime so `content_sha256` matches `fingerprint_packet`. Input
packets and decision requests remain compact Markdown with documented
fixture SHA-256 values.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from skill_paths import EXAMPLES_GT003, SKILL_ROOT

from awr.artifacts.contracts import ArtifactPurpose, ArtifactReference
from awr.responses import (
    RESPONSE_AUTHORITY,
    RESPONSE_SCHEMA,
    parse_response_markdown,
    parse_response_packet,
    render_response_markdown,
)
from awr.responses.canonical import fingerprint_packet

SKILL_FIXTURE_DIR = SKILL_ROOT / "assets" / "examples"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def render_packet(payload: dict[str, Any]) -> tuple[str, str]:
    packet = parse_response_packet(payload)
    markdown = render_response_markdown(packet)
    parsed = parse_response_markdown(markdown)
    digest = parsed.content_sha256 or ""
    if digest != fingerprint_packet(parsed):
        raise SystemExit(f"fingerprint mismatch for {payload['response_type']}")
    return markdown, digest


def event(
    seq: int,
    kind: str,
    event_id: str,
    work_order_id: str,
    *,
    in_reply_to: str | None = None,
    source: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "seq": seq,
        "kind": kind,
        "id": event_id,
        "work_order_id": work_order_id,
        "in_reply_to": in_reply_to,
        "source": source,
    }
    if extra:
        row.update(extra)
    return row


def main() -> int:
    work_order_id = "wo_gt003_feature_catalog"
    actor_planner = "planner:gt003.catalog"
    actor_worker = "executor:gt003.worker"
    actor_reviewer = "reviewer:gt003.review"
    actor_human = "human:gt003.owner"
    repository_url = "https://github.com/example/fixture-relay"
    base_ref = "main"
    base_sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    branch = "feature/gt003-catalog-search"
    commit = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    executor_run_id = "cursor-cloud-run-gt003-0001"
    plan_id = "plan_gt003_v1"
    input_message_id = "msg_gt003_feature_plan"
    receipt_id = "msg_gt003_receipt_feature"
    plan_message_id = "msg_gt003_plan_v1"
    execute_message_id = "msg_gt003_plan_execute"
    ack_id = "msg_gt003_exec_ack"
    progress_id = "msg_gt003_exec_progress"
    completed_id = "msg_gt003_exec_completed"
    review_input_id = "msg_gt003_completion_review"
    review_id = "rev_gt003_v1"
    review_message_id = "msg_gt003_review"
    approve_decision_id = "dec_gt003_approve_plan"
    accept_decision_id = "dec_gt003_accept_completion"

    input_feature = f"""@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  parent_work_order_id: null
  correlation_id: corr_gt003_feature_plan
  idempotency_key: awr:{work_order_id}:feature.plan:v1
  repository:
    url: {repository_url}
    base_ref: {base_ref}
    base_sha: {base_sha}
  requested_executor: cursor
  requested_authority: plan_only
---

# Catalog search

## Outcome

Published products can be searched with stable pagination.

## Context

Existing catalog query service. Do not change checkout or payment.

## Requirements

- GET /catalog/search returns matching published products
- Results are paginated and stable for a given query
- Unpublished products never appear

## Acceptance criteria

- [ ] Empty query returns an empty page
- [ ] Matching query returns published products only
- [ ] Unpublished products are excluded
- [ ] Pagination is stable for the same query

## Constraints and non-goals

- Do not introduce a new search vendor
- Do not change checkout or payment flows

## Relevant artifacts

- none

## Planning response requested

Return a compact `plan.completed` packet. Do not modify the repository.
This draft does not authorize transmission, execution, merge, or deploy.
"""
    source_input_sha256 = sha256_hex(input_feature)

    receipt_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "receipt.accepted",
        "work_order_id": work_order_id,
        "in_reply_to": input_message_id,
        "idempotency_key": f"awr:{work_order_id}:receipt.accepted:feature.plan:v1",
        "source_input_sha256": source_input_sha256,
        "created_at": "2026-08-29T12:00:00Z",
        "authority": RESPONSE_AUTHORITY,
        "payload": {
            "receipt_type": "work_order.accepted",
            "status": "PLANNING",
            "content_sha256": source_input_sha256,
            "ledger_sequence": 1,
        },
    }
    receipt_md, receipt_fp = render_packet(receipt_payload)

    plan_content = (
        "Add in-process catalog search with stable pagination for published "
        "products only. Extend the existing catalog query service. Do not "
        "introduce a new index vendor.\n\n"
        "Steps: 1) add search query parameters 2) filter unpublished products "
        "3) paginate deterministically 4) add API and unit tests.\n\n"
        "Contracts: GET /catalog/search. Tests: empty, match, unpublished, "
        "pagination stability. Residual risk: naive scan on large catalogs."
    )
    plan_body_sha = sha256_hex(plan_content)
    plan_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "plan.completed",
        "work_order_id": work_order_id,
        "in_reply_to": receipt_id,
        "idempotency_key": f"awr:{work_order_id}:plan.completed:v1",
        "source_input_sha256": source_input_sha256,
        "created_at": "2026-08-29T12:05:00Z",
        "authority": RESPONSE_AUTHORITY,
        "payload": {
            "title": "Catalog search plan",
            "content": plan_content,
            "content_sha256": plan_body_sha,
            "plan_id": plan_id,
        },
    }
    plan_md, plan_fp = render_packet(plan_payload)

    decision_request = f"""# human.decision.request
schema: awr.decision.request/v1
kind: request_plan_approval
work_order_id: {work_order_id}
plan_id: {plan_id}
plan_sha256: {plan_fp}
source_input_sha256: {source_input_sha256}
in_reply_to: {plan_message_id}
actor: {actor_human}
idempotency_key: awr:{work_order_id}:request_plan_approval:{plan_id}

Approve the exact plan identified by plan_id and plan SHA-256. This request
is not itself a stored decision. Transmit only via record_decision after the
human explicitly approves.
"""
    stored_decision = {
        "kind": "approve_plan",
        "work_order_id": work_order_id,
        "decision_id": approve_decision_id,
        "plan_id": plan_id,
        "plan_sha256": plan_fp,
        "target_id": plan_id,
        "target_sha256": plan_fp,
        "actor": actor_human,
        "rationale": "Exact plan approved for catalog search scope.",
        "idempotency_key": f"awr:{work_order_id}:approve_plan:{plan_id}",
    }

    execute_input = f"""@input
---
awr:
  schema: awr.input/v1
  intent: plan.execute
  parent_work_order_id: {work_order_id}
  correlation_id: corr_gt003_plan_execute
  idempotency_key: awr:{work_order_id}:plan.execute:{plan_id}:v1
  plan_id: {plan_id}
  plan_sha256: sha256:{plan_fp}
  approval_receipt_id: {approve_decision_id}
  repository:
    url: {repository_url}
    base_ref: {base_ref}
    base_sha: {base_sha}
  requested_executor: cursor
  requested_authority: approved_execution
---

# Execute approved plan: Catalog search

Required lifecycle bindings: parent work order, exact approved plan ID and
SHA-256, and the stored approval receipt.

## Approved scope

Add catalog search with pagination for published products only.

## Required evidence

- Changes mapped to acceptance criteria
- Commands and tests with results
- Exact before and after commits

This input does not itself grant authority. A request to execute does not
authorize merge, main-branch push, deployment, or completion acceptance.

If EX-01 orchestration tools are not listed, stop and say so.
"""
    execute_sha = sha256_hex(execute_input)

    ack_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "execution.acknowledged",
        "work_order_id": work_order_id,
        "in_reply_to": execute_message_id,
        "idempotency_key": f"awr:{work_order_id}:execution.acknowledged:{executor_run_id}",
        "source_input_sha256": execute_sha,
        "created_at": "2026-08-29T12:20:00Z",
        "authority": RESPONSE_AUTHORITY,
        "executor_run_id": executor_run_id,
        "payload": {
            "executor": "cursor:cloud",
            "executor_run_id": executor_run_id,
        },
    }
    ack_md, ack_fp = render_packet(ack_payload)

    progress_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "execution.progress",
        "work_order_id": work_order_id,
        "in_reply_to": ack_id,
        "idempotency_key": f"awr:{work_order_id}:execution.progress:{executor_run_id}:milestone1",
        "source_input_sha256": execute_sha,
        "created_at": "2026-08-29T12:35:00Z",
        "authority": RESPONSE_AUTHORITY,
        "executor_run_id": executor_run_id,
        "payload": {
            "message": (
                "Search query path and unpublished filter implemented; tests "
                "not yet green. Catalog scan may be slow on large inventories."
            ),
            "percent": 60,
        },
    }
    progress_md, progress_fp = render_packet(progress_payload)

    evidence = ArtifactReference(
        artifact_id="art_gt003_catalog_tests",
        purpose=ArtifactPurpose.OTHER_REFERENCE,
        byte_length=128,
        sha256="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        detected_media_type="text/plain",
        safe_filename="catalog-search-tests.txt",
    )
    completed_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "execution.completed",
        "work_order_id": work_order_id,
        "in_reply_to": progress_id,
        "idempotency_key": f"awr:{work_order_id}:execution.completed:{executor_run_id}",
        "source_input_sha256": execute_sha,
        "created_at": "2026-08-29T13:00:00Z",
        "authority": RESPONSE_AUTHORITY,
        "executor_run_id": executor_run_id,
        "evidence_refs": [evidence.to_dict()],
        "payload": {
            "summary": (
                "Catalog search with pagination is implemented for published "
                "products only. Changed src/catalog/query.py, src/catalog/api.py, "
                "and tests/test_catalog_search.py. Acceptance criteria passed. "
                f"Verified with `uv run pytest tests/test_catalog_search.py -q` "
                f"(4 passed). Branch {branch} at {commit}. No migrations. "
                "Residual risk: full-table scan on very large catalogs."
            ),
        },
    }
    completed_md, completed_fp = render_packet(completed_payload)

    review_input = f"""@input
---
awr:
  schema: awr.input/v1
  intent: completion.review
  parent_work_order_id: {work_order_id}
  correlation_id: corr_gt003_completion_review
  idempotency_key: awr:{work_order_id}:completion.review:{executor_run_id}:v1
  plan_id: {plan_id}
  plan_sha256: sha256:{plan_fp}
  executor_run_id: {executor_run_id}
  completion_id: {completed_id}
---

# Review catalog search completion

Compare the approved plan with the completion packet and evidence. Recommend
APPROVED, REVISE, or REJECTED. Do not close the work order.
"""
    review_input_sha = sha256_hex(review_input)

    review_payload = {
        "schema": RESPONSE_SCHEMA,
        "response_type": "review.completed",
        "work_order_id": work_order_id,
        "in_reply_to": review_input_id,
        "idempotency_key": f"awr:{work_order_id}:review.completed:{executor_run_id}:v1",
        "source_input_sha256": review_input_sha,
        "created_at": "2026-08-29T13:15:00Z",
        "authority": RESPONSE_AUTHORITY,
        "payload": {
            "outcome": "APPROVED",
            "rationale": (
                "Reported changes stay inside catalog search. Checkout and "
                "payment were not mutated. All stated acceptance criteria are "
                "covered by the referenced tests. Large-catalog scan "
                "performance remains an accepted residual risk. This review "
                "grants no close, merge, or deploy authority."
            ),
        },
    }
    review_md, review_fp = render_packet(review_payload)

    accept_request = f"""# human.decision.request
schema: awr.decision.request/v1
kind: accept_completion
work_order_id: {work_order_id}
review_id: {review_id}
completion_id: {completed_id}
plan_id: {plan_id}
in_reply_to: {review_message_id}
actor: {actor_human}
idempotency_key: awr:{work_order_id}:accept_completion:{review_id}

Human acceptance of completion. Transmit only via record_decision after the
human explicitly accepts. Agent recommendation grants no authority.
"""
    accept_decision = {
        "kind": "accept_completion",
        "work_order_id": work_order_id,
        "decision_id": accept_decision_id,
        "review_id": review_id,
        "target_id": completed_id,
        "target_sha256": completed_fp,
        "actor": actor_human,
        "rationale": "Human accepts catalog search completion after review.",
        "idempotency_key": f"awr:{work_order_id}:accept_completion:{review_id}",
    }

    timeline = [
        event(
            1,
            "work_order.accepted",
            "evt_gt003_feature_accepted",
            work_order_id,
            source="01-feature-plan.input.md",
        ),
        event(
            2,
            "receipt.accepted",
            receipt_id,
            work_order_id,
            in_reply_to=input_message_id,
            source="02-receipt-accepted.response.md",
            extra={"packet_fingerprint": receipt_fp},
        ),
        event(
            3,
            "plan.completed",
            plan_message_id,
            work_order_id,
            in_reply_to=receipt_id,
            source="03-plan-completed.response.md",
            extra={"packet_fingerprint": plan_fp, "plan_id": plan_id},
        ),
        event(
            4,
            "plan.approval_requested",
            "req_gt003_plan_approval",
            work_order_id,
            in_reply_to=plan_message_id,
            source="04-plan-approval.decision-request.md",
            extra={"plan_id": plan_id, "plan_sha256": plan_fp},
        ),
        event(
            5,
            "approve_plan",
            approve_decision_id,
            work_order_id,
            in_reply_to=plan_message_id,
            source="04-plan-approval.decision.json",
            extra={"plan_id": plan_id, "plan_sha256": plan_fp},
        ),
        event(
            6,
            "plan.execute",
            execute_message_id,
            work_order_id,
            in_reply_to=approve_decision_id,
            source="05-plan-execute.input.md",
        ),
        event(
            7,
            "execution.acknowledged",
            ack_id,
            work_order_id,
            in_reply_to=execute_message_id,
            source="06-execution-acknowledged.response.md",
            extra={"executor_run_id": executor_run_id, "packet_fingerprint": ack_fp},
        ),
        event(
            8,
            "execution.progress",
            progress_id,
            work_order_id,
            in_reply_to=ack_id,
            source="07-execution-progress.response.md",
            extra={"executor_run_id": executor_run_id, "packet_fingerprint": progress_fp},
        ),
        event(
            9,
            "execution.completed",
            completed_id,
            work_order_id,
            in_reply_to=progress_id,
            source="08-execution-completed.response.md",
            extra={"executor_run_id": executor_run_id, "packet_fingerprint": completed_fp},
        ),
        event(
            10,
            "completion.review",
            review_input_id,
            work_order_id,
            in_reply_to=completed_id,
            source="09-completion-review.input.md",
        ),
        event(
            11,
            "review.completed",
            review_message_id,
            work_order_id,
            in_reply_to=review_input_id,
            source="10-review-completed.response.md",
            extra={"review_id": review_id, "packet_fingerprint": review_fp},
        ),
        event(
            12,
            "accept_completion",
            accept_decision_id,
            work_order_id,
            in_reply_to=review_message_id,
            source="11-completion-acceptance.decision.json",
        ),
    ]

    ids = {
        "work_order_id": work_order_id,
        "plan_id": plan_id,
        "plan_sha256": plan_fp,
        "source_input_sha256": source_input_sha256,
        "execute_input_sha256": execute_sha,
        "review_input_sha256": review_input_sha,
        "executor_run_id": executor_run_id,
        "repository_url": repository_url,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "branch": branch,
        "commit": commit,
        "message_ids": {
            "feature_plan": input_message_id,
            "receipt": receipt_id,
            "plan": plan_message_id,
            "plan_execute": execute_message_id,
            "execution_acknowledged": ack_id,
            "execution_progress": progress_id,
            "execution_completed": completed_id,
            "completion_review": review_input_id,
            "review": review_message_id,
        },
        "decision_ids": {
            "approve_plan": approve_decision_id,
            "accept_completion": accept_decision_id,
        },
        "review_id": review_id,
        "review_outcome": "APPROVED",
        "actors": {
            "planner": actor_planner,
            "worker": actor_worker,
            "reviewer": actor_reviewer,
            "human": actor_human,
        },
        "generated_fields": [
            "content_sha256",
            "plan_sha256",
            "source_input_sha256",
            "packet_fingerprint",
        ],
        "generation_note": (
            "Response content_sha256 values are generated by parse_response_packet "
            "+ render_response_markdown + fingerprint_packet. Do not hand-edit them."
        ),
        "fingerprints": {
            "receipt.accepted": receipt_fp,
            "plan.completed": plan_fp,
            "execution.acknowledged": ack_fp,
            "execution.progress": progress_fp,
            "execution.completed": completed_fp,
            "review.completed": review_fp,
        },
    }

    EXAMPLES_GT003.mkdir(parents=True, exist_ok=True)
    write(EXAMPLES_GT003 / "01-feature-plan.input.md", input_feature)
    write(EXAMPLES_GT003 / "02-receipt-accepted.response.md", receipt_md)
    write(EXAMPLES_GT003 / "03-plan-completed.response.md", plan_md)
    write(EXAMPLES_GT003 / "04-plan-approval.decision-request.md", decision_request)
    write(EXAMPLES_GT003 / "04-plan-approval.decision.json", json.dumps(stored_decision, indent=2))
    write(EXAMPLES_GT003 / "05-plan-execute.input.md", execute_input)
    write(EXAMPLES_GT003 / "06-execution-acknowledged.response.md", ack_md)
    write(EXAMPLES_GT003 / "07-execution-progress.response.md", progress_md)
    write(EXAMPLES_GT003 / "08-execution-completed.response.md", completed_md)
    write(EXAMPLES_GT003 / "09-completion-review.input.md", review_input)
    write(EXAMPLES_GT003 / "10-review-completed.response.md", review_md)
    write(EXAMPLES_GT003 / "11-completion-acceptance.decision-request.md", accept_request)
    write(
        EXAMPLES_GT003 / "11-completion-acceptance.decision.json",
        json.dumps(accept_decision, indent=2),
    )
    write(EXAMPLES_GT003 / "12-expected-timeline.json", json.dumps(timeline, indent=2))
    write(EXAMPLES_GT003 / "fixture-ids.json", json.dumps(ids, indent=2))
    write(
        EXAMPLES_GT003 / "README.md",
        """# AWR-GT-003 static fixtures

Provider-neutral happy-path packets for a fictitious catalog-search feature.
Identifiers, repository URL, and commit SHAs are fixtures only.

Response `content_sha256` values are generated by the AS-03 runtime
renderer. Regenerate and verify with:

```bash
uv run python .agents/skills/agent-work-relay/scripts/generate_gt003.py
uv run python .agents/skills/agent-work-relay/scripts/validate_gt003.py
```

Do not treat these packets as live broker receipts. Do not embed credentials
or live cloud identifiers.
""",
    )
    write(
        SKILL_FIXTURE_DIR / "gt003-lineage.md",
        f"""# GT-003 lineage (generated)

work_order_id: `{work_order_id}`
plan_id: `{plan_id}`
plan_sha256: `{plan_fp}`
executor_run_id: `{executor_run_id}`
repository_url: `{repository_url}`
""",
    )
    print(f"wrote fixtures under {EXAMPLES_GT003}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
