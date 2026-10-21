"""积分兑换（mall-create-order）预检与返回解析测试。

重点：
* 兑换不等于免费——积分与现金必须同时出现；
* 下单后是「待支付」，不能当成兑换成功；
* 实物缺 addressId 必须阻断。
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.redemption import (  # noqa: E402
    RedemptionRequest,
    describe_result,
    parse_redemption_result,
    preflight_redemption,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def request(**overrides):
    base = dict(
        sku_id="10863",
        spu_name="早餐权益卡",
        sku_name="30天",
        spu_category="1",
        points_required=Decimal("0"),
        cash_price=Decimal("0.01"),
        count=1,
        available_points=Decimal("2350"),
    )
    base.update(overrides)
    return RedemptionRequest(**base)


class ParseResultTest(unittest.TestCase):
    def setUp(self):
        self.result = parse_redemption_result(load("mall_order_created.json"))

    def test_order_fields(self):
        self.assertEqual(self.result.order_id, "ECS1211037028709736448")
        self.assertEqual(self.result.status_title, "待支付")
        self.assertEqual(self.result.order_status, 1)

    def test_awaiting_payment_and_window(self):
        self.assertTrue(self.result.awaiting_payment)
        self.assertEqual(self.result.left_pay_seconds, 899)
        self.assertIn("14 分", self.result.payment_window_text)

    def test_both_points_and_cash(self):
        # 示例是 0 积分 + 0.01 元：这是现金支付，不能报成免费兑换
        self.assertEqual(self.result.real_total_points, Decimal("0"))
        self.assertEqual(self.result.real_total_amount, Decimal("0.01"))

    def test_goods_parsed(self):
        self.assertEqual(len(self.result.goods), 1)
        self.assertEqual(self.result.goods[0].spu_name, "早餐权益卡")
        self.assertEqual(self.result.goods[0].count, 1)

    def test_address_summary(self):
        self.assertIn("李", self.result.address_summary)
        self.assertIn("江苏", self.result.address_summary)

    def test_narrative_does_not_claim_success(self):
        text = "\n".join(describe_result(self.result))
        self.assertIn("待支付", text)
        self.assertIn("不代付", text)
        self.assertNotIn("兑换成功", text)

    def test_missing_fields_reported(self):
        result = parse_redemption_result({"data": {}})
        self.assertIsNone(result.order_id)
        self.assertIn("orderId", result.missing_fields)
        self.assertIn("接口未返回", result.payment_window_text)


class PreflightTest(unittest.TestCase):
    def test_virtual_item_ready(self):
        check = preflight_redemption(request())
        self.assertTrue(check.ready)

    def test_physical_item_requires_address(self):
        check = preflight_redemption(request(spu_category="2"))
        self.assertFalse(check.ready)
        self.assertTrue(any("addressId" in b for b in check.blockers))

    def test_physical_item_with_address_ready(self):
        check = preflight_redemption(request(spu_category="2", address_id="A001"))
        self.assertTrue(check.ready)

    def test_points_insufficient(self):
        check = preflight_redemption(
            request(points_required=Decimal("3000"), cash_price=Decimal("0"))
        )
        self.assertFalse(check.ready)
        self.assertTrue(any("积分不足" in b for b in check.blockers))
        self.assertEqual(check.totals["remaining_points"], Decimal("-650.00"))

    def test_cash_only_warns_not_free(self):
        check = preflight_redemption(request(points_required=Decimal("0")))
        self.assertTrue(any("现金支付" in w for w in check.warnings))

    def test_points_plus_cash(self):
        check = preflight_redemption(
            request(points_required=Decimal("1200"), cash_price=Decimal("9.9"))
        )
        self.assertTrue(any("积分 + 现金" in w for w in check.warnings))

    def test_count_multiplies(self):
        check = preflight_redemption(request(points_required=Decimal("100"), count=3))
        self.assertEqual(check.totals["total_points"], Decimal("300.00"))

    def test_missing_category_blocks(self):
        check = preflight_redemption(request(spu_category=""))
        self.assertFalse(check.ready)
        self.assertTrue(any("spuCategory" in b for b in check.blockers))

    def test_missing_sku_blocks(self):
        check = preflight_redemption(request(sku_id=None))
        self.assertFalse(check.ready)
        self.assertTrue(any("skuId" in b for b in check.blockers))

    def test_unknown_price_blocks(self):
        check = preflight_redemption(
            request(points_required=None, cash_price=None)
        )
        self.assertFalse(check.ready)
        self.assertTrue(any("都未知" in b for b in check.blockers))


if __name__ == "__main__":
    unittest.main()
