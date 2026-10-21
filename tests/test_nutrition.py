"""营养组合测试。

重点：缺热量字段的餐品不得参与计算，也不得被估算补齐。
"""

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mclottery.nutrition import (  # noqa: E402
    NutritionFact,
    assess_day,
    parse_nutrition_foods,
    plan_meal,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class NutritionParseTest(unittest.TestCase):
    def setUp(self):
        self.facts = parse_nutrition_foods(load("nutrition_foods.json"))

    def test_parses_items_and_marks_missing_energy(self):
        # 6 条数据里 1 条缺 energy：保留条目用于提示「未参与计算」，但不参与组合。
        self.assertEqual(len(self.facts), 6)
        self.assertEqual(sum(1 for f in self.facts if f.has_energy), 5)

    def test_missing_field_is_recorded(self):
        raw = {"mealName": "神秘汉堡", "energy": "500", "protein": "20"}
        fact = NutritionFact.from_dict(raw)
        self.assertEqual(fact.energy_kcal, Decimal("500"))
        self.assertIn("fat", fact.missing_fields)
        self.assertIn("sodium", fact.missing_fields)

    def test_empty_payload_returns_empty(self):
        self.assertEqual(parse_nutrition_foods({"data": {}}), ())


class MealComboTest(unittest.TestCase):
    def setUp(self):
        self.facts = parse_nutrition_foods(load("nutrition_foods.json"))

    def test_combo_stays_close_to_target(self):
        combo, excluded = plan_meal(self.facts, Decimal("700"))
        self.assertIsNotNone(combo)
        self.assertLessEqual(combo.deviation_ratio, Decimal("0.12"))
        # 缺热量的麦旋风必须被排除，不能用估算值补齐
        self.assertEqual([f.name for f in excluded], ["麦旋风（缺热量字段）"])

    def test_combo_does_not_exceed_target_when_alternative_exists(self):
        combo, _ = plan_meal(self.facts, Decimal("700"))
        self.assertFalse(combo.over_target)

    def test_excluded_items_are_reported(self):
        facts = self.facts + (
            NutritionFact(name="无热量甜品", protein_g=Decimal("3")),
        )
        combo, excluded = plan_meal(facts, Decimal("700"))
        self.assertIsNotNone(combo)
        names = [f.name for f in excluded]
        self.assertIn("无热量甜品", names)
        self.assertIn("麦旋风（缺热量字段）", names)

    def test_no_data_returns_none(self):
        combo, _ = plan_meal((), Decimal("700"))
        self.assertIsNone(combo)

    def test_invalid_target_raises(self):
        with self.assertRaises(ValueError):
            plan_meal(self.facts, Decimal("0"))

    def test_max_items_is_respected(self):
        combo, _ = plan_meal(self.facts, Decimal("900"), max_items=1)
        self.assertEqual(len(combo.items), 1)


class DayAssessmentTest(unittest.TestCase):
    def test_over_target_status(self):
        facts = parse_nutrition_foods(load("nutrition_foods.json"))
        burger = [f for f in facts if f.name == "巨无霸"]
        report = assess_day(burger, Decimal("500"))
        self.assertEqual(report.status, "超出目标")
        self.assertEqual(report.remaining_kcal, Decimal("-50"))

    def test_missing_energy_returns_none(self):
        self.assertIsNone(assess_day([], Decimal("500")))


if __name__ == "__main__":
    unittest.main()
