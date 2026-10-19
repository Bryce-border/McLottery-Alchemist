"""中奖记录与抽奖结果解析测试（字段来自真实返回）。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.models import DrawResult, PrizePage  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class PrizePageTest(unittest.TestCase):
    def test_parse_prizes(self):
        page = PrizePage.from_mcp(load("prizes.json"))
        self.assertEqual(page.status_code, "SUCCESS")
        self.assertEqual(page.total_returned, 2)
        self.assertFalse(page.has_more)
        first = page.prizes[0]
        self.assertEqual(first.name, "小暑")
        self.assertEqual(first.status_text, "可用")
        self.assertEqual(first.type_text, "门票")
        self.assertEqual(first.time_remind_text, "")

    def test_empty_page_is_not_an_error(self):
        page = PrizePage.from_mcp({"data": {"status": {"code": "SUCCESS"}, "prizes": []}})
        self.assertEqual(page.total_returned, 0)


class DrawResultTest(unittest.TestCase):
    def test_win_payload(self):
        result = DrawResult.from_mcp(load("draw_win.json"))
        self.assertTrue(result.success)
        self.assertFalse(result.state_unknown)
        self.assertTrue(result.win)
        self.assertEqual(result.prizes[0].name, "下单立减1元券")

    def test_lose_payload(self):
        result = DrawResult.from_mcp(load("draw_lose.json"))
        self.assertTrue(result.success)
        self.assertFalse(result.win)
        self.assertEqual(result.prizes, ())

    def test_non_json_payload_is_unknown(self):
        result = DrawResult.from_mcp("timeout")
        self.assertFalse(result.raw_ok)
        self.assertTrue(result.state_unknown)


if __name__ == "__main__":
    unittest.main()
