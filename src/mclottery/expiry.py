"""积分到期分类。

诚实性约束（对齐 SKILL.md §3.1 与开发指南 §4 阶段三）：

* 只有接口返回「到期批次 + 数量 + 日期」时才逐项报告；
* ``query-my-account`` 实测只返回**月度汇总**（本月/下月将过期积分），
  没有具体到期日期，因此本模块输出的永远带有精度说明；
* 阈值（7/30 天）是产品侧提醒窗口，**不是**麦当劳官方积分政策。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from .models import AccountSnapshot


class Urgency(str, Enum):
    EXPIRED = "EXPIRED"
    THIS_MONTH = "THIS_MONTH"
    NEXT_MONTH = "NEXT_MONTH"
    LATER = "LATER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ExpiryPolicy:
    """提醒窗口配置。仅为产品侧阈值，可被 CLI/调用方覆盖。"""

    near_days: int = 7
    mid_days: int = 30
    disclaimer: str = (
        "提醒窗口为产品侧阈值，不是麦当劳官方积分有效期政策；"
        "实际有效期以麦当劳官方渠道为准。"
    )


@dataclass(frozen=True)
class ExpiryBatch:
    label: str
    amount: Decimal
    urgency: Urgency
    expire_date: Optional[date] = None
    precision: str = "month"  # month / day / unknown
    source_field: str = ""


@dataclass(frozen=True)
class ExpiryReport:
    batches: Tuple[ExpiryBatch, ...] = ()
    expired_total: Decimal = Decimal("0")
    this_month_total: Decimal = Decimal("0")
    next_month_total: Decimal = Decimal("0")
    near_term_total: Decimal = Decimal("0")
    has_batch_detail: bool = False
    data_gaps: Tuple[str, ...] = ()
    policy: ExpiryPolicy = field(default_factory=ExpiryPolicy)

    @property
    def total_at_risk(self) -> Decimal:
        """本月 + 下月合计，用于生成「止损」方案。"""

        return self.this_month_total + self.next_month_total

    def has_risk(self) -> bool:
        return self.total_at_risk > 0

    def summary_lines(self) -> Tuple[str, ...]:
        lines = []
        if self.expired_total > 0:
            lines.append(f"已过期积分：{self.expired_total}（已不可用）")
        if self.this_month_total > 0:
            lines.append(f"本月将过期：{self.this_month_total}（接口仅返回月度汇总）")
        if self.next_month_total > 0:
            lines.append(f"下月将过期：{self.next_month_total}（接口仅返回月度汇总）")
        if not any(
            (self.expired_total, self.this_month_total, self.next_month_total)
        ):
            lines.append("接口返回的本月/下月将过期积分均为 0")
        if self.data_gaps:
            lines.append("数据缺口：" + "；".join(self.data_gaps))
        lines.append(self.policy.disclaimer)
        return tuple(lines)


def _month_end(day: date) -> date:
    return day.replace(day=calendar.monthrange(day.year, day.month)[1])


def _next_month(day: date) -> date:
    return (day.replace(day=1) + timedelta(days=32)).replace(day=1)


def classify_expiring_points(
    account: Any,
    today: Optional[date] = None,
    policy: Optional[ExpiryPolicy] = None,
) -> ExpiryReport:
    """按账户返回的到期汇总做分类。

    ``account`` 可以是 :class:`AccountSnapshot`，也可以是 ``query-my-account``
    的原始 JSON（会自动归一化）。
    """

    if isinstance(account, AccountSnapshot):
        snapshot = account
    elif isinstance(account, Mapping):
        snapshot = AccountSnapshot.from_mcp(account)
    else:
        snapshot = AccountSnapshot()

    today = today or date.today()
    policy = policy or ExpiryPolicy()

    batches = []
    gaps = list(snapshot.missing_fields)

    this_month_end = _month_end(today)
    next_month_end = _month_end(_next_month(today))

    if snapshot.expiring_this_month is not None:
        amount = snapshot.expiring_this_month
        urgency = (
            Urgency.THIS_MONTH
            if amount > 0
            else Urgency.LATER
        )
        batches.append(
            ExpiryBatch(
                label=f"本月（{today.year}-{today.month:02d}）将过期",
                amount=amount,
                urgency=urgency,
                expire_date=this_month_end,
                precision="month",
                source_field="currentMouthExpirePoint",
            )
        )

    if snapshot.expiring_next_month is not None:
        amount = snapshot.expiring_next_month
        nxt = _next_month(today)
        urgency = (
            Urgency.NEXT_MONTH
            if amount > 0
            else Urgency.LATER
        )
        batches.append(
            ExpiryBatch(
                label=f"下月（{nxt.year}-{nxt.month:02d}）将过期",
                amount=amount,
                urgency=urgency,
                expire_date=next_month_end,
                precision="month",
                source_field="nextMouthExpirePoint",
            )
        )

    near_term = Decimal("0")
    for batch in batches:
        if batch.amount <= 0 or batch.expire_date is None:
            continue
        days_left = (batch.expire_date - today).days
        if days_left <= policy.near_days:
            near_term += batch.amount
        elif days_left <= policy.mid_days:
            near_term += batch.amount

    if snapshot.expiring_this_month is None and snapshot.expiring_next_month is None:
        gaps.append("接口未返回任何到期汇总，无法判断到期风险")

    return ExpiryReport(
        batches=tuple(batches),
        expired_total=snapshot.expired or Decimal("0"),
        this_month_total=snapshot.expiring_this_month or Decimal("0"),
        next_month_total=snapshot.expiring_next_month or Decimal("0"),
        near_term_total=near_term,
        has_batch_detail=snapshot.has_batch_detail,
        data_gaps=tuple(dict.fromkeys(gaps)),
        policy=policy,
    )
