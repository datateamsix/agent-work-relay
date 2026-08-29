from __future__ import annotations

import hashlib
import threading
import unittest
from datetime import UTC, datetime, timedelta

from lc01b_helpers import PLAN_BODY, RECIPIENT, REPOSITORY, SENDER, LifecycleHarness

from awr.contracts import WorkStatus
from awr.executors.execution import DispatchState
from awr.responses.contracts import ResponseType
from awr.service import WorkOrderValidationError
from awr.wrappers import EXECUTION_WRAPPER_ID, EXECUTION_WRAPPER_VERSION, wrap_execution


class ExecutionOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = LifecycleHarness()

    def tearDown(self) -> None:
        self.harness.close()

    def test_no_execution_without_exact_stored_approval(self) -> None:
        work_order_id = self.harness.accept_planning()
        self.harness.complete_plan(work_order_id)
        with self.assertRaisesRegex(WorkOrderValidationError, "not eligible"):
            self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.request_plan_approval(work_order_id, actor=SENDER)
        with self.assertRaisesRegex(WorkOrderValidationError, "stored decision"):
            self.harness.service.dispatch_execution(work_order_id, actor=SENDER)

    def test_stale_plan_fingerprint_is_rejected(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        with self.assertRaisesRegex(WorkOrderValidationError, "cannot authorize another"):
            self.harness.service.dispatch_execution(
                work_order_id,
                actor=SENDER,
                plan_id="PLAN-STALE",
                plan_sha256="f" * 64,
            )

    def test_executor_cannot_approve_its_own_plan(self) -> None:
        work_order_id = self.harness.accept_planning()
        self.harness.complete_plan(work_order_id)
        self.harness.service.request_plan_approval(work_order_id, actor=SENDER)
        lifecycle = self.harness.projection(work_order_id)["lifecycle"]
        with self.assertRaisesRegex(WorkOrderValidationError, "Executor identities"):
            self.harness.service.record_decision(
                decision_type="approve_plan",
                work_order_id=work_order_id,
                actor=RECIPIENT,
                target_id=str(lifecycle["plan_id"]),
                target_sha256=str(lifecycle["plan_sha256"]),
                idempotency_key="exec-approve",
                permitted_action="plan.execute",
                rationale="Executor must not approve.",
            )

    def test_execution_does_not_enter_executing_before_acknowledgement(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        dispatched = self.harness.service.dispatch_execution(work_order_id, actor=SENDER)
        self.assertEqual(dispatched["status"], WorkStatus.EXECUTION_DISPATCHED.value)
        self.assertNotEqual(dispatched["status"], WorkStatus.EXECUTING.value)
        stored = self.harness.store.get_execution_dispatch(str(dispatched["dispatch_id"]))
        assert stored is not None
        self.assertEqual(stored["state"], DispatchState.PENDING.value)
        self.assertIsNone(stored.get("provider_run_id"))

    def test_completion_does_not_close_the_work_order(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        ack = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(ack["response_type"], "execution.acknowledged")
        self.assertEqual(ack["status"], WorkStatus.EXECUTING.value)
        progress = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(progress["response_type"], "execution.progress")
        completed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(completed["response_type"], "execution.completed")
        self.assertEqual(completed["status"], WorkStatus.COMPLETION_READY.value)
        self.assertNotEqual(completed["status"], WorkStatus.COMPLETE.value)

    def test_response_never_grants_authority(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        with self.assertRaisesRegex(Exception, "never grant"):
            self.harness.render_parse(
                response_type=ResponseType.EXECUTION_COMPLETED,
                work_order_id=work_order_id,
                payload={"summary": "done"},
                actor=RECIPIENT,
                idempotency_key="forged-exec",
                authority="execute",
            )

    def test_repeated_dispatch_produces_one_provider_run(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        first = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        second = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        executor = self.harness.service.executor
        self.assertEqual(len(executor.execution_dispatches), 1)
        self.assertEqual(first["response_type"], "execution.acknowledged")
        self.assertEqual(second["response_type"], "execution.progress")

    def test_concurrent_refresh_produces_one_provider_run(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        results: list[dict[str, object]] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(6)

        def worker() -> None:
            try:
                barrier.wait()
                results.append(
                    self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)
        self.assertTrue(results)
        self.assertLessEqual(len(errors), 5)

    def test_crash_before_provider_submission(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        intent = self.harness.service.dispatch_execution(work_order_id, actor=SENDER)
        self.assertEqual(intent["status"], WorkStatus.EXECUTION_DISPATCHED.value)
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 0)
        ack = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(ack["response_type"], "execution.acknowledged")
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)

    def test_ambiguous_timeout_marks_reconciliation(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.executor.force_ambiguous = True
        result = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(result["error"], "RECONCILIATION_REQUIRED")
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 0)
        stored = self.harness.store.list_execution_dispatches(work_order_id)[-1]
        self.assertEqual(stored["state"], DispatchState.RECONCILIATION_REQUIRED.value)

    def test_crash_after_acceptance_before_local_ack(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        intent = self.harness.service.dispatch_execution(work_order_id, actor=SENDER)
        dispatch = self.harness.store.get_execution_dispatch(str(intent["dispatch_id"]))
        assert dispatch is not None
        acknowledgement = self.harness.service.executor.submit_for_execution(
            self.harness.service._to_execution_dispatch(dispatch)
        )
        now = datetime.now(UTC).isoformat()
        with self.harness.store.lock_work_order(work_order_id) as session:
            current = session.get_execution_dispatch(str(intent["dispatch_id"]))
            assert current is not None
            current.update(
                {
                    "state": DispatchState.PROVIDER_ACCEPTED.value,
                    "provider_agent_id": acknowledgement.executor_agent_id,
                    "provider_run_id": acknowledgement.executor_run_id,
                    "updated_at": now,
                }
            )
            session.update_execution_dispatch(current)
        self.assertEqual(
            self.harness.projection(work_order_id)["status"],
            WorkStatus.EXECUTION_DISPATCHED.value,
        )
        ack = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(ack["response_type"], "execution.acknowledged")
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)

    def test_expired_lease_recovery(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        intent = self.harness.service.dispatch_execution(work_order_id, actor=SENDER)
        expired = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with self.harness.store.lock_work_order(work_order_id) as session:
            current = session.get_execution_dispatch(str(intent["dispatch_id"]))
            assert current is not None
            current.update(
                {
                    "state": DispatchState.LEASED.value,
                    "lease_owner": "stale-worker",
                    "lease_expires_at": expired,
                    "updated_at": expired,
                }
            )
            session.update_execution_dispatch(current)
        ack = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(ack["response_type"], "execution.acknowledged")
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)

    def test_repeated_terminal_refresh_returns_original_receipt(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        completed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        replay = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(completed["response_type"], "execution.completed")
        self.assertEqual(replay["content_sha256"], completed["content_sha256"])
        self.assertTrue(replay["duplicate"])
        self.assertEqual(len(self.harness.service.executor.execution_dispatches), 1)

    def test_later_refinement_creates_distinct_attempt(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        first = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        completed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.request_completion_review(work_order_id, actor=SENDER)
        self.harness.submit(
            response_type=ResponseType.REVIEW_COMPLETED,
            work_order_id=work_order_id,
            payload={"outcome": "REVISE", "rationale": "Need a bounded refinement."},
            actor=SENDER,
            idempotency_key="revise-1",
        )
        refined = self.harness.service.refine_implementation(work_order_id, actor=SENDER)
        self.assertEqual(refined["status"], WorkStatus.EXECUTION_DISPATCHED.value)
        self.assertEqual(refined["attempt"], 2)
        self.assertNotEqual(refined["dispatch_id"], first.get("dispatch_id"))
        first_dispatch = self.harness.store.list_execution_dispatches(work_order_id)[0]
        second_dispatch = self.harness.store.list_execution_dispatches(work_order_id)[1]
        self.assertNotEqual(
            first_dispatch["provider_idempotency_key"],
            second_dispatch["provider_idempotency_key"],
        )
        self.assertEqual(first_dispatch["plan_sha256"], second_dispatch["plan_sha256"])
        _ = completed

    def test_malformed_terminal_fails_closed(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        executor = self.harness.service.executor
        run_id = next(iter(executor._polls))
        executor._polls[run_id] = 1
        original = executor.get_execution_run

        def malformed(agent_id: str, exec_run_id: str) -> object:
            result = original(agent_id, exec_run_id)
            return type(result)(
                executor_agent_id=result.executor_agent_id,
                executor_run_id=result.executor_run_id,
                executor=result.executor,
                status=result.status,
                result="I implemented the feature without a packet.",
                duration_ms=result.duration_ms,
                git=None,
            )

        executor.get_execution_run = malformed  # type: ignore[method-assign]
        failed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(failed["error"], "MALFORMED_EXECUTOR_RESPONSE")
        self.assertEqual(failed["response_type"], "execution.failed")
        timeline = self.harness.service.get_work_order_timeline(work_order_id, actor=SENDER)
        joined = str(timeline)
        self.assertNotIn("I implemented the feature without a packet.", joined)

    def test_terminal_progress_packet_fails_closed(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        markdown, _ = self.harness.render_parse(
            response_type=ResponseType.EXECUTION_PROGRESS,
            work_order_id=work_order_id,
            payload={"message": "Still running", "percent": 80},
            actor=RECIPIENT,
            idempotency_key="term-progress",
            executor_run_id=self.harness.store.list_execution_dispatches(work_order_id)[-1][
                "provider_run_id"
            ],
        )
        executor = self.harness.service.executor
        original = executor.get_execution_run

        def progress_as_finished(agent_id: str, exec_run_id: str) -> object:
            result = original(agent_id, exec_run_id)
            return type(result)(
                executor_agent_id=result.executor_agent_id,
                executor_run_id=result.executor_run_id,
                executor=result.executor,
                status=result.status,
                result=markdown,
                duration_ms=result.duration_ms,
                git=None,
            )

        executor.get_execution_run = progress_as_finished  # type: ignore[method-assign]
        failed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(failed["error"], "MALFORMED_EXECUTOR_RESPONSE")
        self.assertEqual(self.harness.projection(work_order_id)["status"], WorkStatus.FAILED.value)

    def test_oversized_result_is_rejected(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        executor = self.harness.service.executor
        original = executor.get_execution_run

        def oversized(agent_id: str, exec_run_id: str) -> object:
            result = original(agent_id, exec_run_id)
            return type(result)(
                executor_agent_id=result.executor_agent_id,
                executor_run_id=result.executor_run_id,
                executor=result.executor,
                status=result.status,
                result="x" * (256 * 1024 + 8),
                duration_ms=result.duration_ms,
                git=result.git,
            )

        executor.get_execution_run = oversized  # type: ignore[method-assign]
        failed = self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        self.assertEqual(failed["error"], "MALFORMED_EXECUTOR_RESPONSE")

    def test_artifact_dependent_execution_is_unsupported(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        with self.harness.store.lock_work_order(work_order_id) as session:
            session.append_ledger(
                "bundle.validated",
                SENDER,
                "broker:awr",
                {"references": [{"artifact_id": "ART-1", "purpose": "design_reference"}]},
            )
        with self.assertRaisesRegex(WorkOrderValidationError, "DELIVERY_UNSUPPORTED"):
            self.harness.service.refresh_external_run(work_order_id, actor=SENDER)

    def test_wrapper_fingerprint_is_stable(self) -> None:
        wrapped = wrap_execution(
            work_order_id="AWR-1",
            plan_id="PLAN-1",
            plan_sha256="a" * 64,
            plan_content=PLAN_BODY,
            repository_url=REPOSITORY,
            base_ref="main",
            attempt=1,
        )
        again = wrap_execution(
            work_order_id="AWR-1",
            plan_id="PLAN-1",
            plan_sha256="a" * 64,
            plan_content=PLAN_BODY,
            repository_url=REPOSITORY,
            base_ref="main",
            attempt=1,
        )
        self.assertEqual(wrapped.wrapper_id, EXECUTION_WRAPPER_ID)
        self.assertEqual(wrapped.wrapper_version, EXECUTION_WRAPPER_VERSION)
        self.assertEqual(wrapped.wrapper_sha256, again.wrapper_sha256)
        self.assertIn("report_only", wrapped.markdown)
        self.assertIn("may not approve", wrapped.markdown)
        self.assertNotIn("path", wrapped.wrapper_sha256)

    def test_secrets_do_not_appear_in_ledger_or_receipts(self) -> None:
        work_order_id = self.harness.reach_ready_for_execution()
        self.harness.service.refresh_external_run(work_order_id, actor=SENDER)
        timeline = str(self.harness.service.get_work_order_timeline(work_order_id, actor=SENDER))
        self.assertNotIn("CURSOR_API_KEY", timeline)
        self.assertNotIn("Bearer ", timeline)
        self.assertNotIn("sk-", timeline)
        _ = hashlib.sha256(PLAN_BODY.encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
