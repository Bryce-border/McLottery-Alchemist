"""抽奖成本、剩余积分与期望价值。

三条硬规则：

1. **能否抽奖只由 ``drawDecision.resourceEligible`` 决定**，不得根据
   ``availableTimes`` 为 0 或 ``drawTypeText`` 自行推断（工具契约明确要求）。
2. ``expected_value`` 在概率缺失、结果空间不完整或价值不可比时**返回 None**，
   绝不给「看似精确」的数字。
3. 剩余积分是预计值，不代表工具执行后的真实余额。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence, Tuple

from .models import (
    CONSUMPTION_CHANCES,
    CONSUMPTION_NONE,
    CONSUMPTION_POINTS,
    AccountSnapshot,
    LotteryActivity,
)

EV_OK = "OK"
EV_PROBABILITY_MISSING = "PROBABILITY_MISSING"
EV_PROBABILITY_INCOMPLETE = "PROBABILITY_INCOMPLETE"
EV_VALUE_MISSING = "VALUE_MISSING"
EV_INVALID_INPUT = "INVALID_INPUT"


def total_cost(cost_per_draw: Optional[Decimal], count: int) -> Optional[Decimal]:
    if cost_per_draw is None or count <= 0:
        return None
    return cost_per_draw * count


def calculate_remaining_points(
    available: Optional[Decimal],
    cost_per_draw: Optional[Decimal],
    count: int,
) -> Optional[Decimal]:
    """预计剩余积分。成本或余额任一缺失时返回 None（不猜）。"""

    cost = total_cost(cost_per_draw, count)
    if available is None or cost is None:
        return None
    return available - cost


@dataclass(frozen=True)
class ExpectedValue:
    value: Optional[Decimal]
    status: str
    reason: str = ""
    covered_probability: Optional[Decimal] = None

    @property
    def is_conclusive(self) -> bool:
        return self.status == EV_OK and self.value is not None

    def display(self) -> str:
        if self.is_conclusive:
            return f"理论期望价值约 {self.value:.2f}（单位与奖品价值一致）"
        return f"无法给出期望值：{self.reason}"


@dataclass(frozen=True)
class Outcome:
    """一个抽奖结果及其价值与概率。概率必须是接口返回的真实值。"""

    name: str
    value: Optional[Decimal] = None
    probability: Optional[Decimal] = None


def expected_value(
    outcomes: Sequence[Outcome],
    *,
    probability_tolerance: Decimal = Decimal("0.001"),
) -> ExpectedValue:
    """Σ(奖品价值 × 中奖概率)。

    这是**理论期望**，不是单次结果保证，也不构成任何中奖承诺。
    """

    if not outcomes:
        return ExpectedValue(None, EV_INVALID_INPUT, "没有可用的抽奖结果数据")

    if any(o.probability is None for o in outcomes):
        return ExpectedValue(
            None,
            EV_PROBABILITY_MISSING,
            "当前接口未返回中奖概率，无法计算理论期望价值",
        )
    if any(o.value is None for o in outcomes):
        return ExpectedValue(
            None,
            EV_VALUE_MISSING,
            "奖品价值不可比或缺失，无法计算理论期望价值",
        )

    probs = [o.probability for o in outcomes if o.probability is not None]
    if any(p < 0 or p > 1 for p in probs):
        return ExpectedValue(None, EV_INVALID_INPUT, "概率取值不在 0~1 区间")

    covered = sum(probs, Decimal("0"))
    if abs(covered - Decimal("1")) > probability_tolerance:
        return ExpectedValue(
            None,
            EV_PROBABILITY_INCOMPLETE,
            f"概率之和为 {covered}，结果空间不完整，不能按完整分布计算",
            covered,
        )

    total = sum(
        (o.value * o.probability for o in outcomes if o.value is not None and o.probability is not None),
        Decimal("0"),
    )
    return ExpectedValue(total, EV_OK, "", covered)


@dataclass(frozen=True)
class DrawPreflight:
    """抽奖前置校验结果。blockers 非空时禁止调用 draw-lottery。"""

    ok: bool
    blockers: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    cost_per_draw: Optional[Decimal] = None
    consumption_text: str = ""
    fallback_text: str = ""
    resource_eligible: Optional[bool] = None
    activity_name: str = ""
    activity_code: str = ""

    def report_lines(self) -> Tuple[str, ...]:
        lines = []
        if self.activity_name:
            lines.append(f"活动：{self.activity_name}（{self.activity_code or '活动号未返回'}）")
        if self.consumption_text:
            lines.append(f"本次消耗：{self.consumption_text}")
        if self.fallback_text:
            lines.append(f"兜底消耗：{self.fallback_text}")
        lines.extend(f"阻止项：{b}" for b in self.blockers)
        lines.extend(f"提示：{w}" for w in self.warnings)
        return tuple(lines)


def evaluate_activity(
    activity: LotteryActivity,
    account: Optional[AccountSnapshot] = None,
    *,
    now: Optional[datetime] = None,
    draws: int = 1,
) -> DrawPreflight:
    """把「能不能抽」的判定收敛到一个函数里，避免模型自由发挥。"""

    now = now or datetime.now()
    blockers = []
    warnings = []

    if activity.status_code and activity.status_code != "SUCCESS":
        blockers.append(
            f"接口状态为 {activity.status_code}：{activity.status_message or '未返回说明'}"
        )
    elif not activity.status_code:
        blockers.append("未拿到活动接口状态码，无法确认活动有效性")

    if activity.status_text and activity.status_text != "进行中":
        blockers.append(f"活动当前状态为「{activity.status_text}」，不可参与")

    if activity.begin_time and now < activity.begin_time:
        blockers.append(f"活动尚未开始（{activity.begin_time:%Y-%m-%d %H:%M} 起）")
    if activity.end_time and now > activity.end_time:
        blockers.append(f"活动已结束（{activity.end_time:%Y-%m-%d %H:%M} 截止）")

    decision = activity.decision
    if decision.resource_eligible is None:
        blockers.append("接口未返回 drawDecision.resourceEligible，无法确认是否可参与")
    elif decision.resource_eligible is False:
        blockers.append(f"服务端判定资源不足：{decision.reason or '未返回原因'}")

    consumption = decision.next_consumption
    cost = None
    consumption_text = ""
    fallback_text = ""
    if consumption is not None:
        consumption_text = consumption.describe()
        if consumption.type == CONSUMPTION_POINTS:
            cost = consumption.points
        elif consumption.type == CONSUMPTION_CHANCES:
            cost = Decimal("0")
        elif consumption.type == CONSUMPTION_NONE:
            cost = Decimal("0")
        else:
            blockers.append("本次消耗规则未明确，不能授权执行")
        if consumption.type == CONSUMPTION_POINTS and consumption.points is None:
            blockers.append("接口未返回本次消耗积分数")

    if consumption is None:
        blockers.append("接口未返回 nextConsumption，不能确定本次成本")
    elif consumption.type == CONSUMPTION_CHANCES and (consumption.chances is None or consumption.chances <= 0):
        blockers.append("本次消耗次数未明确")
    if draws > 1 and consumption is not None and consumption.type == CONSUMPTION_CHANCES:
        blockers.append("次数消耗可能切换为积分；请逐次刷新并核验授权，不能按首抽成本批量试算")
    if cost is not None and (not cost.is_finite() or cost < 0):
        blockers.append("本次消耗积分数无效")
        cost = None

    if decision.fallback_consumption is not None:
        fallback_text = decision.fallback_consumption.describe()
        warnings.append("次数用完后消耗规则会切换为积分，请按提示说明")

    available = None
    if account is not None:
        available = account.available
    if available is None and activity.available_point is not None:
        available = activity.available_point
        warnings.append("可用积分取自活动接口返回的 availablePoint")
    if available is None:
        warnings.append("未获取到可用积分，无法核对余额")

    if cost is not None and cost > 0 and available is not None:
        needed = cost * draws
        if available < needed:
            blockers.append(
                f"积分不足：需要 {needed}，可用 {available}，差额 {needed - available}"
            )
    elif cost is None and available is not None:
        warnings.append("本次消耗未明确，无法核对余额是否充足")

    if draws <= 0:
        blockers.append("抽奖次数必须为正整数")

    if activity.end_time and (activity.end_time - now).days <= 3 and activity.running:
        warnings.append(
            f"活动将在 {activity.end_time:%Y-%m-%d %H:%M} 结束，注意不要卡点操作"
        )

    if not activity.prizes:
        warnings.append("接口未返回奖品列表，无法预览奖池")

    return DrawPreflight(
        ok=not blockers,
        blockers=tuple(blockers),
        warnings=tuple(dict.fromkeys(warnings)),
        cost_per_draw=cost,
        consumption_text=consumption_text,
        fallback_text=fallback_text,
        resource_eligible=decision.resource_eligible,
        activity_name=activity.name,
        activity_code=activity.activity_code,
    )
