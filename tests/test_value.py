"""兑换价值与商城筛选测试。

重点验证：
* point="0" 的付费活动不能被当成 0 积分兑换；
* status=2 但已过 downTime 的商品不能算可兑换；
* 无参考价值时不得生成「性价比排名」。
"""

import json
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.models import AccountSnapshot, parse_products  # noqa: E402
from mclottery.value import (  # noqa: E402
    calculate_unit_value,
    compare_redemption_options,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, 0)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class UnitValueTest(unittest.TestCase):
    def test_without_reference_value_returns_no_number(self):
        result = calculate_unit_value(None, 200)
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "NO_REFERENCE_VALUE")
        self.assertIn("无法计算", result.display)

    def test_with_reference_value(self):
        result = calculate_unit_value(Decimal("12"), Decimal("200"))
        self.assertEqual(result.status, "OK")
        self.assertEqual(result.value, Decimal("0.06"))
        self.assertIn("元/积分", result.display)

    def test_zero_points_is_not_a_freebie(self):
        result = calculate_unit_value(Decimal("45"), Decimal("0"))
        self.assertIsNone(result.value)
        self.assertEqual(result.status, "INVALID_POINTS")

    def test_invalid_reference_value(self):
        result = calculate_unit_value(Decimal("0"), Decimal("100"))
        self.assertEqual(result.status, "NO_REFERENCE_VALUE")


class ProductFilterTest(unittest.TestCase):
    def setUp(self):
        self.products = parse_products(load("products_mixed.json"))
        self.account = AccountSnapshot.from_mcp(load("account_expiring.json"))

    def test_affordable_only_contains_really_redeemable(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        names = [item.product.name for item in comparison.affordable]
        self.assertEqual(len(names), 3)
        self.assertIn("6.9元可乐麦炫酷", names)
        self.assertIn("10.9元指定小食任选", names)

    def test_paid_activity_excluded_with_reason(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        excluded = {item.product.spu_id: item.reason for item in comparison.excluded}
        self.assertIn(1830, excluded)
        self.assertIn("非积分兑换商品", excluded[1830])

    def test_expired_window_excluded_even_if_status_is_on_shelf(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        excluded = {item.product.spu_id: item.reason for item in comparison.excluded}
        self.assertIn(15866, excluded)
        self.assertIn("兑换已截止", excluded[15866])

    def test_sold_out_and_off_shelf_excluded(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        excluded = {item.product.spu_id: item.reason for item in comparison.excluded}
        self.assertEqual(excluded[16052], "已售罄")
        self.assertEqual(excluded[5246], "已下架")

    def test_preheat_and_not_started_excluded(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        excluded = {item.product.spu_id: item.reason for item in comparison.excluded}
        self.assertIn("预热", excluded[99999])
        self.assertIn("兑换未开始", excluded[5555])

    def test_short_of_points_reports_gap(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        gaps = {item.product.spu_id: item.note for item in comparison.short_of_points}
        self.assertIn("还差 480 积分", gaps[17081])

    def test_value_basis_none_adds_qualitative_note(self):
        comparison = compare_redemption_options(self.products, self.account, now=NOW)
        self.assertEqual(comparison.value_basis, "none")
        joined = " ".join(comparison.notes)
        self.assertIn("不代表性价比", joined)

    def test_preference_affects_order(self):
        comparison = compare_redemption_options(
            self.products, self.account, now=NOW, preferred_categories=["到店专用"]
        )
        self.assertTrue(comparison.affordable)
        self.assertTrue(all(i.product.cat_name == "到店专用" for i in comparison.affordable))

    def test_unknown_balance_never_claims_affordable(self):
        comparison = compare_redemption_options(
            self.products, AccountSnapshot(), now=NOW
        )
        self.assertFalse(comparison.affordable)
        self.assertTrue(comparison.short_of_points)
        joined = " ".join(comparison.notes)
        self.assertIn("余额未获取", joined)


if __name__ == "__main__":
    unittest.main()
