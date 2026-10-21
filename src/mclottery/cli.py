"""命令行入口：把 MCP 返回的 JSON 交给确定性计算层。

用法示例::

    python run.py account --input tests/fixtures/account_normal.json
    python run.py expiry  --input tests/fixtures/account_normal.json --today 2026-10-19
    python run.py products --input tests/fixtures/products_mixed.json --account tests/fixtures/account_normal.json
    python run.py preflight --input tests/fixtures/lottery_running.json --account tests/fixtures/account_normal.json
    python run.py plan --account tests/fixtures/account_normal.json --products tests/fixtures/products_mixed.json

所有子命令都可加 ``--json`` 输出机器可读结果。
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

from .expiry import ExpiryPolicy, classify_expiring_points
from .coupon import (
    STORE_COUPON_CAVEATS,
    TEXT_COUPON_CAVEATS,
    best_coupon,
    claim_advice,
    match_store_coupons,
    parse_coupons,
    parse_markdown_coupons,
    parse_store_coupons,
)
from .guard import (
    AuthorizationRequired,
    DrawGuard,
    DrawLimitExceeded,
    GuardError,
    ReconcileRequired,
)
from .mealplan import GoalRequest, build_goal_plan
from .nutrition import assess_day, parse_nutrition_foods, plan_meal
from .order import (
    build_confirmation,
    cart_fingerprint,
    duplicate_within,
    explain_order_status,
    load_order_ledger,
    parse_cart,
    verify_price,
)
from .redemption import (
    RedemptionRequest,
    describe_result,
    parse_redemption_result,
    preflight_redemption,
)
from .lottery import (
    Outcome,
    calculate_remaining_points,
    evaluate_activity,
    expected_value,
    total_cost,
)
from .models import (
    AccountSnapshot,
    DrawResult,
    LotteryActivity,
    parse_products,
)
from .plan import build_plans
from .value import compare_redemption_options


def _load(path: Optional[str]) -> Any:
    if not path or path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    raw = raw.strip()
    if not raw:
        raise SystemExit("输入为空：请用 --input 指定 JSON 文件，或从 stdin 传入")
    return json.loads(raw)


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _emit(payload: Any, lines: Sequence[str], as_json: bool) -> int:
    if as_json:
        print(json.dumps(_jsonable(payload), ensure_ascii=False, indent=2))
    else:
        print("\n".join(lines))
    return 0


def _parse_day(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    return datetime.strptime(text, "%Y-%m-%d").date()


def _parse_now(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise SystemExit(f"时间格式无法解析：{text}")


# --------------------------------------------------------------------- 子命令
def cmd_account(args: argparse.Namespace) -> int:
    snapshot = AccountSnapshot.from_mcp(_load(args.input))
    lines = [
        "—— 积分账户 ——",
        f"可用积分：{snapshot.available if snapshot.available is not None else '接口未返回'}",
        f"累计积分：{snapshot.accumulated if snapshot.accumulated is not None else '接口未返回'}",
        f"已使用：{snapshot.used if snapshot.used is not None else '接口未返回'}",
        f"已过期：{snapshot.expired if snapshot.expired is not None else '接口未返回'}",
        f"冻结：{snapshot.frozen if snapshot.frozen is not None else '接口未返回'}",
        f"本月将过期：{snapshot.expiring_this_month if snapshot.expiring_this_month is not None else '接口未返回'}",
        f"下月将过期：{snapshot.expiring_next_month if snapshot.expiring_next_month is not None else '接口未返回'}",
    ]
    if snapshot.missing_fields:
        lines.append("缺失字段：" + "、".join(snapshot.missing_fields))
    lines.append("说明：接口未返回分批到期明细，无法给出具体到期日期。")
    return _emit(snapshot, lines, args.json)


def cmd_expiry(args: argparse.Namespace) -> int:
    snapshot = AccountSnapshot.from_mcp(_load(args.input))
    policy = ExpiryPolicy(near_days=args.near_days, mid_days=args.mid_days)
    report = classify_expiring_points(snapshot, today=_parse_day(args.today), policy=policy)
    lines = ["—— 到期盘点 ——"]
    lines.extend(f"· {line}" for line in report.summary_lines())
    for batch in report.batches:
        precision = "月度汇总" if batch.precision == "month" else batch.precision
        lines.append(
            f"· {batch.label}：{batch.amount}（精度：{precision}，来源字段：{batch.source_field}）"
        )
    payload = _jsonable(report)
    payload["total_at_risk"] = str(report.total_at_risk)
    payload["has_risk"] = report.has_risk()
    return _emit(payload, lines, args.json)


def cmd_products(args: argparse.Namespace) -> int:
    products = parse_products(_load(args.input))
    account = AccountSnapshot.from_mcp(_load(args.account)) if args.account else AccountSnapshot()
    comparison = compare_redemption_options(
        products,
        account,
        now=_parse_now(args.now) or datetime.now(),
        preferred_categories=args.prefer or (),
    )
    lines = ["—— 积分商城（仅列当前真正可兑换项）——"]
    if comparison.affordable:
        for item in comparison.affordable:
            window = (
                f"｜兑换期至 {item.product.down_time:%Y-%m-%d %H:%M}"
                if item.product.down_time
                else "｜兑换期未返回"
            )
            lines.append(
                f"· {item.product.name}｜{item.product.point} 积分｜"
                f"{item.product.cat_name or '类目未返回'}{window}"
            )
    else:
        lines.append("· 当前没有积分足够且状态正常的商品")
    if comparison.short_of_points:
        lines.append("积分还差的商品：")
        for item in comparison.short_of_points[:5]:
            lines.append(f"· {item.product.name}｜{item.product.point} 积分｜{item.note}")
    lines.append(f"已排除 {len(comparison.excluded)} 个不可兑换条目（付费活动/已下架/已过兑换期等）")
    lines.extend(f"注：{note}" for note in comparison.notes)
    return _emit(comparison, lines, args.json)


def cmd_lottery(args: argparse.Namespace) -> int:
    activity = LotteryActivity.from_mcp(_load(args.input))
    lines = [
        "—— 抽奖活动 ——",
        f"活动：{activity.name or '未返回'}（{activity.activity_code or '活动号未返回'}）",
        f"状态：{activity.status_text or activity.status_code or '未返回'}",
        f"时间：{activity.begin_time} ~ {activity.end_time}",
        f"单次消耗：{activity.draw_point if activity.draw_point is not None else '未返回'} 积分",
        f"消耗规则：{activity.draw_type_text or '未返回'}",
        f"剩余次数：{activity.available_times if activity.available_times is not None else '未返回/不适用'}",
        f"可用积分：{activity.available_point if activity.available_point is not None else '未返回'}",
    ]
    decision = activity.decision
    lines.append(
        f"服务端资源判定：{'满足' if decision.resource_eligible else '不满足' if decision.resource_eligible is False else '未返回'}"
        + (f"（{decision.reason}）" if decision.reason else "")
    )
    if decision.next_consumption:
        lines.append(f"本次实际消耗：{decision.next_consumption.describe()}")
    if decision.fallback_consumption:
        lines.append(f"兜底消耗：{decision.fallback_consumption.describe()}")
    if activity.prizes:
        lines.append("奖品预览：" + "、".join(p.name for p in activity.prizes))
    lines.append("说明：接口未返回中奖概率，无法计算期望收益，也不构成任何中奖承诺。")
    return _emit(activity, lines, args.json)


def cmd_preflight(args: argparse.Namespace) -> int:
    activity = LotteryActivity.from_mcp(_load(args.input))
    account = AccountSnapshot.from_mcp(_load(args.account)) if args.account else None
    preflight = evaluate_activity(
        activity, account, now=_parse_now(args.now), draws=args.draws
    )
    lines = ["—— 抽奖前置校验 ——", f"结论：{'可以进入授权流程' if preflight.ok else '不可抽奖'}"]
    lines.extend(f"· {line}" for line in preflight.report_lines())
    remaining = calculate_remaining_points(
        (account.available if account else activity.available_point),
        preflight.cost_per_draw,
        args.draws,
    )
    lines.append(f"预计剩余积分：{remaining if remaining is not None else '成本或余额未明确，无法估算'}")
    return _emit(preflight, lines, args.json)


def cmd_ev(args: argparse.Namespace) -> int:
    payload = _load(args.input)
    raw = payload.get("outcomes") if isinstance(payload, dict) else payload
    outcomes = [
        Outcome(
            name=str(item.get("name", "")),
            value=None if item.get("value") is None else Decimal(str(item["value"])),
            probability=None
            if item.get("probability") is None
            else Decimal(str(item["probability"])),
        )
        for item in raw or []
    ]
    result = expected_value(outcomes)
    cost = None
    if isinstance(payload, dict) and payload.get("cost_per_draw") is not None:
        cost = Decimal(str(payload["cost_per_draw"]))
    lines = ["—— 抽奖期望价值 ——", f"· {result.display()}"]
    if cost is not None and result.is_conclusive:
        lines.append(f"· 单次成本：{cost} 积分；理论期望净价值 = {result.value - cost:.2f}（按同一价值单位）")
    lines.append("· 上述为理论期望，不是单次结果保证；概率缺失时不会给出任何数字。")
    return _emit(result, lines, args.json)


def cmd_plan(args: argparse.Namespace) -> int:
    account = AccountSnapshot.from_mcp(_load(args.account))
    products = parse_products(_load(args.products)) if args.products else ()
    activity = LotteryActivity.from_mcp(_load(args.activity)) if args.activity else None
    bundle = build_plans(
        account,
        products,
        activity,
        now=_parse_now(args.now),
        policy=ExpiryPolicy(near_days=args.near_days, mid_days=args.mid_days),
        preferred_categories=args.prefer or (),
    )
    lines = list(bundle.render())
    return _emit(bundle, lines, args.json)


def cmd_guard(args: argparse.Namespace) -> int:
    guard = DrawGuard(args.ledger)
    action = args.action

    if action == "status":
        status = guard.status()
        lines = [
            "—— 抽奖守卫状态 ——",
            f"今日已执行抽奖：{status['draws_today']} / {status['max_draws_per_day']}",
            f"台账：{status['ledger']}",
        ]
        if status["unresolved"]:
            lines.append("存在未结算请求（已冻结后续抽奖）：")
            for item in status["unresolved"]:
                lines.append(
                    f"· {item['attempt_id']}｜{item['status']}｜{item['activity_code']}｜{item['created_at']}"
                )
        else:
            lines.append("无未结算请求")
        return _emit(status, lines, args.json)

    if action == "propose":
        if not args.preflight_ok:
            raise GuardError("前置校验未通过（--preflight-ok 缺省为 false），不允许进入授权流程")
        intent = guard.propose(
            activity_code=args.activity_code or "",
            activity_name=args.activity_name or "",
            draws=args.draws,
            cost_per_draw=None if args.cost is None else Decimal(str(args.cost)),
            preflight_ok=True,
        )
        lines = ["—— 抽奖意图已登记 ——", intent.confirm_text(), f"intent_id：{intent.intent_id}"]
        return _emit(intent, lines, args.json)

    if action == "authorize":
        auth = guard.authorize(args.intent_id, explicit=args.explicit)
        lines = [
            "—— 授权已登记 ——",
            f"auth_id：{auth.auth_id}｜可执行 {auth.remaining} 次｜活动 {auth.activity_code}",
        ]
        return _emit(auth, lines, args.json)

    if action == "revoke":
        auth = guard.revoke(args.auth_id)
        return _emit(auth, ["—— 授权已撤销 ——", f"剩余可执行：{auth.remaining} 次"], args.json)

    if action == "begin":
        attempt = guard.begin(args.auth_id)
        lines = [
            "—— 允许执行一次真实抽奖 ——",
            f"attempt_id：{attempt.attempt_id}",
            "现在可以调用 draw-lottery 一次；返回后必须立即 settle。",
        ]
        return _emit(attempt, lines, args.json)

    if action == "settle":
        result = DrawResult.from_mcp(_load(args.result)) if args.result else None
        attempt = guard.settle(
            args.attempt_id,
            result=result,
            status=args.status,
            message=args.message or "",
            win=args.win,
        )
        lines = [
            "—— 结算完成 ——",
            f"attempt_id：{attempt.attempt_id}｜状态：{attempt.status}｜中奖：{attempt.win}",
        ]
        if attempt.status == "UNKNOWN":
            lines.append("状态未知：已冻结后续抽奖，请先查询奖品/记录对账，禁止直接重试。")
        return _emit(attempt, lines, args.json)

    if action == "reconcile":
        attempt = guard.reconcile(args.attempt_id, args.status or "", note=args.note or "", verified=args.verified)
        return _emit(
            attempt,
            ["—— 已对账 ——", f"attempt_id：{attempt.attempt_id}｜最终状态：{attempt.status}"],
            args.json,
        )

    raise SystemExit(f"未知的 guard 子动作：{action}")


def cmd_calc(args: argparse.Namespace) -> int:
    cost = Decimal(str(args.cost)) if args.cost is not None else None
    available = Decimal(str(args.available)) if args.available is not None else None
    total = total_cost(cost, args.draws)
    remaining = calculate_remaining_points(available, cost, args.draws)
    payload = {
        "cost_per_draw": cost,
        "draws": args.draws,
        "total_cost": total,
        "available": available,
        "remaining": remaining,
        "note": "预计值，不代表工具执行后的真实余额",
    }
    lines = [
        "—— 成本试算 ——",
        f"单次消耗：{cost if cost is not None else '未明确'}｜次数：{args.draws}",
        f"总成本：{total if total is not None else '无法计算（成本或次数未明确）'}",
        f"预计剩余：{remaining if remaining is not None else '无法计算（成本或余额未明确）'}",
        "以上为预计值，实际扣减以 draw-lottery 返回为准。",
    ]
    return _emit(payload, lines, args.json)


# --------------------------------------------------------------------- 入口
def _dec(value: Optional[str]) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    return Decimal(str(value))


def cmd_nutrition(args: argparse.Namespace) -> int:
    facts = parse_nutrition_foods(_load(args.input))
    target = Decimal(str(args.target))
    combo, excluded = plan_meal(
        facts,
        target,
        max_items=args.max_items,
        prefer_high_protein=args.prefer_protein,
    )
    payload = {
        "parsed_items": len(facts),
        "target_kcal": target,
        "combo": combo.as_dict() if combo else None,
        "excluded_without_energy": [f.name for f in excluded],
        "note": "营养数值来自 list-nutrition-foods；不构成医疗或减重建议。",
    }
    lines = ["—— 热量组合 ——", f"解析餐品：{len(facts)} 个｜目标：{target} kcal"]
    if combo is None:
        lines.append("没有可用的热量数据，无法组合；请检查接口返回是否含 energy 字段。")
    else:
        names = " + ".join(f.name for f in combo.items)
        totals = combo.totals
        lines += [
            f"建议组合：{names}",
            f"合计热量：{totals.energy_kcal} kcal（目标偏差 {combo.deviation_kcal}）",
            f"蛋白质：{totals.protein_g if totals.protein_g is not None else '接口未返回'} g"
            f"｜脂肪：{totals.fat_g if totals.fat_g is not None else '接口未返回'} g"
            f"｜碳水：{totals.carbohydrate_g if totals.carbohydrate_g is not None else '接口未返回'} g",
            f"钠：{totals.sodium_mg if totals.sodium_mg is not None else '接口未返回'} mg",
        ]
        lines.extend(f"注意：{n}" for n in combo.notes)
    if excluded:
        lines.append(f"未参与计算（缺热量字段）：{'、'.join(f.name for f in excluded)}")
    return _emit(payload, lines, args.json)


def cmd_mealplan(args: argparse.Namespace) -> int:
    deadline = _parse_day(args.deadline)
    today = _parse_day(args.today) or date.today()
    request = GoalRequest(
        target_name=args.name,
        target_points=Decimal(str(args.target_points)),
        current_points=Decimal(str(args.current_points)),
        today=today,
        weekly_visits=args.weekly_visits,
        budget_min=Decimal(str(args.budget_min)),
        budget_max=Decimal(str(args.budget_max)),
        deadline=deadline,
        points_per_visit=_dec(args.points_per_visit),
        points_per_yuan=_dec(args.points_per_yuan),
        draw_cost=_dec(args.draw_cost),
        expiring_points=_dec(args.expiring),
    )
    plan = build_goal_plan(request)
    payload = plan.as_dict()
    lines = [
        "—— 积分目标计划 ——",
        f"目标：{plan.target_name}｜需要 {plan.target_points} 分｜当前 {plan.current_points} 分｜缺口 {plan.gap} 分",
    ]
    if plan.weeks_left is not None:
        lines.append(f"距截止：{plan.weeks_left} 周（截止 {plan.deadline}）")
    else:
        lines.append("未提供截止日期，无法计算达成时间。")
    if plan.earn_per_visit is None:
        lines.append("每次消费可得积分：缺少规则，未做估算。")
    else:
        lines.append(f"每次消费可得积分：约 {plan.earn_per_visit} 分（按给定规则推算）")
        lines.append(
            f"需要消费：约 {plan.visits_needed} 次"
            + (f"｜按每周 {args.weekly_visits} 次，约 {plan.weeks_needed} 周" if plan.weeks_needed else "")
        )
    if plan.feasible is not None:
        lines.append(
            "截止前能否达成：" + ("可以" if plan.feasible else "按当前频率与预算不够")
            + (f"｜预计累计 {plan.projected_points} 分" if plan.projected_points is not None else "")
            + (f"｜预计总支出 {plan.projected_spend} 元" if plan.projected_spend is not None else "")
        )
    if plan.weekly_plan:
        lines.append("逐周计划：")
        for week in plan.weekly_plan:
            earned = f"+{week.earned_points}" if week.earned_points is not None else "积分规则缺失"
            lines.append(
                f"  {week.label}：{week.visits} 次｜预算 {week.budget} 元｜{earned}"
                f"｜累计 {week.cumulative_points}{'｜已达标' if week.reached else ''}"
            )
    lines.extend(f"可以考虑：{a}" for a in plan.alternatives)
    lines.extend(f"前提：{a}" for a in plan.assumptions)
    lines.extend(f"提醒：{w}" for w in plan.warnings)
    return _emit(payload, lines, args.json)


def cmd_coupons(args: argparse.Namespace) -> int:
    coupons = parse_coupons(_load(args.input))
    cart_total = Decimal(str(args.cart_total))
    delivery_fee = Decimal(str(args.delivery_fee or "0"))
    decision = best_coupon(cart_total, coupons, delivery_fee=delivery_fee)
    payload = decision.as_dict()
    payload["claim_advice"] = list(claim_advice(coupons))
    lines = ["—— 优惠券试算 ——", f"订单金额：{cart_total} 元｜配送费：{delivery_fee} 元｜券：{len(coupons)} 张"]
    if decision.best is None:
        lines.append("当前没有可确认适用的券。")
    else:
        best = decision.best
        lines.append(
            f"建议用：「{best.coupon.name}」｜实付 {best.payable} 元｜省 {best.saved} 元｜{best.reason}"
        )
    for quote in decision.quotes:
        if decision.best is not None and quote is decision.best:
            continue
        lines.append(f"  · {quote.coupon.name}：{quote.status}｜{quote.reason}")
    if decision.unverified:
        lines.append(f"有 {len(decision.unverified)} 张券字段不全，需在门店券列表确认后再用。")
    lines.extend(f"说明：{a}" for a in decision.assumptions)
    lines.extend(f"领券：{a}" for a in payload["claim_advice"])
    return _emit(payload, lines, args.json)


def cmd_order(args: argparse.Namespace) -> int:
    lines = parse_cart(_load(args.input))
    price_check = verify_price(
        lines,
        _dec(args.remote_total),
        delivery_fee=Decimal(str(args.delivery_fee or "0")),
        discount=Decimal(str(args.discount or "0")),
    )
    confirmation = build_confirmation(
        store_name=args.store_name,
        store_id=args.store_id,
        meal_type=args.meal_type,
        lines=lines,
        price_check=price_check,
        discount=_dec(args.discount),
        delivery_fee=_dec(args.delivery_fee),
        coupon_name=args.coupon_name,
    )
    payload = confirmation.as_dict()
    fingerprint = cart_fingerprint(args.store_id, args.meal_type, lines)
    payload["fingerprint"] = fingerprint
    now = _parse_now(args.now) or datetime.now()
    if args.ledger:
        previous = duplicate_within(load_order_ledger(args.ledger), fingerprint, now)
        if previous is not None:
            payload["duplicate_of"] = previous.get("created_at")
    out = [
        "—— 下单前确认卡 ——",
        f"门店：{confirmation.store_name}（{confirmation.store_id or 'ID 未返回'}）",
        f"取餐方式：{confirmation.meal_type}",
        "商品：",
    ]
    for line in lines:
        out.append(
            f"  · {line.name} × {line.quantity}｜单价 "
            f"{line.unit_price if line.unit_price is not None else '未返回'}"
            f"｜小计 {line.subtotal if line.subtotal is not None else '无法计算'}"
        )
    out += [
        f"小计：{confirmation.subtotal if confirmation.subtotal is not None else '无法计算'}",
        f"优惠：{confirmation.discount if confirmation.discount is not None else '无/未返回'}"
        f"｜配送费：{confirmation.delivery_fee if confirmation.delivery_fee is not None else '无/未返回'}",
        f"应付：{confirmation.payable if confirmation.payable is not None else '接口未返回，禁止下单'}",
        f"价格复核：{price_check.reason}",
        f"购物车指纹：{fingerprint}",
    ]
    if payload.get("duplicate_of"):
        out.append(f"注意：{payload['duplicate_of']} 有一笔相同购物车记录，先确认是否已下单。")
    out.extend(f"阻断：{b}" for b in confirmation.blockers)
    out.extend(f"提醒：{r}" for r in confirmation.reminders)
    out.append("本卡只做本地复核；下单需用户明确确认，结果以 create-order 返回为准。")
    return _emit(payload, out, args.json)


def cmd_order_status(args: argparse.Namespace) -> int:
    data = _load(args.input)
    status = None
    if isinstance(data, dict):
        for key in ("statusText", "orderStatusText", "status", "orderStatus"):
            if data.get(key):
                status = str(data[key])
                break
        inner = data.get("data")
        if status is None and isinstance(inner, dict):
            for key in ("statusText", "orderStatusText", "status", "orderStatus"):
                if inner.get(key):
                    status = str(inner[key])
                    break
    explanation = explain_order_status(status)
    payload = {"status_text": status, "explanation": explanation}
    lines = [
        "—— 订单状态解读 ——",
        f"接口状态：{status or '未返回'}",
        f"含义：{explanation}",
        "取消操作前先确认订单是否已支付：已支付通常走退款，不等于原路秒退。",
    ]
def cmd_redeem(args: argparse.Namespace) -> int:
    request = RedemptionRequest(
        sku_id=args.sku_id,
        spu_name=args.spu_name,
        sku_name=args.sku_name or "",
        spu_category=args.category,
        points_required=_dec(args.points),
        cash_price=_dec(args.price),
        count=args.count,
        address_id=args.address_id,
        available_points=_dec(args.available),
    )
    check = preflight_redemption(request)
    payload = check.as_dict()
    totals = check.totals
    lines = ["—— 积分兑换预检 ——"]
    lines.append(
        f"商品：{totals['spu_name']}{(' ' + totals['sku_name']) if totals['sku_name'] else ''}"
        f" ×{totals['count']}｜品类：{totals['spu_category'] or '未提供'}"
    )
    lines.append(
        f"预计支出：{totals['total_points'] if totals['total_points'] is not None else '未提供'} 积分"
        f" + {totals['total_cash'] if totals['total_cash'] is not None else '未提供'} 元"
    )
    if totals["remaining_points"] is not None:
        lines.append(f"兑换后预计剩余：{totals['remaining_points']} 积分（预计值）")
    lines.append("可以进入下单确认" if check.ready else "暂不可下单")
    lines.extend(f"阻断：{b}" for b in check.blockers)
    lines.extend(
        f"提醒：{w}" for w in check.warnings if not w.startswith("兑换后预计剩余")
    )
    lines.extend(f"说明：{n}" for n in check.notes)
    return _emit(payload, lines, args.json)


def cmd_redeem_result(args: argparse.Namespace) -> int:
    result = parse_redemption_result(_load(args.input))
    payload = result.as_dict()
    lines = ["—— 兑换结果解读 ——", *describe_result(result)]
    if result.missing_fields:
        lines.append(f"接口未返回：{'、'.join(result.missing_fields)}")
    return _emit(payload, lines, args.json)


def cmd_store_coupons(args: argparse.Namespace) -> int:
    coupons = parse_store_coupons(_load(args.input))
    codes = [c.strip() for c in (args.products or "").split(",") if c.strip()]
    matches = match_store_coupons(coupons, codes)
    payload = {
        "count": len(coupons),
        "cart_products": codes,
        "matches": [
            {**m.coupon.as_dict(), "applicable": m.applicable, "reason": m.reason}
            for m in matches
        ],
        "caveats": list(STORE_COUPON_CAVEATS),
    }
    lines = [
        "—— 门店可用券 ——",
        f"券：{len(coupons)} 张｜购物车商品：{len(codes)} 个",
    ]
    for m in matches:
        flag = "可能适用" if m.applicable else "不匹配"
        lines.append(f"· {m.coupon.title}｜{flag}｜{m.reason}")
        if m.coupon.trade_period:
            lines.append(f"  有效期：{m.coupon.trade_period}")
        if m.coupon.price_hint is not None:
            lines.append(f"  标题价：{m.coupon.price_hint} 元（用券价，不是折扣金额）")
    lines.extend(f"注意：{c}" for c in STORE_COUPON_CAVEATS)
    return _emit(payload, lines, args.json)


def cmd_coupon_text(args: argparse.Namespace) -> int:
    text = Path(args.input).read_text(encoding="utf-8")
    coupons = parse_markdown_coupons(text)
    payload = {
        "count": len(coupons),
        "coupons": [c.as_dict() for c in coupons],
        "claimable": [c.title for c in coupons if c.claimable],
        "caveats": list(TEXT_COUPON_CAVEATS),
    }
    lines = ["—— 券文本解析 ——", f"解析到 {len(coupons)} 张券"]
    for c in coupons:
        bits = [c.title]
        if c.status:
            bits.append(c.status)
        if c.price_text:
            bits.append(c.price_text)
        if c.valid_period:
            bits.append(c.valid_period)
        if c.tags:
            bits.append("/".join(c.tags))
        lines.append("· " + "｜".join(bits))
    lines.extend(f"注意：{c}" for c in TEXT_COUPON_CAVEATS)
    return _emit(payload, lines, args.json)


# --------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mclottery",
        description="麦门全能管家 · 点餐/优惠/营养/积分计算与抽奖守卫",
        epilog="所有子命令均支持 --json 输出机器可读结果。",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("account", help="解析 query-my-account 返回", parents=[common])
    p.add_argument("--input")
    p.set_defaults(func=cmd_account)

    p = sub.add_parser("expiry", help="积分到期分类", parents=[common])
    p.add_argument("--input")
    p.add_argument("--today", help="YYYY-MM-DD")
    p.add_argument("--near-days", type=int, default=7)
    p.add_argument("--mid-days", type=int, default=30)
    p.set_defaults(func=cmd_expiry)

    p = sub.add_parser("products", help="商城可兑换项筛选", parents=[common])
    p.add_argument("--input")
    p.add_argument("--account")
    p.add_argument("--now")
    p.add_argument("--prefer", action="append", help="偏好类目，可重复")
    p.set_defaults(func=cmd_products)

    p = sub.add_parser("lottery", help="解析 query-lottery-info 返回", parents=[common])
    p.add_argument("--input")
    p.set_defaults(func=cmd_lottery)

    p = sub.add_parser("preflight", help="抽奖前置校验", parents=[common])
    p.add_argument("--input")
    p.add_argument("--account")
    p.add_argument("--now")
    p.add_argument("--draws", type=int, default=1)
    p.set_defaults(func=cmd_preflight)

    p = sub.add_parser("ev", help="期望价值（缺概率则拒绝出数）", parents=[common])
    p.add_argument("--input")
    p.set_defaults(func=cmd_ev)

    p = sub.add_parser("plan", help="生成 A/B/C 三类方案", parents=[common])
    p.add_argument("--account", required=True)
    p.add_argument("--products")
    p.add_argument("--activity")
    p.add_argument("--now")
    p.add_argument("--near-days", type=int, default=7)
    p.add_argument("--mid-days", type=int, default=30)
    p.add_argument("--prefer", action="append")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("calc", help="成本与剩余积分试算", parents=[common])
    p.add_argument("--cost")
    p.add_argument("--available")
    p.add_argument("--draws", type=int, default=1)
    p.set_defaults(func=cmd_calc)

    p = sub.add_parser("nutrition", help="按热量目标组合餐品", parents=[common])
    p.add_argument("--input", required=True, help="list-nutrition-foods 的返回 JSON")
    p.add_argument("--target", required=True, help="目标热量 kcal")
    p.add_argument("--max-items", type=int, default=3)
    p.add_argument("--prefer-protein", action="store_true")
    p.set_defaults(func=cmd_nutrition)

    p = sub.add_parser("mealplan", help="积分缺口 → 饮食计划", parents=[common])
    p.add_argument("--name", default="目标商品")
    p.add_argument("--target-points", required=True)
    p.add_argument("--current-points", required=True)
    p.add_argument("--deadline", help="YYYY-MM-DD")
    p.add_argument("--today", help="YYYY-MM-DD，默认取本机日期")
    p.add_argument("--weekly-visits", type=int, default=2)
    p.add_argument("--budget-min", default="0")
    p.add_argument("--budget-max", default="0")
    p.add_argument("--points-per-visit", help="每单可得积分（来自官方规则）")
    p.add_argument("--points-per-yuan", help="每元可得积分（来自官方规则）")
    p.add_argument("--draw-cost")
    p.add_argument("--expiring")
    p.set_defaults(func=cmd_mealplan)

    p = sub.add_parser("coupons", help="一单最省券试算", parents=[common])
    p.add_argument("--input", required=True, help="券列表 JSON（query-my-coupons / query-store-coupons）")
    p.add_argument("--cart-total", required=True)
    p.add_argument("--delivery-fee", default="0")
    p.set_defaults(func=cmd_coupons)

    p = sub.add_parser("store-coupons", help="门店券适用性匹配（不算金额）", parents=[common])
    p.add_argument("--input", required=True, help="query-store-coupons 返回 JSON")
    p.add_argument("--products", help="购物车商品 code，逗号分隔")
    p.set_defaults(func=cmd_store_coupons)

    p = sub.add_parser("coupon-text", help="解析麦麦省/卡包 Markdown 文本", parents=[common])
    p.add_argument("--input", required=True, help="保存为 txt/md 的券列表文本")
    p.set_defaults(func=cmd_coupon_text)

    p = sub.add_parser("order", help="下单前价格复核与确认卡", parents=[common])
    p.add_argument("--input", required=True, help="购物车 JSON")
    p.add_argument("--store-name", required=True)
    p.add_argument("--store-id")
    p.add_argument("--meal-type", default="到店自取")
    p.add_argument("--remote-total", help="calculate-price 返回的应付总额")
    p.add_argument("--delivery-fee", default="0")
    p.add_argument("--discount", default="0")
    p.add_argument("--coupon-name")
    p.add_argument("--ledger", help="下单台账 JSONL，用于探测重复提交")
    p.add_argument("--now")
    p.set_defaults(func=cmd_order)

    p = sub.add_parser("order-status", help="解读订单状态", parents=[common])
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_order_status)

    p = sub.add_parser("redeem", help="积分兑换下单前预检", parents=[common])
    p.add_argument("--sku-id", required=True)
    p.add_argument("--spu-name", default="未命名商品")
    p.add_argument("--sku-name")
    p.add_argument("--category", required=True, choices=["1", "2"], help='1=虚拟，2=实物')
    p.add_argument("--points", help="单件所需积分")
    p.add_argument("--price", help="单件现金价格")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--address-id", help="实物商品必填")
    p.add_argument("--available", help="当前可用积分")
    p.set_defaults(func=cmd_redeem)

    p = sub.add_parser("redeem-result", help="解析 mall-create-order 返回", parents=[common])
    p.add_argument("--input", required=True)
    p.set_defaults(func=cmd_redeem_result)

    g = sub.add_parser("guard", help="抽奖守卫：授权/执行/结算/对账", parents=[common])
    g.add_argument(
        "action",
        choices=["status", "propose", "authorize", "revoke", "begin", "settle", "reconcile"],
    )
    g.add_argument("--ledger", default=str(Path.home() / ".mclottery" / "ledger.jsonl"))
    g.add_argument("--activity-code")
    g.add_argument("--activity-name")
    g.add_argument("--draws", type=int, default=1)
    g.add_argument("--cost")
    g.add_argument("--preflight-ok", action="store_true")
    g.add_argument("--intent-id")
    g.add_argument("--auth-id")
    g.add_argument("--attempt-id")
    g.add_argument("--explicit", action="store_true", help="用户已明确说「抽」")
    g.add_argument("--status", choices=["SUCCESS", "FAILED", "UNKNOWN"])
    g.add_argument("--win", type=lambda v: v.lower() in {"1", "true", "yes"}, default=None)
    g.add_argument("--message")
    g.add_argument("--note")
    g.add_argument("--verified", action="store_true", help="已获得可关联原请求的官方结果；无新增奖品不算")
    g.add_argument("--result", help="draw-lottery 的原始返回 JSON")
    g.set_defaults(func=cmd_guard)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (GuardError, AuthorizationRequired, ReconcileRequired, DrawLimitExceeded) as exc:
        print(f"[守卫拦截] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
