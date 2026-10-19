"""三类积分方案：止损 / 稳健兑换 / 娱乐抽奖。

原则：**不为凑齐三张卡片而推荐不可执行的选项**。数据不足时如实标注 blocker。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional, Sequence, Tuple

from .expiry import ExpiryPolicy, ExpiryReport, classify_expiring_points
from .lottery import DrawPreflight, evaluate_activity
from .models import AccountSnapshot, LotteryActivity, Product
from .value import RedemptionComparison, compare_redemption_options

PLAN_STOP_LOSS = "A"
PLAN_STEADY = "B"
PLAN_FUN = "C"


@dataclass(frozen=True)
class PlanCard:
    code: str
    title: str
    applicable: bool
    actions: Tuple[str, ...] = ()
    blockers: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    def render(self) -> Tuple[str, ...]:
        head = f"【方案 {self.code}】{self.title}"
        lines = [head]
        if not self.applicable:
            lines.append("  暂不可用：" + "；".join(self.blockers or ("数据不足",)))
        else:
            lines.extend(f"  · {action}" for action in self.actions)
        lines.extend(f"  注：{note}" for note in self.notes)
        return tuple(lines)


@dataclass(frozen=True)
class PlanBundle:
    account: AccountSnapshot
    expiry: ExpiryReport
    redemption: RedemptionComparison
    preflight: Optional[DrawPreflight]
    cards: Tuple[PlanCard, ...]

    def render(self) -> Tuple[str, ...]:
        lines = ["—— 积分盘点 ——"]
        lines.extend(f"· {line}" for line in self.expiry.summary_lines())
        for card in self.cards:
            lines.append("")
            lines.extend(card.render())
        return tuple(lines)


def build_plans(
    account: Any,
    products: Sequence[Product] = (),
    activity: Optional[LotteryActivity] = None,
    *,
    now: Optional[datetime] = None,
    policy: Optional[ExpiryPolicy] = None,
    preferred_categories: Sequence[str] = (),
) -> PlanBundle:
    now = now or datetime.now()
    policy = policy or ExpiryPolicy()

    if isinstance(account, AccountSnapshot):
        snapshot = account
    elif isinstance(account, dict):
        snapshot = AccountSnapshot.from_mcp(account)
    else:
        snapshot = AccountSnapshot()

    expiry = classify_expiring_points(snapshot, today=now.date(), policy=policy)
    comparison = compare_redemption_options(
        products,
        snapshot,
        now=now,
        preferred_categories=preferred_categories,
    )
    preflight = evaluate_activity(activity, snapshot, now=now) if activity else None

    # A. 优先止损
    a_actions, a_blockers = [], []
    if expiry.total_at_risk <= 0:
        a_blockers.append("接口未显示本月/下月有即将过期积分")
    else:
        a_actions.append(
            f"优先处理即将过期的 {expiry.total_at_risk} 积分（本月 {expiry.this_month_total} + 下月 {expiry.next_month_total}）"
        )
        if comparison.affordable:
            for item in comparison.affordable[:3]:
                a_actions.append(
                    f"可兑换：{item.product.name}（{item.product.point} 积分，类目：{item.product.cat_name or '未标注'}）"
                )
        else:
            a_blockers.append("当前积分不足以兑换任何已上架商品，先攒够门槛再处理到期风险")
        if not expiry.has_batch_detail:
            a_actions.append("到期日期只到「月」粒度，具体哪一天过期接口未返回，建议不要拖到月末")
    plan_a = PlanCard(
        PLAN_STOP_LOSS,
        "优先止损（防止积分作废）",
        applicable=expiry.total_at_risk > 0 and bool(comparison.affordable),
        actions=tuple(a_actions),
        blockers=tuple(a_blockers),
        notes=(policy.disclaimer,),
    )

    # B. 稳健兑换
    b_actions, b_blockers = [], []
    if not products:
        b_blockers.append("商城接口未返回商品数据")
    elif not comparison.affordable:
        b_blockers.append("当前积分未达到任何可兑换商品的门槛")
    else:
        for item in comparison.affordable[:3]:
            b_actions.append(
                f"{item.product.name}：{item.product.point} 积分"
                f"（兑换期至 {item.product.down_time:%Y-%m-%d}）"
                if item.product.down_time
                else f"{item.product.name}：{item.product.point} 积分"
            )
        b_actions.append("按「用途 + 兑换门槛」选择，不按门槛低就认定性价比高")
    plan_b = PlanCard(
        PLAN_STEADY,
        "稳健兑换（确定性优先）",
        applicable=bool(b_actions),
        actions=tuple(b_actions),
        blockers=tuple(b_blockers),
        notes=comparison.notes,
    )

    # C. 娱乐抽奖
    c_actions, c_blockers, c_notes = [], [], []
    if preflight is None:
        c_blockers.append("尚未查询抽奖活动信息")
    elif not preflight.ok:
        c_blockers.extend(preflight.blockers)
    else:
        c_actions.append(
            f"{preflight.activity_name}：单次 {preflight.cost_per_draw} 积分，{preflight.consumption_text or '消耗以接口为准'}"
        )
        if preflight.warnings:
            c_notes.extend(preflight.warnings)
        c_actions.append("只在你明确同意的次数内执行，抽完即止，不追加")
    c_notes.append("抽奖是概率事件，接口未返回中奖概率，任何「期望收益」都不成立，请按娱乐预算参与。")
    plan_c = PlanCard(
        PLAN_FUN,
        "娱乐抽奖（体验随机结果）",
        applicable=bool(c_actions) and preflight is not None and preflight.ok,
        actions=tuple(c_actions),
        blockers=tuple(c_blockers),
        notes=tuple(c_notes),
    )

    return PlanBundle(
        account=snapshot,
        expiry=expiry,
        redemption=comparison,
        preflight=preflight,
        cards=(plan_a, plan_b, plan_c),
    )
