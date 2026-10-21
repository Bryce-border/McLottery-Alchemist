"""积分缺口计划测试。

重点：没有积分获取规则时不得编造速度；预算与达成时间必须同时出现。
"""

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.mealplan import GoalRequest, build_goal_plan, lower_targets  # noqa: E402

TODAY = date(2026, 10, 21)
DEADLINE = date(2026, 12, 31)


def request(**overrides):
    base = dict(
        target_name="麦麦兑换礼品",
        target_points=Decimal("1200"),
        current_points=Decimal("300"),
        today=TODAY,
        weekly_visits=2,
        budget_min=Decimal("25"),
        budget_max=Decimal("40"),
        deadline=DEADLINE,
        points_per_yuan=Decimal("1"),
    )
    base.update(overrides)
    return GoalRequest(**base)


class NoEarnRuleTest(unittest.TestCase):
    def test_gap_is_computed_without_guessing(self):
        plan = build_goal_plan(request(points_per_yuan=None))
        self.assertEqual(plan.gap, Decimal("900"))
        self.assertIsNone(plan.earn_per_visit)
        self.assertIsNone(plan.visits_needed)
        self.assertIsNone(plan.projected_points)
        self.assertTrue(any("缺少积分获取规则" in a for a in plan.alternatives))

    def test_points_per_visit_takes_precedence(self):
        plan = build_goal_plan(
            request(points_per_yuan=None, points_per_visit=Decimal("30"))
        )
        self.assertEqual(plan.earn_per_visit, Decimal("30"))


class FeasibilityTest(unittest.TestCase):
    def test_infeasible_plan_says_so(self):
        plan = build_goal_plan(request())
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.visits_needed, 28)
        self.assertEqual(plan.projected_points, Decimal("950.00"))
        self.assertEqual(plan.projected_spend, Decimal("650.00"))

    def test_weekly_plan_matches_projected(self):
        plan = build_goal_plan(request())
        earned = sum(
            (w.earned_points for w in plan.weekly_plan), Decimal("0")
        )
        self.assertEqual(plan.current_points + earned, plan.projected_points)

    def test_weekly_plan_never_exceeds_deadline(self):
        plan = build_goal_plan(request())
        self.assertLessEqual(len(plan.weekly_plan), 12)
        self.assertEqual(len(plan.weekly_plan), 10)

    def test_feasible_when_budget_and_frequency_enough(self):
        plan = build_goal_plan(
            request(points_per_visit=Decimal("120"), weekly_visits=2)
        )
        self.assertTrue(plan.feasible)

    def test_spend_is_always_reported(self):
        plan = build_goal_plan(request())
        self.assertIsNotNone(plan.projected_spend)


class EdgeCaseTest(unittest.TestCase):
    def test_past_deadline_is_warned(self):
        plan = build_goal_plan(request(deadline=date(2026, 10, 1)))
        self.assertEqual(plan.weeks_left, Decimal("0"))
        self.assertTrue(any("截止日期已到" in w for w in plan.warnings))

    def test_zero_weekly_visits(self):
        plan = build_goal_plan(request(weekly_visits=0))
        self.assertFalse(plan.feasible)
        self.assertTrue(any("每周 0 次" in a for a in plan.alternatives))

    def test_high_frequency_warning(self):
        plan = build_goal_plan(request(weekly_visits=6))
        self.assertTrue(any("高频消费" in w for w in plan.warnings))

    def test_expiring_points_warning(self):
        plan = build_goal_plan(request(expiring_points=Decimal("120")))
        self.assertTrue(any("即将到期" in w for w in plan.warnings))

    def test_invalid_budget_raises(self):
        with self.assertRaises(ValueError):
            build_goal_plan(request(budget_min=Decimal("50"), budget_max=Decimal("20")))

    def test_goal_already_reached(self):
        plan = build_goal_plan(request(current_points=Decimal("2000")))
        self.assertEqual(plan.gap, Decimal("0"))
        self.assertTrue(plan.weekly_plan)  # 仍给出后续消费参考


class LowerTargetTest(unittest.TestCase):
    def test_suggests_affordable_targets(self):
        targets = [
            ("A", Decimal("200")),
            ("B", Decimal("150")),
            ("C", Decimal("500")),
            ("D", Decimal("50")),
        ]
        ready = lower_targets(targets, Decimal("300"))
        self.assertEqual([n for n, _ in ready], ["A", "B", "D"])


if __name__ == "__main__":
    unittest.main()
