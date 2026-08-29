from __future__ import annotations

import unittest
from dataclasses import replace

from awr.contracts import WorkStatus
from awr.lifecycle.decisions import DecisionTargetKind, DecisionType, StoredDecision
from awr.lifecycle.errors import AuthorityError, LineageError, TransitionError
from awr.lifecycle.events import LifecycleEvent
from awr.lifecycle.kernel import apply_broker_event, apply_decision, apply_response, derive_snapshot
from awr.lifecycle.transitions import CANCELLABLE, TRANSITION_TABLE, allowed_events, next_state
from awr.responses.contracts import (
    RESPONSE_AUTHORITY,
    RESPONSE_SCHEMA,
    ResponsePacket,
    ResponseType,
)

SOURCE = "a" * 64
PLAN_SHA = "b" * 64


def _packet(
    response_type: ResponseType,
    payload: dict[str, object],
    *,
    parent: str = "AWR-1",
    actor: str = "cursor:worker",
    run_id: str | None = None,
    source: str = SOURCE,
) -> ResponsePacket:
    return ResponsePacket(
        schema=RESPONSE_SCHEMA,
        response_type=response_type,
        work_order_id="AWR-1",
        in_reply_to=parent,
        idempotency_key="k1",
        source_input_sha256=source,
        created_at="2026-08-29T00:00:00+00:00",
        authority=RESPONSE_AUTHORITY,
        payload=payload,
        actor=actor,
        executor_run_id=run_id,
        content_sha256=PLAN_SHA if response_type is ResponseType.PLAN_COMPLETED else "c" * 64,
        message_id="MSG-1",
    )


def _approval() -> StoredDecision:
    return StoredDecision(
        decision_id="DEC-1",
        decision_type=DecisionType.APPROVE_PLAN,
        work_order_id="AWR-1",
        actor="human:owner",
        target_kind=DecisionTargetKind.PLAN,
        target_id="PLAN-1",
        target_sha256=PLAN_SHA,
        permitted_action="plan.execute",
        scope="restricted",
        created_at="2026-08-29T00:00:00+00:00",
        idempotency_key="approve-1",
        rationale="Approve the stored plan fingerprint.",
    )


class MinimizedTransitionTests(unittest.TestCase):
    def test_explicit_table_is_the_smallest_operational_graph(self) -> None:
        expected = {
            (WorkStatus.PLANNING, LifecycleEvent.PLAN_COMPLETED),
            (WorkStatus.PLANNING, LifecycleEvent.QUESTION_BLOCKED),
            (WorkStatus.PLAN_READY, LifecycleEvent.PLAN_APPROVAL_REQUESTED),
            (WorkStatus.WAITING_FOR_PLAN_APPROVAL, LifecycleEvent.APPROVE_PLAN),
            (WorkStatus.WAITING_FOR_PLAN_APPROVAL, LifecycleEvent.REJECT_PLAN),
            (WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.PLAN_EXECUTE),
            (WorkStatus.EXECUTION_DISPATCHED, LifecycleEvent.EXECUTION_ACKNOWLEDGED),
            (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_PROGRESS),
            (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_COMPLETED),
            (WorkStatus.EXECUTING, LifecycleEvent.EXECUTION_FAILED),
            (WorkStatus.EXECUTING, LifecycleEvent.QUESTION_BLOCKED),
            (WorkStatus.COMPLETION_READY, LifecycleEvent.COMPLETION_REVIEW),
            (WorkStatus.PLANNER_REVIEWING, LifecycleEvent.REVIEW_COMPLETED),
            (WorkStatus.WAITING_FOR_HUMAN_REVIEW, LifecycleEvent.ACCEPT_COMPLETION),
            (WorkStatus.WAITING_FOR_HUMAN_REVIEW, LifecycleEvent.REQUEST_REVISION),
            (WorkStatus.WAITING_FOR_INPUT, LifecycleEvent.QUESTION_ANSWER),
            (WorkStatus.REVISION_REQUIRED, LifecycleEvent.IMPLEMENTATION_REFINE),
        }
        self.assertEqual(set(TRANSITION_TABLE), expected)
        self.assertEqual(
            next_state(WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.PLAN_EXECUTE),
            WorkStatus.EXECUTION_DISPATCHED,
        )
        self.assertNotIn(
            (WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.EXECUTION_ACKNOWLEDGED),
            TRANSITION_TABLE,
        )

    def test_skipped_and_convenience_edges_fail(self) -> None:
        forbidden = [
            (WorkStatus.PLAN_READY, LifecycleEvent.APPROVE_PLAN),
            (WorkStatus.PLAN_READY, LifecycleEvent.PLAN_EXECUTE),
            (WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.EXECUTION_ACKNOWLEDGED),
            (WorkStatus.READY_FOR_EXECUTION, LifecycleEvent.EXECUTION_COMPLETED),
            (WorkStatus.EXECUTION_DISPATCHED, LifecycleEvent.EXECUTION_COMPLETED),
            (WorkStatus.EXECUTION_DISPATCHED, LifecycleEvent.EXECUTION_PROGRESS),
            (WorkStatus.PLANNER_REVIEWING, LifecycleEvent.ACCEPT_COMPLETION),
            (WorkStatus.COMPLETION_READY, LifecycleEvent.ACCEPT_COMPLETION),
            (WorkStatus.PLANNING, LifecycleEvent.PLAN_EXECUTE),
            (WorkStatus.COMPLETE, LifecycleEvent.CANCEL),
            (WorkStatus.FAILED, LifecycleEvent.CANCEL),
            (WorkStatus.CANCELLED, LifecycleEvent.CANCEL),
        ]
        for status, event in forbidden:
            with self.subTest(status=status, event=event), self.assertRaises(TransitionError):
                next_state(status, event)

    def test_cancel_is_a_family_rule(self) -> None:
        for status in CANCELLABLE:
            self.assertEqual(next_state(status, LifecycleEvent.CANCEL), WorkStatus.CANCELLED)
            self.assertIn(LifecycleEvent.CANCEL, allowed_events(status))
        for status in (
            WorkStatus.COMPLETE,
            WorkStatus.FAILED,
            WorkStatus.CANCELLED,
            WorkStatus.ACCEPTED,
        ):
            with self.assertRaises(TransitionError):
                next_state(status, LifecycleEvent.CANCEL)


