"""营养数据与热量组合（list-nutrition-foods 的确定性计算）。

设计约束：

1. 营养字段一律来自接口，缺失保留 ``None`` 并登记到 ``missing_fields``；
   不用「同类餐品估算」补齐热量。
2. 组合推荐只做「在给定热量目标下最接近的一餐」，不输出减脂/增肌等医学建议，
   也不承诺减重效果。
3. 份量口径以接口返回为准；接口未给份量时，所有数值都标注为「按接口默认份」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .models import to_decimal, unwrap

ENERGY_KEYS = ("energy", "calorie", "calories", "kcal", "energyKcal", "heat")
PROTEIN_KEYS = ("protein", "proteins")
FAT_KEYS = ("fat", "fats", "totalFat")
CARB_KEYS = ("carbohydrate", "carbohydrates", "carb", "carbs")
SODIUM_KEYS = ("sodium", "na")
CALCIUM_KEYS = ("calcium", "ca")
NAME_KEYS = ("name", "productName", "mealName", "foodName", "title")
CODE_KEYS = ("code", "mealCode", "productCode", "skuId", "id")


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


@dataclass(frozen=True)
class NutritionFact:
    """单个餐品的营养信息，仅保留接口给出的字段。"""

    name: str
    code: Optional[str] = None
    energy_kcal: Optional[Decimal] = None
    protein_g: Optional[Decimal] = None
    fat_g: Optional[Decimal] = None
    carbohydrate_g: Optional[Decimal] = None
    sodium_mg: Optional[Decimal] = None
    calcium_mg: Optional[Decimal] = None
    serving: Optional[str] = None
    missing_fields: Tuple[str, ...] = ()

    @property
    def has_energy(self) -> bool:
        return self.energy_kcal is not None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NutritionFact":
        name = str(_pick(raw, NAME_KEYS) or "未命名餐品").strip()
        code = _pick(raw, CODE_KEYS)
        energy = to_decimal(_pick(raw, ENERGY_KEYS))
        protein = to_decimal(_pick(raw, PROTEIN_KEYS))
        fat = to_decimal(_pick(raw, FAT_KEYS))
        carb = to_decimal(_pick(raw, CARB_KEYS))
        sodium = to_decimal(_pick(raw, SODIUM_KEYS))
        calcium = to_decimal(_pick(raw, CALCIUM_KEYS))
        serving = _pick(raw, ("serving", "portion", "unit", "spec", "weight"))

        missing = []
        for label, value in (
            ("energy", energy),
            ("protein", protein),
            ("fat", fat),
            ("carbohydrate", carb),
            ("sodium", sodium),
            ("calcium", calcium),
        ):
            if value is None:
                missing.append(label)

        return cls(
            name=name,
            code=str(code) if code is not None else None,
            energy_kcal=energy,
            protein_g=protein,
            fat_g=fat,
            carbohydrate_g=carb,
            sodium_mg=sodium,
            calcium_mg=calcium,
            serving=str(serving) if serving is not None else None,
            missing_fields=tuple(missing),
        )


def _food_list(payload: Any) -> Iterable[Mapping[str, Any]]:
    """兼容 data.list / data.items / data.foods / 顶层数组。"""

    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, Mapping)]
    data = unwrap(payload)
    for key in ("list", "items", "foods", "products", "records", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, Mapping)]
    return []


def parse_nutrition_foods(payload: Any) -> Tuple[NutritionFact, ...]:
    facts = [NutritionFact.from_dict(raw) for raw in _food_list(payload)]
    return tuple(f for f in facts if f.has_energy or f.protein_g is not None)


@dataclass(frozen=True)
class NutritionTotals:
    energy_kcal: Optional[Decimal] = None
    protein_g: Optional[Decimal] = None
    fat_g: Optional[Decimal] = None
    carbohydrate_g: Optional[Decimal] = None
    sodium_mg: Optional[Decimal] = None
    calcium_mg: Optional[Decimal] = None

    @property
    def complete(self) -> bool:
        return self.energy_kcal is not None

    def as_dict(self) -> dict:
        return {
            "energy_kcal": self.energy_kcal,
            "protein_g": self.protein_g,
            "fat_g": self.fat_g,
            "carbohydrate_g": self.carbohydrate_g,
            "sodium_mg": self.sodium_mg,
            "calcium_mg": self.calcium_mg,
        }


def _sum(facts: Sequence[NutritionFact]) -> NutritionTotals:
    if not facts:
        return NutritionTotals()

    def total(attr: str) -> Optional[Decimal]:
        values = [getattr(f, attr) for f in facts]
        if any(v is None for v in values):
            return None
        return sum(values, Decimal("0"))

    return NutritionTotals(
        energy_kcal=total("energy_kcal"),
        protein_g=total("protein_g"),
        fat_g=total("fat_g"),
        carbohydrate_g=total("carbohydrate_g"),
        sodium_mg=total("sodium_mg"),
        calcium_mg=total("calcium_mg"),
    )


@dataclass(frozen=True)
class MealCombo:
    """一次组合建议：1–3 件餐品，热量尽量接近目标。"""

    items: Tuple[NutritionFact, ...]
    totals: NutritionTotals
    target_kcal: Decimal
    deviation_kcal: Decimal
    deviation_ratio: Decimal
    over_target: bool
    notes: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "items": [
                {
                    "name": f.name,
                    "code": f.code,
                    "energy_kcal": f.energy_kcal,
                    "protein_g": f.protein_g,
                    "fat_g": f.fat_g,
                    "carbohydrate_g": f.carbohydrate_g,
                    "sodium_mg": f.sodium_mg,
                    "serving": f.serving,
                }
                for f in self.items
            ],
            "totals": self.totals.as_dict(),
            "target_kcal": self.target_kcal,
            "deviation_kcal": self.deviation_kcal,
            "deviation_ratio": self.deviation_ratio,
            "over_target": self.over_target,
            "notes": list(self.notes),
        }


def plan_meal(
    facts: Sequence[NutritionFact],
    target_kcal: Decimal,
    *,
    max_items: int = 3,
    max_candidates: int = 40,
    tolerance_ratio: Decimal = Decimal("0.1"),
    prefer_high_protein: bool = False,
) -> Tuple[Optional[MealCombo], Tuple[NutritionFact, ...]]:
    """在给定热量目标下挑一餐。

    返回 ``(最优组合, 被排除的条目)``。被排除的是热量缺失的餐品——不是不好，
    而是无法参与计算，必须如实告知用户「这些没算进来」。
    """

    if target_kcal <= 0:
        raise ValueError("目标热量必须大于 0")

    excluded = tuple(f for f in facts if not f.has_energy)
    candidates = [f for f in facts if f.has_energy]
    if not candidates:
        return None, excluded

    # 候选过多时按热量升序截断，保证组合可枚举且偏向小份搭配。
    candidates.sort(key=lambda f: (f.energy_kcal, f.name))
    candidates = candidates[: max(1, max_candidates)]

    best: Optional[MealCombo] = None

    def score(combo: MealCombo) -> Tuple[Decimal, Decimal]:
        # 优先不过量（超目标更差），其次看偏差绝对值，蛋白质偏好只作末位权重。
        penalty = abs(combo.deviation_kcal) * (
            Decimal("2") if combo.over_target else Decimal("1")
        )
        protein_bonus = Decimal("0")
        if prefer_high_protein and combo.totals.protein_g is not None:
            protein_bonus = -combo.totals.protein_g / Decimal("100")
        return (penalty - protein_bonus, combo.deviation_kcal)

    for size in range(1, max(1, max_items) + 1):
        for group in combinations(candidates, size):
            totals = _sum(group)
            if totals.energy_kcal is None:
                continue
            deviation = totals.energy_kcal - target_kcal
            ratio = (abs(deviation) / target_kcal).quantize(Decimal("0.0001"))
            combo = MealCombo(
                items=tuple(group),
                totals=totals,
                target_kcal=target_kcal,
                deviation_kcal=deviation,
                deviation_ratio=ratio,
                over_target=deviation > 0,
            )
            if best is None or score(combo) < score(best):
                best = combo

    if best is not None:
        notes = [
            "热量与营养数值来自 list-nutrition-foods，份量口径以接口返回为准。",
            "仅为热量搭配建议，不构成营养、医疗或减重建议。",
        ]
        if best.deviation_ratio > tolerance_ratio:
            notes.append(
                f"最接近的组合仍偏离目标 {best.deviation_kcal} kcal，"
                "说明当前菜单里没有更合适的单品组合。"
            )
        if excluded:
            notes.append(f"有 {len(excluded)} 个餐品因缺少热量字段未参与组合计算。")
        best = MealCombo(
            items=best.items,
            totals=best.totals,
            target_kcal=best.target_kcal,
            deviation_kcal=best.deviation_kcal,
            deviation_ratio=best.deviation_ratio,
            over_target=best.over_target,
            notes=tuple(notes),
        )
    return best, excluded


@dataclass(frozen=True)
class DayAssessment:
    """一天摄入与目标热量的对照（只做算术，不做健康诊断）。"""

    totals: NutritionTotals
    target_kcal: Decimal
    remaining_kcal: Decimal
    ratio: Decimal
    status: str
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "totals": self.totals.as_dict(),
            "target_kcal": self.target_kcal,
            "remaining_kcal": self.remaining_kcal,
            "ratio": self.ratio,
            "status": self.status,
            "notes": list(self.notes),
        }


def assess_day(
    facts: Sequence[NutritionFact], target_kcal: Decimal
) -> Optional[DayAssessment]:
    if target_kcal <= 0:
        raise ValueError("目标热量必须大于 0")
    totals = _sum(facts)
    if totals.energy_kcal is None:
        return None
    remaining = target_kcal - totals.energy_kcal
    ratio = (totals.energy_kcal / target_kcal).quantize(Decimal("0.0001"))
    if remaining < 0:
        status = "超出目标"
    elif ratio >= Decimal("0.85"):
        status = "接近目标"
    else:
        status = "低于目标"
    notes = ["按接口默认份累加；实际份量与配料会影响结果。"]
    sodium = totals.sodium_mg
    if sodium is not None and sodium > Decimal("2000"):
        notes.append("钠合计已超过 2000mg 的通用参考值，注意搭配。")
    return DayAssessment(
        totals=totals,
        target_kcal=target_kcal,
        remaining_kcal=remaining,
        ratio=ratio,
        status=status,
        notes=tuple(notes),
    )
