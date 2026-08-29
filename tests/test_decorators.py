from __future__ import annotations

import unittest

from awr.contracts import WorkAction, WorkKind
from awr.decorators import DirectiveError, parse_directive


class DirectiveTests(unittest.TestCase):
    def test_feature_plan(self) -> None:
        directive = parse_directive("@awr feature.plan\n\n# Feature")
        self.assertEqual(directive.kind, WorkKind.FEATURE)
        self.assertEqual(directive.action, WorkAction.PLAN)
        self.assertIsNone(directive.parent_work_order_id)

    def test_refinement_requires_parent(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "requires parent"):
            parse_directive("@awr refinement.plan\n\nTighten the tests")

    def test_refinement_accepts_parent(self) -> None:
        directive = parse_directive("@awr refinement.plan parent=AWR-123\n\nTighten tests")
        self.assertEqual(directive.parent_work_order_id, "AWR-123")

    def test_loose_keyword_does_not_route(self) -> None:
        with self.assertRaises(DirectiveError):
            parse_directive("Please build this feature and plan it")

    def test_second_decorator_is_ambiguous(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "Exactly one"):
            parse_directive("@awr feature.plan\n\n@awr feature.plan")

    def test_legacy_ewb_directive_fails_closed(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "@awr"):
            parse_directive("@ewb feature.plan\n\n# Legacy directive")

    def test_input_feature_plan_is_canonical(self) -> None:
        markdown = """@input
---
awr:
  schema: awr.input/v1
  intent: feature.plan
  requested_authority: plan_only
---

# Feature
"""
        directive = parse_directive(markdown)
        self.assertEqual(directive.kind, WorkKind.FEATURE)
        self.assertEqual(directive.form, "input")
        self.assertIsNone(directive.parent_work_order_id)

    def test_input_message_type_alias_and_refinement_parent(self) -> None:
        markdown = """@input

---
awr:
  schema_version: awr.message/v1
  message_type: refinement.plan
  parent_work_order_id: AWR-123
---

Tighten tests
"""
        directive = parse_directive(markdown)
        self.assertEqual(directive.parent_work_order_id, "AWR-123")
        self.assertEqual(directive.form, "input")

    def test_input_rejects_execution_intent(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "unsupported"):
            parse_directive(
                """@input
---
awr:
  intent: plan.execute
---

# No
"""
            )


if __name__ == "__main__":
    unittest.main()
