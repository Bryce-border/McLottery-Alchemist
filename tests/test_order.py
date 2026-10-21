"""下单前校验测试。

重点：金额不一致必须阻断；购物车指纹用于防重复提交；状态不明不得推断。
"""

import json
import sys
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.order import (  # noqa: E402
    CartLine,
    build_confirmation,
    cart_fingerprint,
    duplicate_within,
    explain_order_status,
    parse_cart,
    verify_price,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class CartTest(unittest.TestCase):
    def test_subtotal(self):
        lines = parse_cart(load("cart_basic.json"))
        self.assertEqual(len(lines), 3)
        subtotal = sum(line.subtotal for line in lines)
        self.assertEqual(subtotal, Decimal("58.50"))

    def test_missing_price(self):
        line = CartLine(meal_code="X", name="神秘餐品", quantity=2, unit_price=None)
        self.assertIsNone(line.subtotal)


class PriceCheckTest(unittest.TestCase):
    def setUp(self):
        self.lines = parse_cart(load("cart_basic.json"))

    def test_consistent(self):
        check = verify_price(self.lines, Decimal("58.5"))
        self.assertTrue(check.consistent)
        self.assertEqual(check.difference, Decimal("0.00"))

    def test_inconsistent_blocks(self):
        check = verify_price(self.lines, Decimal("62"))
        self.assertFalse(check.consistent)
        # 差异来自门店调价或漏算优惠，必须先查清
        self.assertEqual(check.difference, Decimal("3.50"))

    def test_delivery_and_discount(self):
        check = verify_price(
            self.lines,
            Decimal("57.5"),
            delivery_fee=Decimal("6"),
            discount=Decimal("7"),
        )
        self.assertTrue(check.consistent)

    def test_missing_remote_total(self):
        check = verify_price(self.lines, None)
        self.assertIsNone(check.consistent)
        self.assertIn("不能确认价格", check.reason)

    def test_missing_unit_price(self):
        lines = (CartLine(meal_code="X", name="神秘餐品", unit_price=None),)
        check = verify_price(lines, Decimal("10"))
        self.assertIsNone(check.consistent)
        self.assertIn("神秘餐品", check.reason)


class ConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.lines = parse_cart(load("cart_basic.json"))

    def test_ready_when_price_matches(self):
        check = verify_price(self.lines, Decimal("58.5"))
        card = build_confirmation(
            store_name="麦当劳测试店",
            store_id="S001",
            meal_type="到店自取",
            lines=self.lines,
            price_check=check,
        )
        self.assertTrue(card.ready)
        self.assertEqual(card.payable, Decimal("58.5"))

    def test_blocked_when_price_differs(self):
        check = verify_price(self.lines, Decimal("99"))
        card = build_confirmation(
            store_name="麦当劳测试店",
            meal_type="到店自取",
            lines=self.lines,
            price_check=check,
        )
        self.assertFalse(card.ready)
        self.assertTrue(card.blockers)

    def test_empty_cart_blocked(self):
        check = verify_price((), Decimal("0"))
        card = build_confirmation(
            store_name="店",
            meal_type="到店自取",
            lines=(),
            price_check=check,
        )
        self.assertIn("购物车为空", card.blockers)


class FingerprintTest(unittest.TestCase):
    def setUp(self):
        self.lines = parse_cart(load("cart_basic.json"))

    def test_stable_and_order_independent(self):
        a = cart_fingerprint("S001", "到店自取", self.lines)
        b = cart_fingerprint("S001", "到店自取", tuple(reversed(self.lines)))
        self.assertEqual(a, b)

    def test_differs_by_store(self):
        a = cart_fingerprint("S001", "到店自取", self.lines)
        b = cart_fingerprint("S002", "到店自取", self.lines)
        self.assertNotEqual(a, b)

    def test_duplicate_detected(self):
        fp = cart_fingerprint("S001", "到店自取", self.lines)
        now = datetime(2026, 10, 21, 12, 0, 0)
        records = [
            {"fingerprint": fp, "created_at": (now - timedelta(minutes=3)).isoformat()}
        ]
        self.assertIsNotNone(duplicate_within(records, fp, now))

    def test_old_record_ignored(self):
        fp = cart_fingerprint("S001", "到店自取", self.lines)
        now = datetime(2026, 10, 21, 12, 0, 0)
        records = [
            {"fingerprint": fp, "created_at": (now - timedelta(minutes=30)).isoformat()}
        ]
        self.assertIsNone(duplicate_within(records, fp, now))


class StatusTest(unittest.TestCase):
    def test_known_status(self):
        self.assertIn("退款", explain_order_status("已支付"))

    def test_unknown_status(self):
        self.assertIn("不做推断", explain_order_status(None))
        self.assertIn("未识别", explain_order_status("奇怪状态"))


if __name__ == "__main__":
    unittest.main()
