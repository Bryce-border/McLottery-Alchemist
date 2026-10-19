"""MCP 返回数据 → 领域模型。

设计约束（对齐 SKILL.md「事实由工具决定」）：

1. 字段名全部来自麦当劳 MCP 的真实返回，不做猜测性补全。
2. 任何缺失字段一律保留为 ``None``，并在 ``missing_fields`` 中登记，
   供上层如实告知用户「接口未返回该数据」。
3. 账户 ID 等标识不进入模型，避免日志与输出泄露账户信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

STATUS_TEXT = {
    1: "仓库中",
    2: "上架",
    3: "售罄",
    4: "下架",
    5: "预热",
}

CONSUMPTION_NONE = "NONE"
CONSUMPTION_POINTS = "POINTS"
CONSUMPTION_CHANCES = "CHANCES"
CONSUMPTION_UNKNOWN = "UNKNOWN"


def to_decimal(value: Any) -> Optional[Decimal]:
    """把接口返回的字符串数字转成 Decimal；空值/非法值返回 None。"""

    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def to_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def unwrap(payload: Any) -> Mapping[str, Any]:
    """兼容 MCP 工具两种包装：直接返回 data 或包在 {"data": ...} 中。"""

    if not isinstance(payload, Mapping):
        raise TypeError("MCP 返回不是 JSON 对象")
    inner = payload.get("data")
    if isinstance(inner, Mapping):
        return inner
    return payload


@dataclass(frozen=True)
class AccountSnapshot:
    """query-my-account 的可用视图。

    真实字段（2026-10 实测）：availablePoint / accumulativePoint / usedPoint /
    expiredPoint / frozenPoint / currentMouthExpirePoint / nextMouthExpirePoint /
    lastMouthExpirePoint / currency。接口拼写为 ``Mouth``，此处同时兼容 ``Month``。
    """

    available: Optional[Decimal] = None
    accumulated: Optional[Decimal] = None
    used: Optional[Decimal] = None
    expired: Optional[Decimal] = None
    frozen: Optional[Decimal] = None
    expiring_this_month: Optional[Decimal] = None
    expiring_next_month: Optional[Decimal] = None
    currency: str = ""
    has_account_id: bool = False
    missing_fields: Tuple[str, ...] = ()

    @property
    def has_batch_detail(self) -> bool:
        """接口是否返回了「分批到期」明细（数量 + 具体日期）。"""

        return False

    @classmethod
    def from_mcp(cls, payload: Any) -> "AccountSnapshot":
        data = unwrap(payload)
        missing = []

        def pick(*keys: str) -> Any:
            for key in keys:
                if key in data and data[key] not in (None, ""):
                    return data[key]
            return None

        available = to_decimal(pick("availablePoint"))
        if available is None:
            missing.append("availablePoint")
        accumulated = to_decimal(pick("accumulativePoint"))
        used = to_decimal(pick("usedPoint"))
        expired = to_decimal(pick("expiredPoint"))
        frozen = to_decimal(pick("frozenPoint"))
        this_month = to_decimal(pick("currentMouthExpirePoint", "currentMonthExpirePoint"))
        next_month = to_decimal(pick("nextMouthExpirePoint", "nextMonthExpirePoint"))
        if this_month is None and next_month is None:
            missing.append("currentMouthExpirePoint/nextMouthExpirePoint")

        return cls(
            available=available,
            accumulated=accumulated,
            used=used,
            expired=expired,
            frozen=frozen,
            expiring_this_month=this_month,
            expiring_next_month=next_month,
            currency=str(pick("currency") or ""),
            has_account_id=bool(pick("accountId")),
            missing_fields=tuple(missing),
        )


@dataclass(frozen=True)
class Consumption:
    """drawDecision.nextConsumption / fallbackConsumption。"""

    type: str = CONSUMPTION_UNKNOWN
    points: Optional[Decimal] = None
    chances: Optional[int] = None
    text: str = ""

    @property
    def is_free(self) -> bool:
        return self.type == CONSUMPTION_NONE

    @classmethod
    def from_mcp(cls, payload: Any) -> Optional["Consumption"]:
        if not isinstance(payload, Mapping):
            return None
        raw_type = str(payload.get("type") or "").strip().upper()
        if raw_type not in {CONSUMPTION_NONE, CONSUMPTION_POINTS, CONSUMPTION_CHANCES}:
            raw_type = CONSUMPTION_UNKNOWN
        return cls(
            type=raw_type,
            points=to_decimal(payload.get("points")),
            chances=to_int(payload.get("chances")),
            text=str(payload.get("text") or ""),
        )

    def describe(self) -> str:
        if self.text:
            return self.text
        if self.type == CONSUMPTION_POINTS and self.points is not None:
            return f"本次将消耗 {self.points} 积分"
        if self.type == CONSUMPTION_CHANCES and self.chances is not None:
            return f"本次将消耗 {self.chances} 次抽奖机会"
        if self.type == CONSUMPTION_NONE:
            return "本次不消耗积分或次数"
        return "本次消耗规则未明确"


@dataclass(frozen=True)
class DrawDecision:
    """服务端资源决策，是能否抽奖的唯一依据。"""

    resource_eligible: Optional[bool] = None
    reason: Optional[str] = None
    next_consumption: Optional[Consumption] = None
    fallback_consumption: Optional[Consumption] = None

    @classmethod
    def from_mcp(cls, payload: Any) -> "DrawDecision":
        if not isinstance(payload, Mapping):
            return cls()
        eligible = payload.get("resourceEligible")
        return cls(
            resource_eligible=eligible if isinstance(eligible, bool) else None,
            reason=payload.get("reason") or None,
            next_consumption=Consumption.from_mcp(payload.get("nextConsumption")),
            fallback_consumption=Consumption.from_mcp(payload.get("fallbackConsumption")),
        )


@dataclass(frozen=True)
class LotteryPrize:
    name: str
    type_text: str = ""
    image_url: str = ""

    @classmethod
    def from_mcp(cls, payload: Mapping[str, Any]) -> "LotteryPrize":
        return cls(
            name=str(payload.get("name") or ""),
            type_text=str(payload.get("typeText") or ""),
            image_url=str(payload.get("imageUrl") or ""),
        )


@dataclass(frozen=True)
class LotteryActivity:
    """query-lottery-info 的可用视图。

    注意：真实接口**不返回中奖概率**，``prizes`` 只有名称/类型/图片。
    """

    status_code: str = ""
    status_message: str = ""
    activity_code: str = ""
    name: str = ""
    status_text: str = ""
    begin_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    draw_point: Optional[Decimal] = None
    draw_type_text: str = ""
    available_times: Optional[int] = None
    available_point: Optional[Decimal] = None
    decision: DrawDecision = field(default_factory=DrawDecision)
    prizes: Tuple[LotteryPrize, ...] = ()
    missing_fields: Tuple[str, ...] = ()

    @property
    def running(self) -> bool:
        return self.status_text == "进行中" and self.status_code == "SUCCESS"

    @property
    def has_probabilities(self) -> bool:
        """接口从不返回概率，保留该判断以便未来契约升级。"""

        return False

    @classmethod
    def from_mcp(cls, payload: Any) -> "LotteryActivity":
        data = unwrap(payload)
        status = data.get("status")
        status = status if isinstance(status, Mapping) else {}
        missing = []

        code = str(status.get("code") or "")
        message = str(status.get("message") or "")
        if not code:
            missing.append("status.code")

        draw_point = to_decimal(data.get("drawPoint"))
        if draw_point is None:
            missing.append("drawPoint")

        prizes_raw = data.get("prizes")
        prizes: Tuple[LotteryPrize, ...] = ()
        if isinstance(prizes_raw, Sequence) and not isinstance(prizes_raw, (str, bytes)):
            prizes = tuple(
                LotteryPrize.from_mcp(item)
                for item in prizes_raw
                if isinstance(item, Mapping)
            )
        else:
            missing.append("prizes")

        decision_raw = data.get("drawDecision")
        if not isinstance(decision_raw, Mapping):
            missing.append("drawDecision")

        return cls(
            status_code=code,
            status_message=message,
            activity_code=str(data.get("activityCode") or ""),
            name=str(data.get("activityName") or ""),
            status_text=str(data.get("activityStatusText") or ""),
            begin_time=to_datetime(data.get("beginTime")),
            end_time=to_datetime(data.get("endTime")),
            draw_point=draw_point,
            draw_type_text=str(data.get("drawTypeText") or ""),
            available_times=to_int(data.get("availableTimes")),
            available_point=to_decimal(data.get("availablePoint")),
            decision=DrawDecision.from_mcp(decision_raw),
            prizes=prizes,
            missing_fields=tuple(missing),
        )


@dataclass(frozen=True)
class Product:
    """mall-points-products 的单条商品。

    真实坑位：``point="0"`` 多为付费活动（如生日派对），不是「0 积分白拿」；
    ``status=2`` 但 ``downTime`` 已过期的商品仍在列表中。两种情况都不能当作可兑换。
    """

    spu_id: int
    name: str
    point: Optional[Decimal]
    price: Optional[Decimal] = None
    selling: str = ""
    up_time: Optional[datetime] = None
    down_time: Optional[datetime] = None
    cat_name: str = ""
    status: Optional[int] = None
    shop_id: Optional[int] = None

    @property
    def status_text(self) -> str:
        if self.status is None:
            return "状态未知"
        return STATUS_TEXT.get(self.status, f"未知状态({self.status})")

    def availability(self, now: datetime) -> Tuple[bool, str]:
        """返回 (是否积分可兑, 原因)。原因用于向用户解释为什么被排除。"""

        if self.status is None:
            return False, "商品状态未知，无法确认可兑换"
        if self.status == 3:
            return False, "已售罄"
        if self.status == 4:
            return False, "已下架"
        if self.status == 1:
            return False, "仍在仓库中，未上架"
        if self.status == 5:
            return False, "预热中，尚未开放兑换"
        if self.point is None:
            return False, "接口未返回所需积分"
        if self.point <= 0:
            return False, f"非积分兑换商品（需付费 {self.price if self.price is not None else '金额未返回'} 元）"
        if self.up_time and now < self.up_time:
            return False, f"兑换未开始（{self.up_time:%Y-%m-%d} 起）"
        if self.down_time and now > self.down_time:
            return False, f"兑换已截止（{self.down_time:%Y-%m-%d %H:%M} 结束）"
        return True, ""

    def affordable(self, available: Optional[Decimal]) -> Optional[bool]:
        if self.point is None or available is None:
            return None
        return available >= self.point

    @classmethod
    def from_mcp(cls, payload: Mapping[str, Any]) -> Optional["Product"]:
        spu_id = to_int(payload.get("spuId"))
        if spu_id is None:
            return None
        return cls(
            spu_id=spu_id,
            name=str(payload.get("spuName") or ""),
            point=to_decimal(payload.get("point")),
            price=to_decimal(payload.get("price")),
            selling=str(payload.get("selling") or ""),
            up_time=to_datetime(payload.get("upTime")),
            down_time=to_datetime(payload.get("downTime")),
            cat_name=str(payload.get("catName") or ""),
            status=to_int(payload.get("status")),
            shop_id=to_int(payload.get("shopId")),
        )


def parse_products(payload: Any) -> Tuple[Product, ...]:
    """解析商城列表；兼容 {"data": [...]} 与裸数组。"""

    items: Iterable[Any]
    if isinstance(payload, Mapping):
        raw = payload.get("data")
        items = raw if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        items = payload
    else:
        items = []

    products = []
    for item in items:
        if isinstance(item, Mapping):
            product = Product.from_mcp(item)
            if product is not None:
                products.append(product)
    return tuple(products)


@dataclass(frozen=True)
class MyPrize:
    """query-my-prizes 的单条中奖记录。"""

    prize_id: str
    name: str
    status_text: str
    record_time: Optional[datetime] = None
    time_remind_text: str = ""
    type_text: str = ""

    @classmethod
    def from_mcp(cls, payload: Mapping[str, Any]) -> "MyPrize":
        return cls(
            prize_id=str(payload.get("id") or ""),
            name=str(payload.get("name") or ""),
            status_text=str(payload.get("statusText") or ""),
            record_time=to_datetime(payload.get("recordTime")),
            time_remind_text=str(payload.get("timeRemindText") or ""),
            type_text=str(payload.get("prizeTypeText") or ""),
        )


@dataclass(frozen=True)
class PrizePage:
    prizes: Tuple[MyPrize, ...] = ()
    has_more: bool = False
    page_num: int = 1
    total_returned: int = 0
    status_code: str = ""
    truncated: bool = False

    @classmethod
    def from_mcp(cls, payload: Any) -> "PrizePage":
        data = unwrap(payload)
        status = data.get("status")
        status = status if isinstance(status, Mapping) else {}
        raw = data.get("prizes")
        prizes: Tuple[MyPrize, ...] = ()
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            prizes = tuple(
                MyPrize.from_mcp(item) for item in raw if isinstance(item, Mapping)
            )
        return cls(
            prizes=prizes,
            has_more=bool(data.get("hasMore")),
            page_num=to_int(data.get("pageNum")) or 1,
            total_returned=len(prizes),
            status_code=str(status.get("code") or ""),
            truncated=bool(data.get("truncated")),
        )


@dataclass(frozen=True)
class DrawResult:
    """draw-lottery 的返回值。status.code 非 SUCCESS 时 message 需原样展示。"""

    status_code: str = ""
    message: str = ""
    win: Optional[bool] = None
    prizes: Tuple[LotteryPrize, ...] = ()
    raw_ok: bool = True

    @property
    def success(self) -> bool:
        return self.status_code == "SUCCESS"

    @property
    def state_unknown(self) -> bool:
        """既未拿到明确状态码（超时/断连）也未拿到可判定的失败信息。"""

        return (not self.raw_ok) or self.status_code == ""

    @classmethod
    def from_mcp(cls, payload: Any) -> "DrawResult":
        if not isinstance(payload, Mapping):
            return cls(raw_ok=False)
        data = unwrap(payload)
        status = data.get("status")
        status = status if isinstance(status, Mapping) else {}
        raw_prizes = data.get("prizes")
        prizes: Tuple[LotteryPrize, ...] = ()
        if isinstance(raw_prizes, Sequence) and not isinstance(raw_prizes, (str, bytes)):
            prizes = tuple(
                LotteryPrize.from_mcp(item)
                for item in raw_prizes
                if isinstance(item, Mapping)
            )
        win = data.get("win")
        return cls(
            status_code=str(status.get("code") or ""),
            message=str(status.get("message") or ""),
            win=win if isinstance(win, bool) else None,
            prizes=prizes,
        )
