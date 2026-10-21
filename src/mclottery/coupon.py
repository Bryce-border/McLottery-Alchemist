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


# ---------------------------------------------------------------- 真实门店券
# 官方文档 4.2.7：query-store-coupons 只返回 title / couponId / couponCode /
# tradeDateTime / products[]，**没有门槛、面额、折扣率**。因此不能用满减模型算金额。


@dataclass(frozen=True)
class StoreCoupon:
    title: str
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None
    trade_period: Optional[str] = None
    product_codes: Tuple[str, ...] = ()
    product_names: Tuple[str, ...] = ()
    price_hint: Optional[Decimal] = None

    @property
    def product_scope(self) -> str:
        if not self.product_codes and not self.product_names:
            return "未限定商品（以门店与下单页为准）"
        return "、".join(self.product_names or self.product_codes)

    @property
    def start_time(self) -> Optional[str]:
        match = _PERIOD_RE.search(self.trade_period or "")
        return match.group(1) if match else None

    @property
    def end_time(self) -> Optional[str]:
        match = _PERIOD_RE.search(self.trade_period or "")
        return match.group(2) if match else None

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "coupon_id": self.coupon_id,
            "coupon_code": self.coupon_code,
            "trade_period": self.trade_period,
            "product_scope": self.product_scope,
            "price_hint": self.price_hint,
        }


def parse_store_coupons(payload: Any) -> Tuple[StoreCoupon, ...]:
    # 注意：data 本身是数组，models.unwrap 只处理 Mapping 包装，这里单独取。
    data: Any = payload
    if isinstance(data, Mapping):
        inner = data.get("data")
        if isinstance(inner, list):
            data = inner
        else:
            for key in ("list", "items", "coupons", "records", "couponList"):
                value = data.get(key)
                if isinstance(value, list):
                    data = value
                    break
    if not isinstance(data, list):
        return ()

    coupons = []
    for raw in data:
        if not isinstance(raw, Mapping):
            continue
        title = str(raw.get("title") or raw.get("couponName") or "未命名优惠券").strip()
        products = raw.get("products")
        products = products if isinstance(products, list) else []
        codes = tuple(
            str(p.get("productCode")).strip()
            for p in products
            if isinstance(p, Mapping) and p.get("productCode")
        )
        names = tuple(
            str(p.get("productName")).strip()
            for p in products
            if isinstance(p, Mapping) and p.get("productName")
        )
        coupons.append(
            StoreCoupon(
                title=title,
                coupon_id=_text(raw.get("couponId")),
                coupon_code=_text(raw.get("couponCode")),
                trade_period=_text(raw.get("tradeDateTime")),
                product_codes=codes,
                product_names=names,
                price_hint=parse_price_hint(title),
            )
        )
    return tuple(coupons)


_PRICE_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*元")
_PERIOD_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s*[-~至]\s*(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
)


def parse_price_hint(text: Optional[str]) -> Optional[Decimal]:
    """从券标题里提取「9.9元」这类用券价。

    注意：这是**用券后的价格**，不是满减金额。不能拿它当折扣算进订单总额。
    """

    if not text:
        return None
    match = _PRICE_HINT.search(text)
    return Decimal(match.group(1)) if match else None


@dataclass(frozen=True)
class StoreCouponMatch:
    coupon: StoreCoupon
    applicable: bool
    reason: str


def match_store_coupons(
    coupons: Sequence[StoreCoupon], cart_product_codes: Sequence[str]
) -> Tuple[StoreCouponMatch, ...]:
    """判断门店券是否可能适用于当前购物车（只按商品范围匹配，不判断金额）。"""

    cart = {str(code) for code in cart_product_codes if code}
    results = []
    for coupon in coupons:
        if not coupon.product_codes:
            results.append(StoreCouponMatch(coupon, True, "未限定商品，需在下单页确认"))
            continue
        hit = cart & set(coupon.product_codes)
        if not hit:
            results.append(
                StoreCouponMatch(
                    coupon,
                    False,
                    f"仅适用于：{coupon.product_scope}，购物车里没有匹配商品",
                )
            )
        elif len(hit) == len(coupon.product_codes):
            results.append(StoreCouponMatch(coupon, True, "适用商品都在购物车里"))
        else:
            results.append(
                StoreCouponMatch(
                    coupon, True, f"部分适用（{len(hit)}/{len(coupon.product_codes)} 个商品在车内）"
                )
            )
    return tuple(results)


