"""抽奖守卫测试：授权、单次执行、未知状态冻结、台账持久化。"""

import json
import sys
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.guard import (  # noqa: E402
    AuthorizationRequired,
    DrawGuard,
    DrawLimitExceeded,
    GuardError,
    ReconcileRequired,
)
from mclottery.models import DrawResult  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 19, 12, 0, 0)


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_guard(tmpdir, **kwargs):
    ledger = Path(tmpdir) / "ledger.jsonl"
    kwargs.setdefault("clock", lambda: NOW)
    return DrawGuard(ledger, **kwargs)


def propose(guard, draws=1):
    return guard.propose(
        activity_code="dtx2",
        activity_name="麦麦积分抽奖",
        draws=draws,
        cost_per_draw=Decimal("24"),
        preflight_ok=True,
    )


class AuthorizationTest(unittest.TestCase):
    def test_propose_requires_preflight_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            with self.assertRaises(GuardError):
                guard.propose(
                    activity_code="dtx2",
                    activity_name="麦麦积分抽奖",
                    draws=1,
                    cost_per_draw=Decimal("24"),
                    preflight_ok=False,
                    blockers=("积分不足",),
                )

    def test_authorize_requires_explicit_consent(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            intent = propose(guard)
            with self.assertRaises(AuthorizationRequired):
                guard.authorize(intent.intent_id, explicit=False)
            auth = guard.authorize(intent.intent_id, explicit=True)
            self.assertEqual(auth.remaining, 1)

    def test_propose_rejects_too_many_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp, max_draws_per_authorization=2)
            with self.assertRaises(DrawLimitExceeded):
                propose(guard, draws=3)


class ExecutionTest(unittest.TestCase):
    def test_single_draw_then_exhausted(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard).intent_id, explicit=True)

            attempt = guard.begin(auth.auth_id)
            self.assertEqual(attempt.status, "IN_FLIGHT")
            with self.assertRaises(ReconcileRequired):
                guard.begin(auth.auth_id)  # 未结算前禁止第二次

            guard.settle(attempt.attempt_id, result=DrawResult.from_mcp(load("draw_win.json")))
            self.assertFalse(guard.status()["blocked"])  # 已结算，解冻
            with self.assertRaises(DrawLimitExceeded):
                guard.begin(auth.auth_id)  # 授权额度已用完

    def test_two_authorized_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard, draws=2).intent_id, explicit=True)

            first = guard.begin(auth.auth_id)
            guard.settle(first.attempt_id, result=DrawResult.from_mcp(load("draw_lose.json")))
            second = guard.begin(auth.auth_id)
            guard.settle(second.attempt_id, result=DrawResult.from_mcp(load("draw_win.json")))
            with self.assertRaises(DrawLimitExceeded):
                guard.begin(auth.auth_id)

            self.assertEqual(len(guard.attempts_today()), 2)

    def test_unknown_result_freezes_until_reconciled(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard).intent_id, explicit=True)
            attempt = guard.begin(auth.auth_id)

            result = DrawResult.from_mcp(load("draw_timeout.json"))
            self.assertTrue(result.state_unknown)
            settled = guard.settle(attempt.attempt_id, result=result)
            self.assertEqual(settled.status, "UNKNOWN")
            self.assertTrue(guard.status()["blocked"])

            with self.assertRaises(ReconcileRequired):
                guard.begin(auth.auth_id)

            guard.reconcile(attempt.attempt_id, "FAILED", note="已查询奖品记录，无新增")
            self.assertFalse(guard.status()["blocked"])

    def test_timeout_fixture_has_no_status_code(self):
        result = DrawResult.from_mcp(load("draw_timeout.json"))
        self.assertEqual(result.status_code, "")
        self.assertTrue(result.state_unknown)

    def test_failed_result_is_settled_not_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard).intent_id, explicit=True)
            attempt = guard.begin(auth.auth_id)
            payload = load("draw_lose.json")
            payload["data"]["status"] = {"code": "BUSY", "message": "操作太频繁，请稍后再试"}
            settled = guard.settle(attempt.attempt_id, result=DrawResult.from_mcp(payload))
            self.assertEqual(settled.status, "FAILED")
            self.assertEqual(settled.message, "操作太频繁，请稍后再试")
            self.assertFalse(guard.status()["blocked"])

    def test_revoke_blocks_remaining_draws(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard, draws=2).intent_id, explicit=True)
            guard.revoke(auth.auth_id)
            with self.assertRaises(AuthorizationRequired):
                guard.begin(auth.auth_id)

    def test_daily_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp, max_draws_per_day=2)
            for _ in range(2):
                auth = guard.authorize(propose(guard).intent_id, explicit=True)
                attempt = guard.begin(auth.auth_id)
                guard.settle(attempt.attempt_id, result=DrawResult.from_mcp(load("draw_lose.json")))
            auth = guard.authorize(propose(guard).intent_id, explicit=True)
            with self.assertRaises(DrawLimitExceeded):
                guard.begin(auth.auth_id)


class LedgerTest(unittest.TestCase):
    def test_state_survives_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            auth = guard.authorize(propose(guard).intent_id, explicit=True)
            attempt = guard.begin(auth.auth_id)
            guard.settle(attempt.attempt_id, result=DrawResult.from_mcp(load("draw_timeout.json")))

            reloaded = DrawGuard(Path(tmp) / "ledger.jsonl", clock=lambda: NOW)
            self.assertTrue(reloaded.status()["blocked"])
            self.assertEqual(len(reloaded.attempts_today()), 1)

    def test_ledger_has_no_account_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            guard = make_guard(tmp)
            guard.authorize(propose(guard).intent_id, explicit=True)
            text = (Path(tmp) / "ledger.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("TEST_ACCOUNT_ID", text)
            self.assertNotIn("availab", text.lower())
            self.assertNotIn("Bearer", text)
            self.assertIn("dtx2", text)

    def test_corrupted_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            path.write_text("{not json}\n", encoding="utf-8")
            guard = DrawGuard(path, clock=lambda: NOW)
            self.assertFalse(guard.status()["blocked"])


if __name__ == "__main__":
    unittest.main()
