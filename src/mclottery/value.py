"""兑换价值计算与商城横向比较。

关键取舍：麦当劳 MCP **不返回商品参考价值**（``price`` 是「所需金额」，
``point="0"`` 的条目多为付费活动，商品名里的「10.9 元」是券面价而非可白拿价值）。
因此本模块默认 ``value_basis = "none"``，只做定性比较，不生成「性价比第一名」。
只有调用方显式提供可信参考价时，才会计算「元/积分」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional, Sequence, Tuple

from .models import AccountSnapshot, Product


@dataclass(frozen=True)
class UnitValue:
    value: Optional[Decimal]
    status: str  # OK / NO_REFERENCE_VALUE / INVALID_POINTS
    note: str = ""

    @property
    def display(self) -> str:
        if self.value is None:
            return "无法计算（缺少可信参考价值）"
        return f"{self.value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)} 元/积分"


def calculate_unit_value(
    reference_value_cny: Optional[Any], points: Optional[Any]
) -> UnitValue:
    """单位积分价值 = 商品参考价值 ÷ 所需积分。

    只有商品价值可信、积分门槛有效且单位一致时才计算。
    """

    if points is None:
        return UnitValue(None, "INVALID_POINTS", "接口未返回所需积分")
    points_d = points if isinstance(points, Decimal) else Decimal(str(points))
    if points_d <= 0:
        return UnitValue(None, "INVALID_POINTS", "积分门槛为 0，非积分兑换商品")

    if reference_value_cny is None:
        return UnitValue(
            None,
            "NO_REFERENCE_VALUE",
            "缺少可信商品参考价值，按定性比较处理",
        )
    value_d = (
        reference_value_cny
        if isinstance(reference_value_cny, Decimal)
        else Decimal(str(reference_value_cny))
    )
    if value_d <= 0:
        return UnitValue(None, "NO_REFERENCE_VALUE", "参考价值无效")
    return UnitValue(value_d / points_d, "OK")


@dataclass(frozen=True)
class RankedProduct:
    product: Product
    unit_value: Optional[Decimal] = None
    note: str = ""

    @property
    def points(self) -> Decimal:
        return self.product.point or Decimal("0")


@dataclass(frozen=True)
class ExcludedProduct:
    product: Product
    reason: str


@dataclass(frozen=True)
class RedemptionComparison:
    affordable: Tuple[RankedProduct, ...] = ()
    short_of_points: Tuple[RankedProduct, ...] = ()
    excluded: Tuple[ExcludedProduct, ...] = ()
    value_basis: str = "none"  # none / reference
    notes: Tuple[str, ...] = ()
    available_points: Optional[Decimal] = None

    def has_options(self) -> bool:
        return bool(self.affordable)


def compare_redemption_options(
    products: Sequence[Product],
    account: Any,
    *,
    now: Optional[datetime] = None,
    reference_values: Optional[Mapping[int, Decimal]] = None,
    preferred_categories: Sequence[str] = (),
) -> RedemptionComparison:
    """筛选「当前真正可兑换」的商品并排序。

    排序规则（无参考价值时）：
    1. 命中用户偏好类目的优先；
    2. 兑换门槛从低到高。

    明确声明：门槛低 ≠ 性价比高。
    """

    now = now or datetime.now()
    if isinstance(account, AccountSnapshot):
        snapshot = account
    elif isinstance(account, Mapping):
        snapshot = AccountSnapshot.from_mcp(account)
    else:
        snapshot = AccountSnapshot()

    available = snapshot.available
    values = reference_values or {}
    preferred = {c.strip() for c in preferred_categories if c and c.strip()}

    affordable, short, excluded = [], [], []
    basis = "reference" if values else "none"
    notes = []

    for product in products:
        ok, reason = product.availability(now)
        if not ok:
            excluded.append(ExcludedProduct(product, reason))
            continue

        unit = calculate_unit_value(values.get(product.spu_id), product.point)

        if available is None:
            short.append(RankedProduct(product, unit.value, "积分余额未知，无法判断可兑换性"))
            continue

        if product.point is not None and available >= product.point:
            affordable.append(RankedProduct(product, unit.value))
        else:
            gap = product.point - available if product.point is not None else None
            short.append(
                RankedProduct(
                    product,
                    unit.value,
                    f"还差 {gap} 积分" if gap is not None else "所需积分未返回",
                )
            )

    def sort_key(item: RankedProduct):
        is_preferred = item.product.cat_name in preferred
        if basis == "reference" and item.unit_value is not None:
            return (0 if is_preferred else 1, -item.unit_value)
        return (0 if is_preferred else 1, item.points)

    affordable.sort(key=sort_key)
    short.sort(key=lambda item: item.points)

    if basis == "none":
        notes.append(
            "接口未提供商品参考价值，以下仅为「门槛 + 可兑换状态」定性排序，"
            "不代表性价比排名。"
        )
    if available is None:
        notes.append("积分余额未获取，无法判断可兑换性。")
    if not products:
        notes.append("商城接口未返回任何商品。")

    return RedemptionComparison(
        affordable=tuple(affordable),
        short_of_points=tuple(short),
        excluded=tuple(excluded),
        value_basis=basis,
        notes=tuple(notes),
        available_points=available,
    )
