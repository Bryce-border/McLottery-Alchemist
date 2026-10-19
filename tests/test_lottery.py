"""抽奖成本、期望价值与前置校验测试。"""

import json
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.lottery import (  # noqa: E402
    Outcome,
    calculate_remaining_points,
    evaluate_activity,
    expected_value,
    total_cost,
)
from mclottery.models import AccountSnapshot, LotteryActivity  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, 0)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CostTest(unittest.TestCase):
    def test_total_cost(self):
        self.assertEqual(total_cost(Decimal("24"), 3), Decimal("72"))

    def test_total_cost_needs_both_inputs(self):
        self.assertIsNone(total_cost(None, 3))
        self.assertIsNone(total_cost(Decimal("24"), 0))

    def test_remaining_points_can_be_negative(self):
        self.assertEqual(
            calculate_remaining_points(Decimal("20.5"), Decimal("24"), 1),
            Decimal("-3.5"),
        )

    def test_remaining_points_unknown_balance(self):
        self.assertIsNone(calculate_remaining_points(None, Decimal("24"), 1))


class ExpectedValueTest(unittest.TestCase):
    def test_missing_probability_refuses_number(self):
        outcomes = [
            Outcome("下单立减3元券", Decimal("3"), None),
            Outcome("下单立减2元券", Decimal("2"), None),
        ]
        result = expected_value(outcomes)
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "PROBABILITY_MISSING")
        self.assertIn("无法给出", result.display())

    def test_complete_distribution_computes(self):
        outcomes = [
            Outcome("10元券", Decimal("10"), Decimal("0.5")),
            Outcome("未中奖", Decimal("0"), Decimal("0.5")),
        ]
        result = expected_value(outcomes)
        self.assertTrue(result.is_conclusive)
        self.assertEqual(result.value, Decimal("5.0"))

    def test_incomplete_distribution_refused(self):
        outcomes = [
            Outcome("10元券", Decimal("10"), Decimal("0.5")),
            Outcome("未中奖", Decimal("0"), Decimal("0.2")),
        ]
        result = expected_value(outcomes)
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "PROBABILITY_INCOMPLETE")

    def test_missing_value_refused(self):
        outcomes = [Outcome("30积分", None, Decimal("1"))]
        result = expected_value(outcomes)
        self.assertEqual(result.status, "VALUE_MISSING")

    def test_empty_outcomes(self):
        self.assertEqual(expected_value([]).status, "INVALID_INPUT")


class PreflightTest(unittest.TestCase):
    def test_real_snapshot_blocks_insufficient_points(self):
        activity = LotteryActivity.from_mcp(load("lottery_running_insufficient.json"))
        account = AccountSnapshot.from_mcp(load("account_normal.json"))
        preflight = evaluate_activity(activity, account, now=NOW)
        self.assertFalse(preflight.ok)
        joined = " ".join(preflight.blockers)
        self.assertIn("资源不足", joined)
        self.assertIn("积分不足", joined)
        self.assertIn("差额 3.5", joined)
        self.assertEqual(preflight.cost_per_draw, Decimal("24"))
        self.assertIn("24 积分", preflight.consumption_text)

    def test_ok_case_allows_authorization_flow(self):
        activity = LotteryActivity.from_mcp(load("lottery_running_ok.json"))
        account = AccountSnapshot.from_mcp(load("account_expiring.json"))
        preflight = evaluate_activity(activity, account, now=NOW, draws=1)
        self.assertTrue(preflight.ok, preflight.blockers)
        self.assertEqual(preflight.cost_per_draw, Decimal("24"))
        self.assertEqual(preflight.resource_eligible, True)

    def test_multi_draw_cost_check(self):
        activity = LotteryActivity.from_mcp(load("lottery_running_ok.json"))
        account = AccountSnapshot.from_mcp(load("account_expiring.json"))
        self.assertTrue(evaluate_activity(activity, account, now=NOW, draws=13).ok)
        blocked = evaluate_activity(activity, account, now=NOW, draws=14)
        self.assertFalse(blocked.ok)
        self.assertIn("积分不足", " ".join(blocked.blockers))

    def test_ended_activity_blocked(self):
        activity = LotteryActivity.from_mcp(load("lottery_ended.json"))
        preflight = evaluate_activity(activity, None, now=NOW)
        self.assertFalse(preflight.ok)
        joined = " ".join(preflight.blockers)
        self.assertIn("已结束", joined)
        self.assertIn("不可参与", joined)

    def test_not_login_blocked(self):
        activity = LotteryActivity.from_mcp(load("lottery_not_login.json"))
        preflight = evaluate_activity(activity, None, now=NOW)
        self.assertFalse(preflight.ok)
        self.assertIn("NOT_LOGIN", " ".join(preflight.blockers))

    def test_missing_decision_is_blocking_not_assumed(self):
        payload = load("lottery_running_ok.json")
        payload["data"].pop("drawDecision")
        activity = LotteryActivity.from_mcp(payload)
        preflight = evaluate_activity(activity, None, now=NOW)
        self.assertFalse(preflight.ok)
        self.assertIn("resourceEligible", " ".join(preflight.blockers))

    def test_zero_draws_blocked(self):
        activity = LotteryActivity.from_mcp(load("lottery_running_ok.json"))
        account = AccountSnapshot.from_mcp(load("account_expiring.json"))
        preflight = evaluate_activity(activity, account, now=NOW, draws=0)
        self.assertFalse(preflight.ok)


class ConsumptionSafetyTest(unittest.TestCase):
    def test_missing_actual_cost_cannot_fall_back_to_draw_point(self):
        payload = load("lottery_running_ok.json")
        payload["data"]["drawDecision"].pop("nextConsumption", None)
        result = evaluate_activity(LotteryActivity.from_mcp(payload), now=NOW)
        self.assertFalse(result.ok)
        self.assertIsNone(result.cost_per_draw)

    def test_chance_batch_does_not_claim_zero_total_cost(self):
        payload = load("lottery_running_ok.json")
        payload["data"]["drawDecision"]["nextConsumption"] = {"type": "CHANCES", "chances": 1}
        result = evaluate_activity(LotteryActivity.from_mcp(payload), now=NOW, draws=2)
        self.assertFalse(result.ok)
        self.assertIn("逐次", " ".join(result.blockers))


if __name__ == "__main__":
    unittest.main()
