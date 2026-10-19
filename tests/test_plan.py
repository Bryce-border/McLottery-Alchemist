"""三类方案生成测试。"""

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.models import AccountSnapshot, LotteryActivity, parse_products  # noqa: E402
from mclottery.plan import build_plans  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, 0)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PlanTest(unittest.TestCase):
    def test_all_three_plans_available(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_expiring.json")),
            parse_products(load("products_mixed.json")),
            LotteryActivity.from_mcp(load("lottery_running_ok.json")),
            now=NOW,
        )
        codes = {card.code: card for card in bundle.cards}
        self.assertTrue(codes["A"].applicable)
        self.assertTrue(codes["B"].applicable)
        self.assertTrue(codes["C"].applicable)
        rendered = "\n".join(bundle.render())
        self.assertIn("优先止损", rendered)

    def test_plan_c_blocked_when_points_insufficient(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_normal.json")),
            parse_products(load("products_mixed.json")),
            LotteryActivity.from_mcp(load("lottery_running_insufficient.json")),
            now=NOW,
        )
        cards = {card.code: card for card in bundle.cards}
        self.assertFalse(cards["C"].applicable)
        self.assertIn("积分不足", " ".join(cards["C"].blockers))

    def test_no_expiring_points_marks_plan_a_unavailable(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_normal.json")),
            parse_products(load("products_mixed.json")),
            now=NOW,
        )
        cards = {card.code: card for card in bundle.cards}
        self.assertFalse(cards["A"].applicable)
        self.assertIn("即将过期", " ".join(cards["A"].blockers))

    def test_without_products_plan_b_is_honest(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_expiring.json")),
            (),
            now=NOW,
        )
        cards = {card.code: card for card in bundle.cards}
        self.assertFalse(cards["B"].applicable)
        self.assertIn("未返回商品", " ".join(cards["B"].blockers))

    def test_without_activity_plan_c_says_not_queried(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_expiring.json")),
            parse_products(load("products_mixed.json")),
            None,
            now=NOW,
        )
        cards = {card.code: card for card in bundle.cards}
        self.assertFalse(cards["C"].applicable)
        self.assertIn("尚未查询", " ".join(cards["C"].blockers))

    def test_plan_c_never_promises_win(self):
        bundle = build_plans(
            AccountSnapshot.from_mcp(load("account_expiring.json")),
            parse_products(load("products_mixed.json")),
            LotteryActivity.from_mcp(load("lottery_running_ok.json")),
            now=NOW,
        )
        text = "\n".join(bundle.render())
        self.assertIn("概率事件", text)
        for forbidden in ("必中", "提高中奖率", "稳赚"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
