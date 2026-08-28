from __future__ import annotations

import argparse
import json
import time
from collections.abc import Sequence
from pathlib import Path

from .contracts import PlanPacket
from .factory import build_service
from .storage.sqlite import SQLiteStateStore

_DEMO_PROMPT = """@ewb feature.plan

# Add a project health endpoint

Review the repository and produce an implementation plan. Do not edit files.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ewb", description="Engineering Work Broker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="Run EWB-GT-001 with a recording Cursor adapter")
    demo.add_argument("--db", type=Path, default=Path(".ewb/demo.db"))

    refresh = subparsers.add_parser("refresh", help="Refresh a planning run")
    refresh.add_argument("work_order_id")
    refresh.add_argument("--db", type=Path, default=Path(".ewb/ewb.db"))

    plan = subparsers.add_parser("plan", help="Print a completed plan packet")
    plan.add_argument("work_order_id")
    plan.add_argument("--db", type=Path, default=Path(".ewb/ewb.db"))

    submit = subparsers.add_parser("submit", help="Submit a decorated Markdown file")
    submit.add_argument("prompt_file", type=Path)
    submit.add_argument("--sender", default="cli:operator")
    submit.add_argument("--recipient", default="cursor:cloud")
    submit.add_argument("--repository-url")
    submit.add_argument("--base-ref")
    submit.add_argument("--idempotency-key")
    submit.add_argument("--db", type=Path, default=Path(".ewb/ewb.db"))

    wait = subparsers.add_parser("wait", help="Wait for a planning run to finish")
    wait.add_argument("work_order_id")
    wait.add_argument("--interval", type=float, default=5.0)
    wait.add_argument("--timeout", type=float, default=900.0)
    wait.add_argument("--db", type=Path, default=Path(".ewb/ewb.db"))

    ledger = subparsers.add_parser("ledger", help="Print the append-only ledger")
    ledger.add_argument("--db", type=Path, default=Path(".ewb/ewb.db"))
    ledger.add_argument("--work-order-id")

    subparsers.add_parser("mcp", help="Run the MCP v2 server")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    if args.command == "demo":
        service = build_service(args.db)
        receipt = service.submit_prompt_for_planning(
            markdown=_DEMO_PROMPT,
            sender="chatgpt:product-planner",
            recipient="cursor:backend",
            repository_url="https://github.com/example/example",
            base_ref="main",
            idempotency_key="EWB-GT-001-demo",
        )
        plan_packet = service.refresh_planning(receipt.work_order_id)
        print(
            json.dumps(
                {"submission": receipt.to_dict(), "planning": plan_packet.to_dict()},
                indent=2,
            )
        )
        return

    if args.command == "refresh":
        service = build_service(args.db)
        result = service.refresh_planning(args.work_order_id)
        print(json.dumps(result.to_dict(), indent=2))
        return

    if args.command == "plan":
        service = build_service(args.db)
        print(json.dumps(service.get_plan(args.work_order_id).to_dict(), indent=2))
        return

    if args.command == "submit":
        service = build_service(args.db)
        receipt = service.submit_prompt_for_planning(
            markdown=args.prompt_file.read_text(encoding="utf-8"),
            sender=args.sender,
            recipient=args.recipient,
            repository_url=args.repository_url,
            base_ref=args.base_ref,
            idempotency_key=args.idempotency_key,
        )
        print(json.dumps(receipt.to_dict(), indent=2))
        return

    if args.command == "wait":
        if args.interval <= 0 or args.timeout <= 0:
            raise SystemExit("--interval and --timeout must be positive.")
        service = build_service(args.db)
        deadline = time.monotonic() + args.timeout
        while True:
            result = service.refresh_planning(args.work_order_id)
            if isinstance(result, PlanPacket):
                print(json.dumps(result.to_dict(), indent=2))
                return
            if result.status.value == "FAILED":
                print(json.dumps(result.to_dict(), indent=2))
                raise SystemExit(1)
            if time.monotonic() >= deadline:
                print(json.dumps(result.to_dict(), indent=2))
                raise SystemExit("Timed out waiting for the planning run.")
            time.sleep(args.interval)

    if args.command == "ledger":
        store = SQLiteStateStore(args.db)
        entries = [entry.to_dict() for entry in store.list_ledger(args.work_order_id)]
        print(json.dumps(entries, indent=2))
        return

    if args.command == "mcp":
        from .transports.mcp_server import run

        run()
