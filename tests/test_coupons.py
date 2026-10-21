"""优惠券试算测试。

重点：不叠加、不猜门槛、不承诺门店可用。
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.coupon import (  # noqa: E402
    best_coupon,
    claim_advice,
    parse_coupons,
    quote_coupon,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CouponParseTest(unittest.TestCase):
    def setUp(self):
        self.coupons = parse_coupons(load("coupons_mixed.json"))

    def test_parses_count(self):
        self.assertEqual(len(self.coupons), 5)

    def test_threshold_from_text(self):
        c1 = next(c for c in self.coupons if c.coupon_id == "C001")
        self.assertEqual(c1.threshold, Decimal("30"))
        self.assertEqual(c1.amount_off, Decimal("5"))

    def test_discount_rate_normalized(self):
        c3 = next(c for c in self.coupons if c.coupon_id == "C003")
        self.assertEqual(c3.pay_rate, Decimal("0.8"))
        self.assertEqual(c3.max_discount, Decimal("8"))

    def test_missing_fields_are_flagged(self):
        c5 = next(c for c in self.coupons if c.coupon_id == "C005")
        self.assertIn("threshold", c5.missing_fields)
        self.assertIn("discount", c5.missing_fields)
        self.assertEqual(c5.kind, "待确认")


class BestCouponTest(unittest.TestCase):
    def setUp(self):
        self.coupons = parse_coupons(load("coupons_mixed.json"))

    def test_picks_lowest_payable(self):
        decision = best_coupon(Decimal("58.5"), self.coupons)
        self.assertIsNotNone(decision.best)
        # 58.5 元：满 50 减 10 → 48.5，优于 8 折券（最高减 8 → 50.5）
        self.assertEqual(decision.best.coupon.coupon_id, "C002")
        self.assertEqual(decision.best.payable, Decimal("48.50"))
        self.assertEqual(decision.best.saved, Decimal("10.00"))

    def test_discount_coupon_wins_on_small_order(self):
        decision = best_coupon(Decimal("40"), self.coupons)
        # 40 元：8 折 → 32，优于满 30 减 5 → 35；满 50 券不适用
        self.assertEqual(decision.best.coupon.coupon_id, "C003")
        self.assertEqual(decision.best.payable, Decimal("32.00"))

    def test_threshold_not_met(self):
        c2 = next(c for c in self.coupons if c.coupon_id == "C002")
        quote = quote_coupon(c2, Decimal("40"), Decimal("0"))
        self.assertEqual(quote.status, "不适用")
        self.assertIn("还差", quote.reason)

    def test_unverified_coupon_not_used(self):
        decision = best_coupon(Decimal("58.5"), self.coupons)
        names = [q.coupon.name for q in decision.unverified]
        self.assertIn("神秘惊喜券", names)

    def test_no_stacking(self):
        decision = best_coupon(Decimal("58.5"), self.coupons)
        self.assertTrue(any("叠加" in a for a in decision.assumptions))
        self.assertEqual(len(decision.quotes), 5)

    def test_delivery_coupon_only_when_fee_exists(self):
        delivery = next(c for c in self.coupons if c.coupon_id == "C004")
        free = quote_coupon(delivery, Decimal("40"), Decimal("6"))
        self.assertEqual(free.saved, Decimal("6"))
        none = quote_coupon(delivery, Decimal("40"), Decimal("0"))
        self.assertEqual(none.status, "不适用")

    def test_empty_cart(self):
        decision = best_coupon(Decimal("0"), self.coupons)
        self.assertIsNone(decision.best)

    def test_invalid_amount_raises(self):
        with self.assertRaises(ValueError):
            best_coupon(Decimal("-1"), self.coupons)


class ClaimAdviceTest(unittest.TestCase):
    def test_advice_does_not_push_spending(self):
        coupons = parse_coupons(load("coupons_mixed.json"))
        advice = claim_advice(coupons)
        self.assertTrue(any("不要为了凑门槛" in a for a in advice))
        self.assertTrue(any("免配送" in a for a in advice))

    def test_empty_coupons(self):
        advice = claim_advice(())
        self.assertTrue(advice)


if __name__ == "__main__":
    unittest.main()
