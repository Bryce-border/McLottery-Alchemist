"""真实接口契约测试（依据 2026-10-21 官方工具文档）。

覆盖五处与早期假设不同的事实：
* 营养返回 TOON 紧凑格式，含 energyKj / energyKcal 两列；
* calculate-price 金额单位是**分**，create-order 是**元**；
* 点餐下单返回 payH5Url，商城兑换只有 payId；
* 取消订单必须带 cancelReasonCode；
* 门店券不返回门槛/面额，麦麦省与卡包返回 Markdown 文本。
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.coupon import (  # noqa: E402
    match_store_coupons,
    parse_markdown_coupons,
    parse_store_coupons,
)
from mclottery.nutrition import parse_nutrition_foods, parse_toon_table  # noqa: E402
from mclottery.order import (  # noqa: E402
    cancel_reason_text,
    fen_to_yuan,
    parse_order_created,
    parse_price_result,
    validate_cancel,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ToonNutritionTest(unittest.TestCase):
    def test_header_and_rows(self):
        table = parse_toon_table(
            "[1]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:\n"
            "  猪柳麦满分,null,1288,308,16,16,24,781,213"
        )
        self.assertEqual(table.columns[0], "productName")
        self.assertEqual(table.rows[0]["energyKcal"], "308")

    def test_parse_from_api_payload(self):
        facts = parse_nutrition_foods(load("nutrition_toon.json"))
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].name, "猪柳麦满分")
        self.assertEqual(facts[0].energy_kcal, Decimal("308"))
        self.assertEqual(facts[0].energy_kj, Decimal("1288"))
        self.assertIsNone(facts[0].description)  # "null" 文本应转成 None
        self.assertFalse(facts[0].kcal_converted)

    def test_kj_only_is_converted_and_flagged(self):
        payload = {"data": "[1]{productName,energyKj}:\n  测试餐品,418.4"}
        facts = parse_nutrition_foods(payload)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].energy_kcal, Decimal("100.0"))
        self.assertTrue(facts[0].kcal_converted)

    def test_malformed_toon_returns_empty(self):
        self.assertEqual(parse_nutrition_foods({"data": "not a table"}), ())


class PriceUnitTest(unittest.TestCase):
    def test_fen_to_yuan(self):
        self.assertEqual(fen_to_yuan(1600), Decimal("16.00"))
        self.assertEqual(fen_to_yuan("600"), Decimal("6.00"))
        self.assertIsNone(fen_to_yuan(None))

    def test_price_result_converted(self):
        quote = parse_price_result(load("price_result.json"))
        self.assertEqual(quote.product_price, Decimal("15.00"))
        self.assertEqual(quote.delivery_price, Decimal("6.00"))
        self.assertEqual(quote.payable, Decimal("21.00"))
        self.assertEqual(quote.tableware_price, Decimal("0.10"))
        self.assertEqual(len(quote.take_way_list), 2)


class OrderCreatedTest(unittest.TestCase):
    def test_pay_link_and_status(self):
        order = parse_order_created(load("order_created.json"))
        self.assertEqual(order.order_id, "1030938730000733964700499858")
        self.assertEqual(order.status_text, "待支付")
        self.assertTrue(order.has_payment_link)
        self.assertIn("scanToPay", order.pay_h5_url)

    def test_amounts_are_yuan_not_fen(self):
        order = parse_order_created(load("order_created.json"))
        # 下单接口的 totalAmount="21" 是 21 元，不能再除以 100
        self.assertEqual(order.real_total_amount, Decimal("21"))
        self.assertEqual(order.delivery_price, Decimal("6"))

    def test_pickup_fields(self):
        order = parse_order_created(load("order_created.json"))
        self.assertEqual(order.take_way, "locker-in")
        self.assertEqual(order.product_names, ("DC套餐测试",))

    def test_missing_pay_link_is_flagged(self):
        order = parse_order_created({"data": {"orderId": "X", "orderDetail": {"orderStatus": "待支付"}}})
        self.assertFalse(order.has_payment_link)
        self.assertIn("payH5Url", order.missing_fields)


class CancelTest(unittest.TestCase):
    def test_reason_required(self):
        blockers = validate_cancel("ORDER-1", None)
        self.assertTrue(any("cancelReasonCode" in b for b in blockers))

    def test_invalid_reason_rejected(self):
        blockers = validate_cancel("ORDER-1", 99)
        self.assertTrue(any("不在官方取值范围" in b for b in blockers))

    def test_valid_reason_passes(self):
        self.assertEqual(validate_cancel("ORDER-1", 2), ())

    def test_reason_text(self):
        self.assertEqual(cancel_reason_text(4), "地址/电话填错了")
        self.assertIn("未识别", cancel_reason_text(99))


class StoreCouponTest(unittest.TestCase):
    def setUp(self):
        self.coupons = parse_store_coupons(load("store_coupons.json"))

    def test_parse_fields(self):
        self.assertEqual(len(self.coupons), 2)
        first = self.coupons[0]
        self.assertEqual(first.title, "外送优惠券二次券")
        self.assertEqual(first.product_codes, ("9900008139",))
        self.assertEqual(first.start_time, "2025-04-21 23:23:00")
        self.assertEqual(first.end_time, "2026-07-01 23:59:59")

    def test_no_threshold_is_exposed(self):
        # 接口不返回门槛/面额，模型里也不该凭空出现
        self.assertFalse(hasattr(self.coupons[0], "threshold"))
        self.assertFalse(hasattr(self.coupons[0], "amount_off"))

    def test_match_by_product(self):
        matches = match_store_coupons(self.coupons, ["9900008139"])
        self.assertTrue(matches[0].applicable)
        self.assertFalse(matches[1].applicable is False)  # 未限商品 → 可能适用

    def test_not_matching_other_cart(self):
        matches = match_store_coupons(self.coupons, ["999999"])
        self.assertFalse(matches[0].applicable)
        self.assertIn("没有匹配商品", matches[0].reason)


class MarkdownCouponTest(unittest.TestCase):
    def setUp(self):
        text = (FIXTURES / "coupon_text.md").read_text(encoding="utf-8")
        self.coupons = parse_markdown_coupons(text)

    def test_parses_both_sections(self):
        titles = [c.title for c in self.coupons]
        self.assertIn("11.9元麦乐鸡", titles)
        self.assertIn("9.9元薯你最甜", titles)

    def test_status_and_claimable(self):
        # 麦麦省块里有状态，卡包块没有；同名时优先取带状态的那条
        claimable = [c for c in self.coupons if c.title == "9.9元薯你最甜" and c.status][0]
        self.assertTrue(claimable.claimable)
        blocked = [c for c in self.coupons if c.title == "北非蛋风味麦满分"][0]
        self.assertFalse(blocked.claimable)

    def test_price_hint_is_coupon_price(self):
        by_title = {c.title: c for c in self.coupons}
        self.assertEqual(by_title["11.9元麦乐鸡"].price, Decimal("11.9"))

    def test_valid_period_and_tags(self):
        card = [c for c in self.coupons if c.valid_period][0]
        self.assertIn("2025-12-09", card.valid_period)
        self.assertIn("到店专用", card.tags)

    def test_empty_text(self):
        self.assertEqual(parse_markdown_coupons(""), ())


if __name__ == "__main__":
    unittest.main()