STORE_COUPON_CAVEATS = (
    "门店券接口只返回券名、券码、有效期与适用商品，不返回门槛和面额。",
    "因此不能计算「券后实付」；只能判断这张券可能适用于当前门店与商品。",
    "最终可用性与抵扣金额以下单页 calculate-price 的 couponId 计算结果为准。",
)


# ---------------------------------------------------------------- Markdown 券
# 官方文档 4.4.1 / 4.4.3：麦麦省列表与「我的优惠券」返回 Markdown 文本，不是 JSON。


@dataclass(frozen=True)
class TextCoupon:
    title: str
    status: Optional[str] = None
    price_text: Optional[str] = None
    price: Optional[Decimal] = None
    valid_period: Optional[str] = None
    tags: Tuple[str, ...] = ()
    coupon_id: Optional[str] = None
    coupon_code: Optional[str] = None

    @property
    def claimable(self) -> Optional[bool]:
        if self.status is None:
            return None
        if "不可领取" in self.status:
            return False
        return "未领取" in self.status or "可领取" in self.status

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status,
            "price_text": self.price_text,
            "price": self.price,
            "valid_period": self.valid_period,
            "tags": list(self.tags),
        }


def parse_markdown_coupons(text: str) -> Tuple[TextCoupon, ...]:
    """解析麦麦省/卡包的 Markdown 券列表。

    只提取文本里明确写出的字段；没有的保持 None，不推断门槛与面额。
    """

    if not text or "券" not in text:
        return ()

    coupons: list[TextCoupon] = []
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("- 优惠券标题"):
            if current:
                blocks.append(current)
            current = [stripped]
        elif current:
            current.append(stripped)
    if current:
        blocks.append(current)

    for block in blocks:
        head = block[0]
        title = head.lstrip("#- ").strip()
        if "优惠券标题" in title:
            title = title.split("：", 1)[-1].split(":", 1)[-1].strip(" \\")
        title = title.replace("\\", "").strip()
        if not title or title in {"您的优惠券列表", "麦麦省优惠券列表"}:
            continue

        status = None
        price_text = None
        valid = None
        tags: tuple[str, ...] = ()
        coupon_id = None
        coupon_code = None

        for line in block[1:]:
            plain = line.replace("*", "").replace("\\", "").strip().lstrip("-").strip()
            if plain.startswith("状态"):
                status = plain.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif plain.startswith("优惠"):
                price_text = plain.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif plain.startswith("有效期"):
                valid = plain.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif plain.startswith("标签"):
                raw_tags = plain.split("：", 1)[-1].split(":", 1)[-1]
                tags = tuple(t.strip() for t in raw_tags.split("、") if t.strip())
            elif plain.startswith("couponId"):
                coupon_id = plain.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif plain.startswith("couponCode"):
                coupon_code = plain.split("：", 1)[-1].split(":", 1)[-1].strip()

        coupons.append(
            TextCoupon(
                title=title,
                status=status,
                price_text=price_text,
                price=parse_price_hint(price_text or title),
                valid_period=valid,
                tags=tags,
                coupon_id=coupon_id,
                coupon_code=coupon_code,
            )
        )
    return tuple(coupons)


TEXT_COUPON_CAVEATS = (
    "麦麦省与卡包返回 Markdown 文案，只有券名、状态、用券价或有效期，没有满减门槛。",
    "「9.9 元」这类数字是用券后的价格，不是折扣金额，不能直接抵扣订单总额。",
    "能否用于当前门店/订单，必须用 query-store-coupons 与 calculate-price 验证。",
)
