"""点餐订单的本地校验：金额复核、确认卡、重复下单探测。

`create-order` 有副作用（生成订单与支付链接），而接口没有幂等键。
本模块不替代服务端，只做三件确定性的事：

1. 用本地小计与 `calculate-price` 的返回对账，差异超阈值就拦下来；
2. 生成一张「下单前必须让用户看到」的确认卡；
3. 对同一门店 + 同一取餐方式 + 同一购物车做指纹，短时间重复提交时提醒。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from .models import to_decimal, to_datetime, unwrap

CODE_KEYS = ("mealCode", "code", "productCode", "skuId", "id")
NAME_KEYS = ("mealName", "name", "productName", "title")
QTY_KEYS = ("quantity", "qty", "count", "num")
PRICE_KEYS = ("unitPrice", "price", "salePrice", "amount")

ORDER_STATUS_RULES = (
    ("已完成", "已完成，通常无法取消"),
    ("已取消", "已取消，无需再操作"),
    ("待支付", "未支付，可取消或重新下单"),
    ("已支付", "已支付，取消通常涉及退款，需按订单页提示操作"),
    ("制作中", "制作中，取消可能被门店拒绝"),
    ("配送中", "配送中，一般无法取消"),
)

# 官方文档 4.2.12：取消必须带原因 code
CANCEL_REASONS = {
    1: "改主意了",
    2: "重复下单",
    3: "点错了/点多了/点少了",
    4: "地址/电话填错了",
    5: "送达时间选错了",
    -1: "其它原因",
}

FEN = Decimal("100")


def fen_to_yuan(value: Any) -> Optional[Decimal]:
    """算价接口金额单位是**分**（1600 = 16 元）；None 原样返回。"""

    amount = to_decimal(value)
    if amount is None:
        return None
    return (amount / FEN).quantize(Decimal("0.01"))


def cancel_reason_text(code: Optional[int]) -> str:
    if code is None:
        return "未提供取消原因"
    return CANCEL_REASONS.get(code, f"未识别的原因 code：{code}")


def validate_cancel(order_id: Optional[str], reason_code: Optional[int]) -> Tuple[str, ...]:
    """取消订单的两个必填项，缺一不可。"""

    blockers = []
    if not order_id:
        blockers.append("缺少 orderId，无法取消。")
    if reason_code is None:
        blockers.append(
            "缺少 cancelReasonCode：必须从 1/2/3/4/5/-1 中选一个"
            "（改主意/重复下单/点错/地址电话错/时间错/其它）。"
        )
    elif reason_code not in CANCEL_REASONS:
        blockers.append(f"cancelReasonCode={reason_code} 不在官方取值范围内。")
    return tuple(blockers)


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


@dataclass(frozen=True)
class CartLine:
    meal_code: Optional[str]
    name: str
    quantity: int = 1
    unit_price: Optional[Decimal] = None

    @property
    def subtotal(self) -> Optional[Decimal]:
        if self.unit_price is None:
            return None
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "CartLine":
        qty = to_decimal(_pick(raw, QTY_KEYS))
        quantity = int(qty) if qty is not None and qty > 0 else 1
        code = _pick(raw, CODE_KEYS)
        return cls(
            meal_code=str(code) if code is not None else None,
            name=str(_pick(raw, NAME_KEYS) or "未命名餐品"),
            quantity=quantity,
            unit_price=to_decimal(_pick(raw, PRICE_KEYS)),
        )


def parse_cart(payload: Any) -> Tuple[CartLine, ...]:
    if isinstance(payload, Mapping):
        data = unwrap(payload)
        for key in ("items", "meals", "cart", "products", "list"):
            value = data.get(key)
            if isinstance(value, list):
                return tuple(CartLine.from_dict(x) for x in value if isinstance(x, Mapping))
        return ()
    if isinstance(payload, list):
        return tuple(CartLine.from_dict(x) for x in payload if isinstance(x, Mapping))
    return ()


@dataclass(frozen=True)
class PriceCheck:
    local_subtotal: Optional[Decimal]
    remote_total: Optional[Decimal]
    delivery_fee: Optional[Decimal]
    discount: Optional[Decimal]
    difference: Optional[Decimal]
    consistent: Optional[bool]
    reason: str

    def as_dict(self) -> dict:
        return {
            "local_subtotal": self.local_subtotal,
            "remote_total": self.remote_total,
            "delivery_fee": self.delivery_fee,
            "discount": self.discount,
            "difference": self.difference,
            "consistent": self.consistent,
            "reason": self.reason,
        }


def verify_price(
    lines: Sequence[CartLine],
    remote_total: Optional[Decimal],
    *,
    delivery_fee: Decimal = Decimal("0"),
    discount: Decimal = Decimal("0"),
    tolerance: Decimal = Decimal("0.01"),
    unit: str = "yuan",
) -> PriceCheck:
    """本地小计 + 配送费 − 优惠 是否等于接口返回的应付总额。

    ``unit="fen"`` 时把接口金额从分换算成元再比。**算价接口返回分，下单/订单详情返回元**，
    两个接口单位不一致，混用会差 100 倍。
    """

    if unit == "fen":
        remote_total = fen_to_yuan(remote_total)
        delivery_fee = fen_to_yuan(delivery_fee) or Decimal("0")
        discount = fen_to_yuan(discount) or Decimal("0")

    if any(line.subtotal is None for line in lines):
        missing = [line.name for line in lines if line.subtotal is None]
        return PriceCheck(
            local_subtotal=None,
            remote_total=remote_total,
            delivery_fee=delivery_fee,
            discount=discount,
            difference=None,
            consistent=None,
            reason=f"这些餐品缺少单价，无法复核：{'、'.join(missing)}",
        )
    subtotal = sum((line.subtotal for line in lines), Decimal("0"))
    if remote_total is None:
        return PriceCheck(
            local_subtotal=subtotal,
            remote_total=None,
            delivery_fee=delivery_fee,
            discount=discount,
            difference=None,
            consistent=None,
            reason="接口未返回应付总额，不能确认价格；下单前必须重新调用 calculate-price",
        )
    expected = subtotal + delivery_fee - discount
    difference = (remote_total - expected).quantize(Decimal("0.01"))
    consistent = abs(difference) <= tolerance
    reason = (
        "本地复核与接口总额一致"
        if consistent
        else f"本地复核与接口总额相差 {difference} 元，必须先查清差异再下单"
    )
    return PriceCheck(
        local_subtotal=subtotal,
        remote_total=remote_total,
        delivery_fee=delivery_fee,
        discount=discount,
        difference=difference,
        consistent=consistent,
        reason=reason,
    )


@dataclass(frozen=True)
class OrderConfirmation:
    store_name: str
    store_id: Optional[str]
    meal_type: str
    lines: Tuple[CartLine, ...]
    subtotal: Optional[Decimal]
    discount: Optional[Decimal]
    delivery_fee: Optional[Decimal]
    payable: Optional[Decimal]
    coupon_name: Optional[str]
    price_check: PriceCheck
    blockers: Tuple[str, ...] = ()
    reminders: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        return {
            "store_name": self.store_name,
            "store_id": self.store_id,
            "meal_type": self.meal_type,
            "lines": [
                {
                    "name": l.name,
                    "meal_code": l.meal_code,
                    "quantity": l.quantity,
                    "unit_price": l.unit_price,
                    "subtotal": l.subtotal,
                }
                for l in self.lines
            ],
            "subtotal": self.subtotal,
            "discount": self.discount,
            "delivery_fee": self.delivery_fee,
            "payable": self.payable,
            "coupon_name": self.coupon_name,
            "price_check": self.price_check.as_dict(),
            "blockers": list(self.blockers),
            "reminders": list(self.reminders),
            "ready": self.ready,
        }


def build_confirmation(
    *,
    store_name: str,
    meal_type: str,
    lines: Sequence[CartLine],
    price_check: PriceCheck,
    store_id: Optional[str] = None,
    discount: Optional[Decimal] = None,
    delivery_fee: Optional[Decimal] = None,
    coupon_name: Optional[str] = None,
) -> OrderConfirmation:
    blockers = []
    if not lines:
        blockers.append("购物车为空")
    if price_check.consistent is None:
        blockers.append(price_check.reason)
    elif price_check.consistent is False:
        blockers.append(price_check.reason)

    reminders = [
        "下单属于有副作用操作，必须由用户明确确认门店、取餐方式、商品与实付金额。",
        "价格与库存以下单页返回为准；本卡只是复核结果。",
    ]
    if meal_type not in ("到店自取", "外送"):
        reminders.append(f"取餐方式「{meal_type}」不在常见值内，请确认是否正确。")

    return OrderConfirmation(
        store_name=store_name,
        store_id=store_id,
        meal_type=meal_type,
        lines=tuple(lines),
        subtotal=price_check.local_subtotal,
        discount=discount,
        delivery_fee=delivery_fee,
        payable=price_check.remote_total,
        coupon_name=coupon_name,
        price_check=price_check,
        blockers=tuple(blockers),
        reminders=tuple(reminders),
    )


def cart_fingerprint(store_id: Optional[str], meal_type: str, lines: Sequence[CartLine]) -> str:
    payload = {
        "store": store_id or "",
        "meal_type": meal_type,
        "items": sorted(
            [
                {
                    "code": l.meal_code or l.name,
                    "qty": l.quantity,
                }
                for l in lines
            ],
            key=lambda x: (x["code"], x["qty"]),
        ),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_order_ledger(path: str) -> Tuple[Mapping[str, Any], ...]:
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        return ()
    records = []
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, Mapping):
            records.append(item)
    return tuple(records)


def duplicate_within(
    records: Iterable[Mapping[str, Any]],
    fingerprint: str,
    now: datetime,
    *,
    window_minutes: int = 10,
) -> Optional[Mapping[str, Any]]:
    """同一购物车在窗口内重复提交时返回上一条记录，供 Agent 提醒用户。"""

    cutoff = now - timedelta(minutes=window_minutes)
    for record in reversed(list(records)):
        if record.get("fingerprint") != fingerprint:
            continue
        created = to_datetime(record.get("created_at"))
        if created is not None and created >= cutoff:
            return record
    return None


@dataclass(frozen=True)
class PriceQuote:
    """calculate-price 的可用视图（金额已由分换算为元）。"""

    product_original_price: Optional[Decimal]
    product_price: Optional[Decimal]
    delivery_original_price: Optional[Decimal]
    delivery_price: Optional[Decimal]
    original_price: Optional[Decimal]
    discount: Optional[Decimal]
    payable: Optional[Decimal]
    tableware_price: Optional[Decimal]
    take_way_list: Tuple[Mapping[str, Any], ...] = ()
    meal_assistance_list: Tuple[Mapping[str, Any], ...] = ()
    product_list: Tuple[Mapping[str, Any], ...] = ()
    missing_fields: Tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "product_price": self.product_price,
            "delivery_price": self.delivery_price,
            "original_price": self.original_price,
            "discount": self.discount,
            "payable": self.payable,
            "tableware_price": self.tableware_price,
            "take_way_list": list(self.take_way_list),
            "product_list": list(self.product_list),
            "missing_fields": list(self.missing_fields),
        }


def parse_price_result(payload: Any) -> PriceQuote:
    """解析 4.2.10 算价返回。**金额单位是分**，这里统一换成元。"""

    data = unwrap(payload)
    missing = []

    def money(key: str) -> Optional[Decimal]:
        value = fen_to_yuan(data.get(key))
        if value is None:
            missing.append(key)
        return value

    def raw_list(key: str) -> Tuple[Mapping[str, Any], ...]:
        value = data.get(key)
        if isinstance(value, list):
            return tuple(x for x in value if isinstance(x, Mapping))
        return ()

    return PriceQuote(
        product_original_price=money("productOriginalPrice"),
        product_price=money("productPrice"),
        delivery_original_price=money("deliveryOriginalPrice"),
        delivery_price=money("deliveryPrice"),
        original_price=money("originalPrice"),
        discount=money("discount"),
        payable=money("price"),
        tableware_price=money("tablewarePrice"),
        take_way_list=raw_list("takeWayList"),
        meal_assistance_list=raw_list("mealAssistanceList"),
        product_list=raw_list("productList"),
        missing_fields=tuple(missing),
    )


@dataclass(frozen=True)
class OrderCreated:
    """create-order 的可用视图。注意：**这里的金额是元，不是分**。"""

    order_id: Optional[str]
    pay_id: Optional[str]
    pay_h5_url: Optional[str]
    status_text: Optional[str]
    store_name: Optional[str]
    store_address: Optional[str]
    total_amount: Optional[Decimal]
    real_total_amount: Optional[Decimal]
    total_discount_amount: Optional[Decimal]
    delivery_price: Optional[Decimal]
    product_price: Optional[Decimal]
    take_way: Optional[str]
    pickup_code: Optional[str]
    locker_code: Optional[str]
    locker_qr_code: Optional[str]
    delivery_address: Optional[str]
    product_names: Tuple[str, ...] = ()
    missing_fields: Tuple[str, ...] = ()

    @property
    def has_payment_link(self) -> bool:
        return bool(self.pay_h5_url)

    def as_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "pay_id": self.pay_id,
            "pay_h5_url": self.pay_h5_url,
            "status_text": self.status_text,
            "store_name": self.store_name,
            "total_amount": self.total_amount,
            "real_total_amount": self.real_total_amount,
            "total_discount_amount": self.total_discount_amount,
            "delivery_price": self.delivery_price,
            "product_price": self.product_price,
            "take_way": self.take_way,
            "pickup_code": self.pickup_code,
            "locker_code": self.locker_code,
            "locker_qr_code": self.locker_qr_code,
            "delivery_address": self.delivery_address,
            "product_names": list(self.product_names),
            "missing_fields": list(self.missing_fields),
        }


def parse_order_created(payload: Any) -> OrderCreated:
    data = unwrap(payload)
    detail = data.get("orderDetail")
    detail = detail if isinstance(detail, Mapping) else {}

    def pick(*keys: str) -> Any:
        for key in keys:
            if key in detail and detail[key] not in (None, ""):
                return detail[key]
            if key in data and data[key] not in (None, ""):
                return data[key]
        return None

    def text(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        return str(value).strip()

    delivery = detail.get("deliveryInfo")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    products = detail.get("orderProductList")
    products = products if isinstance(products, list) else []

    order_id = text(pick("orderId"))
    pay_h5_url = text(pick("payH5Url"))
    status_text = text(pick("orderStatus"))

    missing = []
    if order_id is None:
        missing.append("orderId")
    if status_text is None:
        missing.append("orderStatus")
    if pay_h5_url is None:
        missing.append("payH5Url")

    address_parts = [
        text(delivery.get("deliveryAddress")),
        text(delivery.get("addressDetail")),
    ]
    address = " ".join(p for p in address_parts if p) or None

    return OrderCreated(
        order_id=order_id,
        pay_id=text(pick("payId")),
        pay_h5_url=pay_h5_url,
        status_text=status_text,
        store_name=text(pick("storeName")),
        store_address=text(pick("storeAddress")),
        total_amount=to_decimal(pick("totalAmount")),
        real_total_amount=to_decimal(pick("realTotalAmount")),
        total_discount_amount=to_decimal(pick("totalDiscountAmount")),
        delivery_price=to_decimal(pick("deliveryPrice")),
        product_price=to_decimal(pick("productPrice")),
        take_way=text(pick("takeWay")),
        pickup_code=text(pick("pickupCode")),
        locker_code=text(pick("lockerCode")),
        locker_qr_code=text(pick("lockerQrCode")),
        delivery_address=address,
        product_names=tuple(
            text(p.get("productName")) or "未命名餐品"
            for p in products
            if isinstance(p, Mapping)
        ),
        missing_fields=tuple(missing),
    )


def explain_order_status(status_text: Optional[str]) -> str:
    if not status_text:
        return "接口未返回订单状态，不能假设订单已成功或已取消；不做推断。"
    for key, explanation in ORDER_STATUS_RULES:
        if key in status_text:
            return explanation
    return "未识别的订单状态，按接口原文展示，不做推断。"
