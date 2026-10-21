"""优惠券匹配与「这一单怎么付最省」（纯算术，不承诺门店可用）。

真实约束（见 references/coupon-strategy.md）：

* `query-my-coupons` 返回的是**卡包券**，不校验门店与订单可用性；
  要判断「这单能不能用」必须查 `query-store-coupons`。
* 麦当劳一单通常只能用一张券。接口没有明确支持叠加时，本模块**只选一张**，
  并明确标注「不叠加」。
* 券面金额、门槛、适用范围缺失时一律标记为「待确认」，不猜测、不默认无门槛。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .models import to_decimal, unwrap

ID_KEYS = ("couponId", "couponCode", "id", "code")
NAME_KEYS = ("couponName", "name", "title", "couponTitle")
TYPE_KEYS = ("couponTypeText", "typeText", "couponType", "type")
THRESHOLD_KEYS = ("threshold", "useCondition", "minAmount", "conditionAmount", "limitAmount", "useThreshold")
DISCOUNT_KEYS = ("discountAmount", "reduceAmount", "faceValue", "amount", "couponAmount")
RATE_KEYS = ("discountRate", "rate", "discount", "zk")
MAX_DISCOUNT_KEYS = ("maxDiscountAmount", "maxDiscount", "discountUpper")
VALID_KEYS = ("validUntil", "endTime", "expireTime", "useEndTime", "validEndTime")
STATUS_KEYS = ("statusText", "status", "useStatusText")
SCOPE_KEYS = ("scope", "useScope", "storeScope", "applicableScope")

_FULL_RE = re.compile(r"满\s*(\d+(?:\.\d+)?)")
_OFF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*折")
_MINUS_RE = re.compile(r"减\s*(\d+(?:\.\d+)?)")
_FREE_DELIVERY_RE = re.compile(r"免配送|免运费|配送费")


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _text(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    return str(value).strip()


def _threshold(value: Any) -> Optional[Decimal]:
    direct = to_decimal(value)
    if direct is not None:
        return direct
    text = _text(value)
    if text:
        match = _FULL_RE.search(text)
        if match:
            return Decimal(match.group(1))
    return None


def _discount(value: Any, text: str) -> Optional[Decimal]:
    direct = to_decimal(value)
    if direct is not None:
        return direct
    match = _MINUS_RE.search(text)
    if match:
        return Decimal(match.group(1))
    return None


def _rate(value: Any, text: str) -> Optional[Decimal]:
    direct = to_decimal(value)
    if direct is not None:
        # 接口可能返回 7.5（折）或 0.75（比例），统一成「实付比例」。
        return direct / Decimal("10") if direct > 1 else direct
    match = _OFF_RE.search(text)
    if match:
        return Decimal(match.group(1)) / Decimal("10")
    return None


@dataclass(frozen=True)
class Coupon:
    coupon_id: Optional[str]
    name: str
    type_text: str
    threshold: Optional[Decimal] = None
    amount_off: Optional[Decimal] = None
    pay_rate: Optional[Decimal] = None
    max_discount: Optional[Decimal] = None
    free_delivery: bool = False
    scope: Optional[str] = None
    valid_until: Optional[str] = None
    status_text: Optional[str] = None
    missing_fields: Tuple[str, ...] = ()

    @property
    def kind(self) -> str:
        if self.free_delivery:
            return "免配送"
        if self.pay_rate is not None:
            return "折扣"
        if self.amount_off is not None:
            return "立减"
        return "待确认"

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Coupon":
        text = " ".join(
            str(v) for v in raw.values() if isinstance(v, (str, int, float))
        )
        name = _text(_pick(raw, NAME_KEYS)) or "未命名优惠券"
        type_text = _text(_pick(raw, TYPE_KEYS)) or ""
        threshold = _threshold(_pick(raw, THRESHOLD_KEYS))
        rate = _rate(_pick(raw, RATE_KEYS), text)
        amount = _discount(_pick(raw, DISCOUNT_KEYS), text)
        free_delivery = bool(_FREE_DELIVERY_RE.search(text)) and amount is None and rate is None
        max_discount = to_decimal(_pick(raw, MAX_DISCOUNT_KEYS))

        missing = []
        if threshold is None:
            missing.append("threshold")
        if amount is None and rate is None and not free_delivery:
            missing.append("discount")

        return cls(
            coupon_id=_text(_pick(raw, ID_KEYS)),
            name=name,
            type_text=type_text,
            threshold=threshold,
            amount_off=amount,
            pay_rate=rate,
            max_discount=max_discount,
            free_delivery=free_delivery,
            scope=_text(_pick(raw, SCOPE_KEYS)),
            valid_until=_text(_pick(raw, VALID_KEYS)),
            status_text=_text(_pick(raw, STATUS_KEYS)),
            missing_fields=tuple(missing),
        )


def _coupon_list(payload: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, Mapping)]
    data = unwrap(payload)
    for key in ("list", "items", "coupons", "records", "couponList"):
        value = data.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, Mapping)]
    return []


def parse_coupons(payload: Any) -> Tuple[Coupon, ...]:
    return tuple(Coupon.from_dict(raw) for raw in _coupon_list(payload))


@dataclass(frozen=True)
class CouponQuote:
    coupon: Coupon
    status: str
    payable: Optional[Decimal]
    saved: Optional[Decimal]
    reason: str

    def as_dict(self) -> dict:
        return {
            "name": self.coupon.name,
            "kind": self.coupon.kind,
            "status": self.status,
            "payable": self.payable,
            "saved": self.saved,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CouponDecision:
    cart_total: Decimal
    delivery_fee: Decimal
    best: Optional[CouponQuote]
    quotes: Tuple[CouponQuote, ...]
    unverified: Tuple[CouponQuote, ...]
    assumptions: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "cart_total": self.cart_total,
            "delivery_fee": self.delivery_fee,
            "best": self.best.as_dict() if self.best else None,
            "quotes": [q.as_dict() for q in self.quotes],
            "unverified": [q.as_dict() for q in self.unverified],
            "assumptions": list(self.assumptions),
        }


def quote_coupon(coupon: Coupon, cart_total: Decimal, delivery_fee: Decimal) -> CouponQuote:
    if coupon.threshold is None:
        return CouponQuote(coupon, "待确认", None, None, "接口未给出使用门槛，需在门店券列表核实")
    if cart_total < coupon.threshold:
        gap = (coupon.threshold - cart_total).quantize(Decimal("0.01"))
        return CouponQuote(
            coupon, "不适用", None, None, f"还差 {gap} 元达到门槛 {coupon.threshold} 元"
        )
    if coupon.free_delivery:
        if delivery_fee <= 0:
            return CouponQuote(coupon, "不适用", cart_total, Decimal("0"), "本单没有配送费")
        return CouponQuote(coupon, "适用", cart_total, delivery_fee, "免掉配送费")
    if coupon.pay_rate is not None:
        saved = (cart_total * (Decimal("1") - coupon.pay_rate)).quantize(Decimal("0.01"))
        if coupon.max_discount is not None:
            saved = min(saved, coupon.max_discount)
        payable = (cart_total - saved + delivery_fee).quantize(Decimal("0.01"))
        return CouponQuote(coupon, "适用", payable, saved, f"按 {coupon.pay_rate * 10} 折计算")
    if coupon.amount_off is not None:
        saved = min(coupon.amount_off, cart_total)
        payable = (cart_total - saved + delivery_fee).quantize(Decimal("0.01"))
        return CouponQuote(coupon, "适用", payable, saved, f"立减 {saved} 元")
    return CouponQuote(coupon, "待确认", None, None, "未识别出券面优惠形式")


def best_coupon(
    cart_total: Decimal,
    coupons: Sequence[Coupon],
    *,
    delivery_fee: Decimal = Decimal("0"),
) -> CouponDecision:
    if cart_total < 0:
        raise ValueError("订单金额不能为负")
    if delivery_fee < 0:
        raise ValueError("配送费不能为负")

    quotes = tuple(quote_coupon(c, cart_total, delivery_fee) for c in coupons)
    applicable = [q for q in quotes if q.status == "适用" and q.payable is not None]
    unverified = tuple(q for q in quotes if q.status == "待确认")
    best = min(applicable, key=lambda q: (q.payable, q.coupon.name)) if applicable else None

    assumptions = [
        "默认一单只能用一张券；接口未明确支持叠加时不做组合叠加计算。",
        "卡包券不校验门店，最终可用性以 query-store-coupons 与下单页为准。",
    ]
    return CouponDecision(
        cart_total=cart_total,
        delivery_fee=delivery_fee,
        best=best,
        quotes=quotes,
        unverified=unverified,
        assumptions=tuple(assumptions),
    )


def claim_advice(coupons: Sequence[Coupon]) -> Tuple[str, ...]:
    """领券建议：只给「先领再算」的顺序，不诱导为用券而消费。"""

    if not coupons:
        return ("当前没有可领券信息；先查 available-coupons，再决定是否领取。",)
    advice = [
        "麦麦省券通常领取无成本，可以先全部领取，再按本单金额筛可用券。",
        "领完不要为了凑门槛而加购；先看券后实付，再决定要不要买。",
    ]
    free = [c for c in coupons if c.free_delivery]
    if free:
        advice.append(f"其中有 {len(free)} 张免配送券，外送场景可优先比较。")
    unknown = [c for c in coupons if c.threshold is None]
    if unknown:
        advice.append(f"{len(unknown)} 张券缺少门槛字段，使用时需在门店券列表确认。")
    return tuple(advice)
