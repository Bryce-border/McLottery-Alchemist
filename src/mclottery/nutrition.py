"""营养数据与热量组合（list-nutrition-foods 的确定性计算）。

**真实返回是 TOON 紧凑格式，不是 JSON 数组**（官方文档 4.2.1）：

```
data: "[1]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
       猪柳麦满分,null,1288,308,16,16,24,781,213"
```

设计约束：

1. 营养字段一律来自接口，缺失保留 ``None`` 并登记到 ``missing_fields``；
   不用「同类餐品估算」补齐热量。
2. 接口同时给 ``energyKj`` 与 ``energyKcal``：**优先用 kcal**，只有 kJ 时才除以 4.184 并标注换算。
3. 组合推荐只做「在给定热量目标下最接近的一餐」，不输出减脂/增肌等医学建议。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .models import to_decimal, unwrap

KJ_PER_KCAL = Decimal("4.184")

ENERGY_KEYS = ("energyKcal", "energy", "calorie", "calories", "kcal", "energyKcalValue", "heat")
ENERGY_KJ_KEYS = ("energyKj", "energyKJ", "kj")
PROTEIN_KEYS = ("protein", "proteins")
FAT_KEYS = ("fat", "fats", "totalFat")
CARB_KEYS = ("carbohydrate", "carbohydrates", "carb", "carbs")
SODIUM_KEYS = ("sodium", "na")
CALCIUM_KEYS = ("calcium", "ca")
NAME_KEYS = ("productName", "name", "mealName", "foodName", "title")
DESC_KEYS = ("nutritionDescription", "description", "desc")
CODE_KEYS = ("code", "mealCode", "productCode", "skuId", "id")

_TOON_HEADER = re.compile(r"^\s*\[(\d+)\]\{([^}]*)\}\s*:\s*$")


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


@dataclass(frozen=True)
class ToonTable:
    columns: Tuple[str, ...]
    rows: Tuple[Mapping[str, str], ...]

    def as_dicts(self) -> Tuple[Mapping[str, str], ...]:
        return self.rows


def _split_row(line: str) -> list[str]:
    """按逗号切分，支持双引号包裹（TOON 对含逗号的文本会加引号）。"""

    cells: list[str] = []
    buf: list[str] = []
    in_quotes = False
    for char in line:
        if char == '"':
            in_quotes = not in_quotes
            buf.append(char)
        elif char == "," and not in_quotes:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(char)
    cells.append("".join(buf).strip())
    return [c[1:-1] if len(c) >= 2 and c[0] == c[-1] == '"' else c for c in cells]


def parse_toon_table(text: str) -> Optional[ToonTable]:
    """解析 TOON 表格；格式不符返回 None（不猜结构）。"""

    if not text or "{" not in text:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    header = _TOON_HEADER.match(lines[0])
    if not header:
        return None
    expected = int(header.group(1))
    columns = tuple(c.strip() for c in header.group(2).split(",") if c.strip())
    rows: list[Mapping[str, str]] = []
    for line in lines[1:]:
        cells = _split_row(line)
        if len(cells) < len(columns):
            cells += [""] * (len(columns) - len(cells))
        rows.append(dict(zip(columns, cells)))
        if len(rows) >= expected:
            break
    return ToonTable(columns=columns, rows=tuple(rows))


@dataclass(frozen=True)
class NutritionFact:
    """单个餐品的营养信息，仅保留接口给出的字段。"""

    name: str
    code: Optional[str] = None
    energy_kcal: Optional[Decimal] = None
    energy_kj: Optional[Decimal] = None
    protein_g: Optional[Decimal] = None
    fat_g: Optional[Decimal] = None
    carbohydrate_g: Optional[Decimal] = None
    sodium_mg: Optional[Decimal] = None
    calcium_mg: Optional[Decimal] = None
    description: Optional[str] = None
    serving: Optional[str] = None
    kcal_converted: bool = False
    missing_fields: Tuple[str, ...] = ()

    @property
    def has_energy(self) -> bool:
        return self.energy_kcal is not None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NutritionFact":
        name = str(_pick(raw, NAME_KEYS) or "未命名餐品").strip()
        code = _pick(raw, CODE_KEYS)
        kcal = to_decimal(_pick(raw, ENERGY_KEYS))
        kj = to_decimal(_pick(raw, ENERGY_KJ_KEYS))
        converted = False
        if kcal is None and kj is not None:
            kcal = (kj / KJ_PER_KCAL).quantize(Decimal("0.1"))
            converted = True
        protein = to_decimal(_pick(raw, PROTEIN_KEYS))
        fat = to_decimal(_pick(raw, FAT_KEYS))
        carb = to_decimal(_pick(raw, CARB_KEYS))
        sodium = to_decimal(_pick(raw, SODIUM_KEYS))
        calcium = to_decimal(_pick(raw, CALCIUM_KEYS))
        description = _pick(raw, DESC_KEYS)
        serving = _pick(raw, ("serving", "portion", "unit", "spec", "weight"))

        missing = []
        for label, value in (
            ("energy", kcal),
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
            energy_kcal=kcal,
            energy_kj=kj,
            protein_g=protein,
            fat_g=fat,
            carbohydrate_g=carb,
            sodium_mg=sodium,
            calcium_mg=calcium,
            description=str(description) if description not in (None, "null") else None,
            serving=str(serving) if serving is not None else None,
            kcal_converted=converted,
            missing_fields=tuple(missing),
        )


def _food_list(payload: Any) -> Iterable[Mapping[str, Any]]:
    """兼容 TOON 字符串、data.list / data.items / 顶层数组。"""

    if isinstance(payload, str):
        table = parse_toon_table(payload)
        return table.rows if table else []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, Mapping)]
    data = unwrap(payload)
    if isinstance(data, str):
        table = parse_toon_table(data)
        return table.rows if table else []
    for key in ("list", "items", "foods", "products", "records", "data"):
        value = data.get(key)
        if isinstance(value, str):
            table = parse_toon_table(value)
            if table:
                return table.rows
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
