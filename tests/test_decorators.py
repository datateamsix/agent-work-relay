from __future__ import annotations

import unittest

from ewb.contracts import WorkAction, WorkKind
from ewb.decorators import DirectiveError, parse_directive


class DirectiveTests(unittest.TestCase):
    def test_feature_plan(self) -> None:
        directive = parse_directive("@ewb feature.plan\n\n# Feature")
        self.assertEqual(directive.kind, WorkKind.FEATURE)
        self.assertEqual(directive.action, WorkAction.PLAN)
        self.assertIsNone(directive.parent_work_order_id)

    def test_refinement_requires_parent(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "requires parent"):
            parse_directive("@ewb refinement.plan\n\nTighten the tests")

    def test_refinement_accepts_parent(self) -> None:
        directive = parse_directive("@ewb refinement.plan parent=EWB-123\n\nTighten tests")
        self.assertEqual(directive.parent_work_order_id, "EWB-123")

    def test_loose_keyword_does_not_route(self) -> None:
        with self.assertRaises(DirectiveError):
            parse_directive("Please build this feature and plan it")

    def test_second_decorator_is_ambiguous(self) -> None:
        with self.assertRaisesRegex(DirectiveError, "Exactly one"):
            parse_directive("@ewb feature.plan\n\n@ewb feature.plan")


if __name__ == "__main__":
    unittest.main()
