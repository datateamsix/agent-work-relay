from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

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

    ledger = subparsers.add_parser("ledger", help="Print the append-only ledger")
    ledger.add_argument("--db", type=Path, default=Path(".ewb/demo.db"))
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
            idempotency_key="EWB-GT-001-demo",
        )
        print(json.dumps(receipt.to_dict(), indent=2))
        return

    if args.command == "ledger":
        store = SQLiteStateStore(args.db)
        entries = [entry.to_dict() for entry in store.list_ledger(args.work_order_id)]
        print(json.dumps(entries, indent=2))
        return

    if args.command == "mcp":
        from .transports.mcp_server import run

        run()