class KernelGuardTests(unittest.TestCase):
    def test_plan_execute_does_not_enter_executing(self) -> None:
        snapshot = replace(
            derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE),
            plan_id="PLAN-1",
            plan_sha256=PLAN_SHA,
        )
        result = apply_broker_event(
            status=WorkStatus.READY_FOR_EXECUTION,
            snapshot=snapshot,
            event=LifecycleEvent.PLAN_EXECUTE,
            actor="human:owner",
            message_id="EXD-1",
            decisions=(_approval(),),
        )
        self.assertEqual(result.status, WorkStatus.EXECUTION_DISPATCHED)

    def test_ack_required_before_progress_or_completion(self) -> None:
        snapshot = derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE)
        packet = _packet(
            ResponseType.EXECUTION_COMPLETED,
            {"summary": "Done"},
            parent=snapshot.current_parent_id,
        )
        with self.assertRaises(LineageError):
            apply_response(
                status=WorkStatus.EXECUTING,
                snapshot=snapshot,
                packet=packet,
                actor="cursor:worker",
                decisions=(_approval(),),
            )

    def test_request_plan_approval_is_not_a_decision(self) -> None:
        snapshot = derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE)
        with self.assertRaises(AuthorityError):
            apply_decision(
                status=WorkStatus.PLAN_READY,
                snapshot=snapshot,
                event=LifecycleEvent.PLAN_APPROVAL_REQUESTED,
                decision=_approval(),
            )

    def test_review_does_not_close_the_work_order(self) -> None:
        snapshot = derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE)
        packet = _packet(
            ResponseType.REVIEW_COMPLETED,
            {"outcome": "APPROVED", "rationale": "Looks good"},
            actor="human:owner",
        )
        result = apply_response(
            status=WorkStatus.PLANNER_REVIEWING,
            snapshot=snapshot,
            packet=packet,
            actor="human:owner",
            decisions=(_approval(),),
        )
        self.assertEqual(result.status, WorkStatus.WAITING_FOR_HUMAN_REVIEW)
        self.assertEqual(result.snapshot.latest_review_outcome, "APPROVED")

    def test_question_blocked_enters_waiting_for_input(self) -> None:
        snapshot = derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE)
        result = apply_response(
            status=WorkStatus.PLANNING,
            snapshot=snapshot,
            packet=_packet(
                ResponseType.QUESTION_BLOCKED,
                {"questions": [{"id": "q1", "text": "Which API?"}]},
            ),
            actor="cursor:worker",
            decisions=(),
        )
        self.assertEqual(result.status, WorkStatus.WAITING_FOR_INPUT)
        self.assertEqual(result.snapshot.blocked_from, WorkStatus.PLANNING)
        answered = apply_broker_event(
            status=result.status,
            snapshot=result.snapshot,
            event=LifecycleEvent.QUESTION_ANSWER,
            actor="human:owner",
            message_id="ANS-1",
            decisions=(),
        )
        self.assertEqual(answered.status, WorkStatus.PLANNING)
        self.assertIsNone(answered.snapshot.blocked_from)
        with self.assertRaises(TransitionError):
            next_state(WorkStatus.WAITING_FOR_HUMAN_REVIEW, LifecycleEvent.QUESTION_ANSWER)

    def test_executor_identities_cannot_record_decisions(self) -> None:
        snapshot = replace(
            derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE),
            plan_id="PLAN-1",
            plan_sha256=PLAN_SHA,
            bound_agent_id="cursor:bound-run",
            executor_principals=frozenset({"cursor:worker", "cursor:bound-run"}),
        )
        for actor in ("cursor:worker", "cursor:bound-run"):
            decision = replace(_approval(), actor=actor)
            with self.subTest(actor=actor), self.assertRaisesRegex(
                AuthorityError, "cannot record human decisions"
            ):
                apply_decision(
                    status=WorkStatus.WAITING_FOR_PLAN_APPROVAL,
                    snapshot=snapshot,
                    event=LifecycleEvent.APPROVE_PLAN,
                    decision=decision,
                )

    def test_foreign_actor_and_wrong_source_fail(self) -> None:
        snapshot = derive_snapshot("AWR-1", "human:owner", "cursor:worker", SOURCE)
        with self.assertRaises(AuthorityError):
            apply_response(
                status=WorkStatus.PLANNING,
                snapshot=snapshot,
                packet=_packet(
                    ResponseType.PLAN_COMPLETED,
                    {"content": "x", "content_sha256": "d" * 64},
                    actor="intruder",
                ),
                actor="intruder",
                decisions=(),
            )
        with self.assertRaises(LineageError):
            apply_response(
                status=WorkStatus.PLANNING,
                snapshot=snapshot,
                packet=_packet(
                    ResponseType.PLAN_COMPLETED,
                    {"content": "x", "content_sha256": "d" * 64},
                    source="e" * 64,
                ),
                actor="cursor:worker",
                decisions=(),
            )


if __name__ == "__main__":
    unittest.main()
