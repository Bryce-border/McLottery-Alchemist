"""CLI 端到端测试（有副作用路径只走内存/临时台账）。"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.cli import main  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


class CliTest(unittest.TestCase):
    def test_account(self):
        code, out, _ = run(["account", "--input", str(FIXTURES / "account_normal.json")])
        self.assertEqual(code, 0)
        self.assertIn("可用积分：20.5", out)
        self.assertIn("无法给出具体到期日期", out)

    def test_expiry_json(self):
        code, out, _ = run(
            [
                "expiry",
                "--input",
                str(FIXTURES / "account_expiring.json"),
                "--today",
                "2026-10-19",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["this_month_total"], "120")
        self.assertTrue(payload["has_risk"])

    def test_products(self):
        code, out, _ = run(
            [
                "products",
                "--input",
                str(FIXTURES / "products_mixed.json"),
                "--account",
                str(FIXTURES / "account_expiring.json"),
                "--now",
                "2026-10-19 12:00:00",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("6.9元可乐麦炫酷", out)
        self.assertIn("已排除 7 个不可兑换条目", out)
        self.assertNotIn("麦当劳亲子读书会｜0", out)

    def test_preflight_blocked(self):
        code, out, _ = run(
            [
                "preflight",
                "--input",
                str(FIXTURES / "lottery_running_insufficient.json"),
                "--account",
                str(FIXTURES / "account_normal.json"),
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("不可抽奖", out)
        self.assertIn("积分不足", out)

    def test_ev_refuses_without_probability(self):
        code, out, _ = run(
            ["ev", "--input", str(FIXTURES / "outcomes_missing_probability.json")]
        )
        self.assertEqual(code, 0)
        self.assertIn("无法给出期望值", out)

    def test_ev_with_full_distribution(self):
        code, out, _ = run(["ev", "--input", str(FIXTURES / "outcomes_full.json")])
        self.assertEqual(code, 0)
        self.assertIn("理论期望价值约", out)

    def test_plan(self):
        code, out, _ = run(
            [
                "plan",
                "--account",
                str(FIXTURES / "account_expiring.json"),
                "--products",
                str(FIXTURES / "products_mixed.json"),
                "--activity",
                str(FIXTURES / "lottery_running_ok.json"),
                "--now",
                "2026-10-19 12:00:00",
            ]
        )
        self.assertEqual(code, 0)
        for title in ("优先止损", "稳健兑换", "娱乐抽奖"):
            self.assertIn(title, out)

    def test_calc(self):
        code, out, _ = run(["calc", "--cost", "24", "--available", "20.5", "--draws", "1"])
        self.assertEqual(code, 0)
        self.assertIn("预计剩余：-3.5", out)

    def test_guard_flow_with_temp_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = str(Path(tmp) / "ledger.jsonl")
            code, out, _ = run(
                [
                    "guard",
                    "propose",
                    "--ledger",
                    ledger,
                    "--activity-code",
                    "dtx2",
                    "--activity-name",
                    "麦麦积分抽奖",
                    "--draws",
                    "1",
                    "--cost",
                    "24",
                    "--preflight-ok",
                ]
            )
            self.assertEqual(code, 0)
            intent_id = out.split("intent_id：")[1].strip()

            code, _, err = run(
                ["guard", "authorize", "--ledger", ledger, "--intent-id", intent_id]
            )
            self.assertEqual(code, 2)
            self.assertIn("未获得明确授权", err)

            code, out, _ = run(
                [
                    "guard",
                    "authorize",
                    "--ledger",
                    ledger,
                    "--intent-id",
                    intent_id,
                    "--explicit",
                ]
            )
            self.assertEqual(code, 0)
            auth_id = out.split("auth_id：")[1].split("｜")[0].strip()

            code, out, _ = run(["guard", "begin", "--ledger", ledger, "--auth-id", auth_id])
            self.assertEqual(code, 0)
            attempt_id = out.split("attempt_id：")[1].split("\n")[0].strip()

            code, _, err = run(["guard", "begin", "--ledger", ledger, "--auth-id", auth_id])
            self.assertEqual(code, 2)
            self.assertIn("禁止盲目重试", err)

            code, out, _ = run(
                [
                    "guard",
                    "settle",
                    "--ledger",
                    ledger,
                    "--attempt-id",
                    attempt_id,
                    "--result",
                    str(FIXTURES / "draw_timeout.json"),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("状态未知", out)

            code, out, _ = run(["guard", "status", "--ledger", ledger])
            self.assertEqual(code, 0)
            self.assertIn("已冻结后续抽奖", out)

    def test_propose_without_preflight_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(
                [
                    "guard",
                    "propose",
                    "--ledger",
                    str(Path(tmp) / "ledger.jsonl"),
                    "--activity-code",
                    "dtx2",
                ]
            )
            self.assertEqual(code, 2)
            self.assertIn("前置校验未通过", err)


if __name__ == "__main__":
    unittest.main()
