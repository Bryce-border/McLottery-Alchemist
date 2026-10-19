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
from .guard import (
    AuthorizationRequired,
    DrawGuard,
    DrawLimitExceeded,
    GuardError,
    ReconcileRequired,
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
        attempt = guard.reconcile(args.attempt_id, args.status or "", note=args.note or "")
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
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mclottery",
        description="麦当劳积分炼金术士 · 确定性计算与抽奖守卫",
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

    g = sub.add_parser("guard", help="抽奖守卫：授权/执行/结算/对账", parents=[common])
    g.add_argument(
        "action",
        choices=["status", "propose", "authorize", "revoke", "begin", "settle", "reconcile"],
    )
    g.add_argument("--ledger", default=".mclottery/ledger.jsonl")
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
