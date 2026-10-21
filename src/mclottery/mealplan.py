"""积分缺口 → 饮食计划（可核对的账，不是消费诱导）。

核心原则：

1. **积分获取速度必须来自外部规则或用户输入**。接口不返回「消费 1 元得多少积分」，
   所以没有 ``points_per_yuan`` / ``points_per_visit`` 时，本模块只算「还差多少积分」
   与「需要多少次消费」，不编造到账速度。
2. 计划同时给出**预计总支出**，不允许只展示「能拿多少积分」而隐藏花出去的钱。
3. 达成时间是估算值，前提是积分按规则实时到账、且目标商品在截止前仍有库存。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional, Sequence, Tuple

ZERO = Decimal("0")


def _ceil_div(a: Decimal, b: Decimal) -> Optional[int]:
    if b <= 0:
        return None
    return int((a / b).to_integral_value(rounding="ROUND_CEILING"))


@dataclass(frozen=True)
class GoalRequest:
    """用户想换/想抽的东西，以及他的消费习惯。"""

    target_name: str
    target_points: Decimal
    current_points: Decimal
    today: date
    weekly_visits: int
    budget_min: Decimal
    budget_max: Decimal
    deadline: Optional[date] = None
    points_per_visit: Optional[Decimal] = None
    points_per_yuan: Optional[Decimal] = None
    draw_cost: Optional[Decimal] = None
    expiring_points: Optional[Decimal] = None

    @property
    def gap(self) -> Decimal:
        return max(ZERO, self.target_points - self.current_points)

    @property
    def avg_budget(self) -> Decimal:
        return ((self.budget_min + self.budget_max) / Decimal("2")).quantize(
            Decimal("0.01")
        )

    @property
    def weeks_left(self) -> Optional[Decimal]:
        if self.deadline is None:
            return None
        days = (self.deadline - self.today).days
        if days < 0:
            return ZERO
        return (Decimal(days) / Decimal("7")).quantize(Decimal("0.01"))

    @property
    def earn_per_visit(self) -> Optional[Decimal]:
        if self.points_per_visit is not None and self.points_per_visit > 0:
            return self.points_per_visit
        if self.points_per_yuan is not None and self.points_per_yuan > 0:
            return (self.avg_budget * self.points_per_yuan).quantize(Decimal("0.01"))
        return None


@dataclass(frozen=True)
class WeekSlot:
    week: int
    label: str
    visits: int
    budget: Decimal
    earned_points: Optional[Decimal]
    cumulative_points: Decimal
    reached: bool


@dataclass(frozen=True)
class GoalPlan:
    target_name: str
    target_points: Decimal
    current_points: Decimal
    gap: Decimal
    deadline: Optional[date]
    weeks_left: Optional[Decimal]
    earn_per_visit: Optional[Decimal]
    visits_needed: Optional[int]
    weeks_needed: Optional[Decimal]
    feasible: Optional[bool]
    projected_points: Optional[Decimal]
    projected_spend: Optional[Decimal]
    weekly_plan: Tuple[WeekSlot, ...] = ()
    alternatives: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "target_name": self.target_name,
            "target_points": self.target_points,
            "current_points": self.current_points,
            "gap": self.gap,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "weeks_left": self.weeks_left,
            "earn_per_visit": self.earn_per_visit,
            "visits_needed": self.visits_needed,
            "weeks_needed": self.weeks_needed,
            "feasible": self.feasible,
            "projected_points": self.projected_points,
            "projected_spend": self.projected_spend,
            "weekly_plan": [
                {
                    "week": w.week,
                    "label": w.label,
                    "visits": w.visits,
                    "budget": w.budget,
                    "earned_points": w.earned_points,
                    "cumulative_points": w.cumulative_points,
                    "reached": w.reached,
                }
                for w in self.weekly_plan
            ],
            "alternatives": list(self.alternatives),
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
        }


def build_goal_plan(request: GoalRequest) -> GoalPlan:
    if request.target_points <= 0:
        raise ValueError("目标积分必须大于 0")
    if request.weekly_visits < 0:
        raise ValueError("每周次数不能为负")
    if request.budget_min < 0 or request.budget_max < 0:
        raise ValueError("预算不能为负")
    if request.budget_max < request.budget_min:
        raise ValueError("预算上限不能低于下限")

    gap = request.gap
    earn = request.earn_per_visit
    weeks_left = request.weeks_left
    warnings = []
    assumptions = [
        "积分获取速度需由官方活动规则或用户提供；本计划不使用行业默认值。",
        "达成时间为估算值，前提是积分按规则到账，且目标商品在截止前仍有库存。",
    ]

    visits_needed = _ceil_div(gap, earn) if earn else None
    weeks_needed = None
    if visits_needed is not None and request.weekly_visits > 0:
        weeks_needed = (Decimal(visits_needed) / Decimal(request.weekly_visits)).quantize(
            Decimal("0.1")
        )

    feasible: Optional[bool] = None
    projected_points: Optional[Decimal] = None
    projected_spend: Optional[Decimal] = None
    weekly_plan: list[WeekSlot] = []

    if earn is not None and weeks_left is not None and request.weekly_visits > 0:
        total_visits = int((weeks_left * request.weekly_visits).to_integral_value(rounding="ROUND_FLOOR"))
        projected_points = request.current_points + earn * total_visits
        projected_spend = request.avg_budget * total_visits
        feasible = projected_points >= request.target_points
    elif earn is not None and request.weekly_visits > 0:
        total_visits = visits_needed or 0
        projected_points = request.target_points
        projected_spend = request.avg_budget * total_visits

    # 逐周计划：只在有截止时间时铺开，最多 12 周，避免无限表格。
    # 周数与 projected_* 使用同一口径（整周 × 每周次数），不出现「表格累计」和
    # 「预计累计」两个数对不上的情况。
    if weeks_left is not None and request.weekly_visits >= 0:
        full_weeks = int(weeks_left.to_integral_value(rounding="ROUND_FLOOR"))
        week_count = max(1, min(12, full_weeks))
        visits_per_week = request.weekly_visits
        if full_weeks == 0:
            # 不足一周时按剩余天数折算，避免把整周次数算进最后一周。
            days_left = max(0, (request.deadline - request.today).days) if request.deadline else 0
            visits_per_week = min(
                request.weekly_visits,
                int((Decimal(days_left) / Decimal("7") * request.weekly_visits).to_integral_value(rounding="ROUND_FLOOR")),
            )
        cumulative = request.current_points
        for index in range(1, week_count + 1):
            visits = visits_per_week
            earned = (earn * visits) if earn is not None else None
            if earned is not None:
                cumulative = cumulative + earned
            weekly_plan.append(
                WeekSlot(
                    week=index,
                    label=f"第 {index} 周",
                    visits=visits,
                    budget=(request.avg_budget * visits).quantize(Decimal("0.01")),
                    earned_points=earned,
                    cumulative_points=cumulative,
                    reached=cumulative >= request.target_points,
                )
            )

    alternatives: list[str] = []
    if gap == 0:
        alternatives.append("当前积分已足够，不需要额外消费；先确认目标商品仍在售。")
    if earn is not None and gap > 0:
        if request.weekly_visits == 0:
            alternatives.append(
                f"当前设为每周 0 次，若不消费无法自然获得积分；需至少 {visits_needed} 次消费。"
            )
        elif feasible is False and weeks_left is not None:
            required_per_week = _ceil_div(
                visits_needed or 0,
                max(Decimal("1"), weeks_left),
            )
            if required_per_week:
                spend = request.avg_budget * required_per_week
                alternatives.append(
                    f"按现有预算，需要提高到每周约 {required_per_week} 次"
                    f"（每周预算约 {spend.quantize(Decimal('0.01'))} 元）才能在截止前达成。"
                )
        if request.points_per_yuan is not None and feasible is False:
            alternatives.append(
                "提高单次预算也能加快积分累积，但请先确认这是可接受的花费，而不是为了积分硬凑。"
            )
    elif earn is None and gap > 0:
        alternatives.append(
            "缺少积分获取规则（消费 1 元得多少分 / 每单得多少分），"
            "无法估算需要几次消费；可查阅官方活动规则后补充。"
        )

    if request.draw_cost is not None and request.draw_cost > 0:
        draws = _ceil_div(request.draw_cost, Decimal("1"))
        if draws:
            alternatives.append(
                f"若改用抽奖：单次消耗 {request.draw_cost} 积分，只适合消耗已有积分，"
                "不会帮你获得目标积分；中奖结果不可预测，不作为达成路径。"
            )

    if request.expiring_points is not None and request.expiring_points > 0:
        warnings.append(
            f"有 {request.expiring_points} 积分即将到期，建议优先使用，"
            "不要为了攒目标而让现有积分过期。"
        )
    if request.weekly_visits >= 5:
        warnings.append("每周 5 次及以上属于高频消费，请确认符合自己的预算与饮食安排。")
    if weeks_left is not None and weeks_left == 0:
        warnings.append("截止日期已到或已过，该目标无法通过继续消费在活动期内达成。")

    return GoalPlan(
        target_name=request.target_name,
        target_points=request.target_points,
        current_points=request.current_points,
        gap=gap,
        deadline=request.deadline,
        weeks_left=weeks_left,
        earn_per_visit=earn,
        visits_needed=visits_needed,
        weeks_needed=weeks_needed,
        feasible=feasible,
        projected_points=projected_points,
        projected_spend=projected_spend,
        weekly_plan=tuple(weekly_plan),
        alternatives=tuple(alternatives),
        assumptions=tuple(assumptions),
        warnings=tuple(warnings),
    )


def lower_targets(
    targets: Sequence[Tuple[str, Decimal]], current_points: Decimal, limit: int = 3
) -> Tuple[Tuple[str, Decimal], ...]:
    """从候选目标里挑出「当前积分已经够」的，供用户换目标而不是硬攒。"""

    ready = [(name, points) for name, points in targets if points <= current_points]
    ready.sort(key=lambda x: x[1], reverse=True)
    return tuple(ready[:limit])
