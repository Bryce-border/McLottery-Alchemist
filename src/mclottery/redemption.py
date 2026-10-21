"""积分兑换（mall-create-order）的预检与返回解析。

真实契约（2026-10-21 由官方工具文档确认）：

入参：``skuId``（必填）、``count``（默认 1）、``addressId``（``spuCategory="2"`` 时必填）、
``spuCategory``（"1" 虚拟 / "2" 实物）。

三个容易踩的坑：

1. **兑换 ≠ 免费。** 返回里同时有 ``realTotalPoints`` 与 ``realTotalAmount``，
   示例就是 0.01 元现金 + 0 积分。只报积分会误导用户。
2. **下单后是「待支付」，不是「已完成」。** ``orderDetailVo.leftPaySecond`` 是剩余支付秒数，
   超时会自动取消，不能把「下单成功」当成「兑换成功」。
3. **实物必须带 addressId。** 缺地址不是「稍后补」，而是直接无法下单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence, Tuple

from .models import to_decimal, unwrap

VIRTUAL = "1"
PHYSICAL = "2"


@dataclass(frozen=True)
class RedemptionRequest:
    """一次兑换的输入。商品信息来自列表/详情，余额来自 query-my-account。"""

    sku_id: Optional[str]
    spu_name: str = "未命名商品"
    sku_name: str = ""
    spu_category: str = ""
    points_required: Optional[Decimal] = None
    cash_price: Optional[Decimal] = None
    count: int = 1
    address_id: Optional[str] = None
    available_points: Optional[Decimal] = None

    @property
    def total_points(self) -> Optional[Decimal]:
        if self.points_required is None:
            return None
        return (self.points_required * self.count).quantize(Decimal("0.01"))

    @property
    def total_cash(self) -> Optional[Decimal]:
        if self.cash_price is None:
            return None
        return (self.cash_price * self.count).quantize(Decimal("0.01"))

    @property
    def remaining_points(self) -> Optional[Decimal]:
        if self.available_points is None or self.total_points is None:
            return None
        return (self.available_points - self.total_points).quantize(Decimal("0.01"))


@dataclass(frozen=True)
class RedemptionCheck:
    ready: bool
    blockers: Tuple[str, ...]
    warnings: Tuple[str, ...]
    totals: Mapping[str, Any]
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "totals": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in self.totals.items()},
            "notes": list(self.notes),
        }


def preflight_redemption(request: RedemptionRequest) -> RedemptionCheck:
    """下单前能本地判断的部分：品类、地址、数量、积分、双金额。"""

    blockers: list[str] = []
    warnings: list[str] = []
    notes: list[str] = [
        "库存、券码发放与最终资格以 mall-create-order 返回为准。",
        "兑换下单后通常进入「待支付」，需在 leftPaySecond 内完成，否则自动取消。",
    ]

    if not request.sku_id:
        blockers.append("缺少 skuId：必须先调用商品详情，拿到具体规格 ID。")
    if request.count <= 0:
        blockers.append("兑换数量必须大于 0。")
    if request.spu_category not in (VIRTUAL, PHYSICAL):
        blockers.append('缺少或非法的 spuCategory：只能是 "1"（虚拟）或 "2"（实物）。')
    if request.spu_category == PHYSICAL and not request.address_id:
        blockers.append("实物商品（spuCategory=2）必须提供 addressId。")

    if request.points_required is None and request.cash_price is None:
        blockers.append("商品所需积分与现金价格都未知，不能下单。")

    total_points = request.total_points
    total_cash = request.total_cash

    if total_points is not None and request.available_points is not None:
        if request.remaining_points is not None and request.remaining_points < 0:
            blockers.append(
                f"积分不足：需要 {total_points}，可用 {request.available_points}，"
                f"还差 {abs(request.remaining_points)}。"
            )
        elif request.remaining_points is not None:
            warnings.append(f"兑换后预计剩余 {request.remaining_points} 积分（预计值）。")
    elif total_points is not None:
        warnings.append("未提供可用积分，无法在本地判断是否足够。")

    if total_cash is not None and total_cash > 0:
        if total_points is None or total_points == 0:
            warnings.append(
                f"这是现金支付，不是积分兑换：应付 {total_cash} 元。"
            )
        else:
            warnings.append(
                f"需要积分 + 现金：{total_points} 积分 + {total_cash} 元，二者都要展示。"
            )

    if request.count > 1:
        warnings.append(f"本次兑换 {request.count} 件，库存按件扣减。")

    return RedemptionCheck(
        ready=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        totals={
            "sku_id": request.sku_id,
            "spu_name": request.spu_name,
            "sku_name": request.sku_name,
            "spu_category": request.spu_category,
            "count": request.count,
            "total_points": total_points,
            "total_cash": total_cash,
            "available_points": request.available_points,
            "remaining_points": request.remaining_points,
        },
        notes=tuple(notes),
    )


@dataclass(frozen=True)
class RedeemedGood:
    spu_id: Optional[str]
    sku_id: Optional[str]
    spu_name: str
    sku_name: str
    count: int
    points: Optional[Decimal]
    price: Optional[Decimal]


@dataclass(frozen=True)
class RedemptionResult:
    """mall-create-order 的可用视图。缺失字段保留 None，不推测。"""

    order_id: Optional[str]
    order_status: Optional[int]
    status_title: Optional[str]
    status_sub_title: Optional[str]
    left_pay_seconds: Optional[int]
    real_total_points: Optional[Decimal]
    real_total_amount: Optional[Decimal]
    pay_id: Optional[str]
    address_summary: Optional[str]
    goods: Tuple[RedeemedGood, ...] = ()
    missing_fields: Tuple[str, ...] = ()
    raw_address_phone_masked: bool = True

    @property
    def awaiting_payment(self) -> Optional[bool]:
        if self.status_title is None:
            return None
        return "待支付" in self.status_title

    @property
    def payment_window_text(self) -> str:
        if self.left_pay_seconds is None:
            return "接口未返回支付剩余时间，以订单页为准。"
        minutes = self.left_pay_seconds // 60
        seconds = self.left_pay_seconds % 60
        return f"剩余支付时间约 {minutes} 分 {seconds} 秒，超时订单会自动取消。"

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "order_status": self.order_status,
            "status_title": self.status_title,
            "status_sub_title": self.status_sub_title,
            "left_pay_seconds": self.left_pay_seconds,
            "real_total_points": self.real_total_points,
            "real_total_amount": self.real_total_amount,
            "pay_id": self.pay_id,
            "address_summary": self.address_summary,
            "goods": [
                {
                    "spu_name": g.spu_name,
                    "sku_name": g.sku_name,
                    "count": g.count,
                    "points": g.points,
                    "price": g.price,
                }
                for g in self.goods
            ],
            "missing_fields": list(self.missing_fields),
        }


def _text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value).strip()


def parse_redemption_result(payload: Any) -> RedemptionResult:
    data = unwrap(payload)
    detail = data.get("orderDetailVo")
    detail = detail if isinstance(detail, Mapping) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in detail and detail[key] not in (None, ""):
                return detail[key]
            if key in data and data[key] not in (None, ""):
                return data[key]
        return None

    missing = []
    order_id = _text(pick("orderId"))
    status_title = _text(pick("orderStatusTitle"))
    left_pay = pick("leftPaySecond")
    points = to_decimal(pick("realTotalPoints"))
    amount = to_decimal(pick("realTotalAmount"))
    pay_id = _text(pick("payId"))

    for label, value in (
        ("orderId", order_id),
        ("orderStatusTitle", status_title),
        ("realTotalPoints", points),
        ("realTotalAmount", amount),
    ):
        if value is None:
            missing.append(label)

    goods: list[RedeemedGood] = []
    for item in detail.get("goods", []) if isinstance(detail.get("goods"), list) else []:
        if not isinstance(item, Mapping):
            continue
        count_raw = to_decimal(item.get("count"))
        goods.append(
            RedeemedGood(
                spu_id=_text(item.get("spuId")),
                sku_id=_text(item.get("skuId")),
                spu_name=_text(item.get("spuName")) or "未命名商品",
                sku_name=_text(item.get("skuName")) or "",
                count=int(count_raw) if count_raw is not None else 1,
                points=to_decimal(item.get("points")),
                price=to_decimal(item.get("price")),
            )
        )

    address = data.get("addressVO")
    address_summary = None
    if isinstance(address, Mapping):
        parts = [
            _text(address.get("contactName")),
            _text(address.get("phone")),
            _text(address.get("fullAddress")),
        ]
        address_summary = "｜".join(p for p in parts if p) or None

    status_raw = pick("orderStatus")
    status_int = None
    if status_raw is not None:
        try:
            status_int = int(str(status_raw))
        except (TypeError, ValueError):
            status_int = None

    left_int = None
    if left_pay is not None:
        try:
            left_int = int(str(left_pay))
        except (TypeError, ValueError):
            left_int = None

    return RedemptionResult(
        order_id=order_id,
        order_status=status_int,
        status_title=status_title,
        status_sub_title=_text(pick("orderStatusSubTitle")),
        left_pay_seconds=left_int,
        real_total_points=points,
        real_total_amount=amount,
        pay_id=pay_id,
        address_summary=address_summary,
        goods=tuple(goods),
        missing_fields=tuple(missing),
    )


def describe_result(result: RedemptionResult) -> Tuple[str, ...]:
    """给用户看的固定口径，避免把「待支付」说成「兑换成功」。"""

    lines = []
    if result.order_id:
        lines.append(f"兑换订单号：{result.order_id}")
    else:
        lines.append("兑换订单号：接口未返回，不能认定已成功。")

    if result.status_title:
        lines.append(f"订单状态：{result.status_title}")
        if result.status_sub_title:
            lines.append(f"接口提示：{result.status_sub_title}")
    else:
        lines.append("订单状态：接口未返回，不能假设兑换成功。")

    points = "接口未返回" if result.real_total_points is None else f"{result.real_total_points} 积分"
    cash = "接口未返回" if result.real_total_amount is None else f"{result.real_total_amount} 元"
    lines.append(f"实际支出：{points} + {cash}")
    lines.append(result.payment_window_text)

    if result.goods:
        names = "、".join(
            f"{g.spu_name}{(' ' + g.sku_name) if g.sku_name else ''} ×{g.count}"
            for g in result.goods
        )
        lines.append(f"商品：{names}")
    if result.address_summary:
        lines.append(f"收货信息：{result.address_summary}")

    lines.append("支付在官方页面完成；我不代付、不保存支付凭证。")
    return tuple(lines)
