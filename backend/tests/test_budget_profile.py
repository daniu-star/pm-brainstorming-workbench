"""预算档位（FR-08 / 低成本环境）：只收紧不放宽，且写进 Run.budget_json 可复现。"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

from core.config import settings  # noqa: E402
from domain import contracts as C  # noqa: E402
from services.command_service import default_budget  # noqa: E402


class BudgetProfileTests(unittest.TestCase):
    def _with_profile(self, name: str):
        return mock.patch.object(settings, "llm_budget_profile", name)

    def test_standard_profile_is_contract_default(self) -> None:
        with self._with_profile("standard"):
            budget = default_budget()
        self.assertEqual(budget["max_run_input_tokens"], C.MAX_RUN_INPUT_TOKENS)
        self.assertEqual(budget["max_run_output_tokens"], C.MAX_RUN_OUTPUT_TOKENS)
        self.assertEqual(budget["max_upstream_calls"], C.MAX_UPSTREAM_CALLS_PER_RUN)
        self.assertEqual(budget["max_context_input_tokens"], C.MAX_CONTEXT_INPUT_TOKENS)
        self.assertEqual(budget["max_output_tokens"], C.MAX_OUTPUT_TOKENS)

    def test_lean_profile_only_tightens(self) -> None:
        with self._with_profile("lean"):
            budget = default_budget()
            synthesis = default_budget(output_tokens=C.MAX_SYNTHESIS_OUTPUT_TOKENS)
        limits = C.BUDGET_PROFILES["lean"]
        for key, cap in limits.items():
            self.assertIn(key, budget if key in budget else synthesis)
            target = budget if key in budget else synthesis
            self.assertLessEqual(int(target[key]), int(cap))
        # 不允许放宽：每一项都不得大于标准档
        with self._with_profile("standard"):
            standard = default_budget()
        for key, value in budget.items():
            if key in standard:
                self.assertLessEqual(int(value), int(standard[key]), key)
        self.assertLessEqual(int(synthesis["max_synthesis_output_tokens"]), limits["max_synthesis_output_tokens"])
        self.assertLessEqual(int(synthesis["max_output_tokens"]), C.MAX_SYNTHESIS_OUTPUT_TOKENS)

    def test_frugal_profile_is_tighter_than_lean(self) -> None:
        with self._with_profile("lean"):
            lean = default_budget()
        with self._with_profile("frugal"):
            frugal = default_budget()
        for key in ("max_upstream_calls", "max_context_input_tokens", "max_output_tokens"):
            self.assertLessEqual(int(frugal[key]), int(lean[key]), key)

    def test_unknown_profile_falls_back_to_standard(self) -> None:
        with self._with_profile("no-such-profile"):
            budget = default_budget()
        self.assertEqual(budget["max_upstream_calls"], C.MAX_UPSTREAM_CALLS_PER_RUN)
        self.assertEqual(budget["max_context_input_tokens"], C.MAX_CONTEXT_INPUT_TOKENS)

    def test_health_detail_reports_budget_limits(self) -> None:
        import main

        with self._with_profile("frugal"):
            limits = main.C_budget_defaults()
        self.assertEqual(limits["max_upstream_calls"], C.BUDGET_PROFILES["frugal"]["max_upstream_calls"])
        self.assertIn("max_run_output_tokens", limits)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()