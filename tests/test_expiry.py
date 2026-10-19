"""积分到期分类的诚实性测试。"""

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.expiry import ExpiryPolicy, classify_expiring_points  # noqa: E402
from mclottery.models import AccountSnapshot  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ExpiryTest(unittest.TestCase):
    def test_normal_account_has_no_risk(self):
        report = classify_expiring_points(
            load("account_normal.json"), today=date(2026, 10, 19)
        )
        self.assertEqual(report.this_month_total, Decimal("0"))
        self.assertEqual(report.next_month_total, Decimal("0"))
        self.assertEqual(report.total_at_risk, Decimal("0"))
        self.assertFalse(report.has_risk())
        self.assertEqual(report.expired_total, Decimal("13.5"))

    def test_expiring_account_buckets(self):
        report = classify_expiring_points(
            load("account_expiring.json"), today=date(2026, 10, 19)
        )
        self.assertEqual(report.this_month_total, Decimal("120"))
        self.assertEqual(report.next_month_total, Decimal("80"))
        self.assertEqual(report.total_at_risk, Decimal("200"))
        self.assertTrue(report.has_risk())
        self.assertEqual(len(report.batches), 2)
        self.assertTrue(all(b.precision == "month" for b in report.batches))
        self.assertTrue(all(b.source_field for b in report.batches))

    def test_no_batch_detail_is_declared(self):
        report = classify_expiring_points(
            load("account_expiring.json"), today=date(2026, 10, 19)
        )
        self.assertFalse(report.has_batch_detail)
        joined = "\n".join(report.summary_lines())
        self.assertIn("月度汇总", joined)
        self.assertIn("不是麦当劳官方", joined)

    def test_missing_fields_are_reported_not_guessed(self):
        report = classify_expiring_points(
            load("account_no_expiry.json"), today=date(2026, 10, 19)
        )
        self.assertEqual(report.total_at_risk, Decimal("0"))
        self.assertTrue(any("到期汇总" in gap for gap in report.data_gaps))
        joined = "\n".join(report.summary_lines())
        self.assertIn("数据缺口", joined)

    def test_policy_window_is_configurable(self):
        policy = ExpiryPolicy(near_days=1, mid_days=2)
        report = classify_expiring_points(
            load("account_expiring.json"), today=date(2026, 10, 19), policy=policy
        )
        # 月末距今超过 2 天，不再计入近端窗口，但月度风险仍在
        self.assertEqual(report.near_term_total, Decimal("0"))
        self.assertTrue(report.has_risk())

    def test_accepts_snapshot_instance(self):
        snapshot = AccountSnapshot(available=Decimal("10"), expiring_this_month=Decimal("5"))
        report = classify_expiring_points(snapshot, today=date(2026, 10, 19))
        self.assertEqual(report.this_month_total, Decimal("5"))


if __name__ == "__main__":
    unittest.main()
