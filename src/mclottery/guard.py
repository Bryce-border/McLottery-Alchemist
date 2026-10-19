"""抽奖守卫：授权、单次执行、未知状态冻结。

为什么必须有这一层：``draw-lottery`` 的真实契约是**无入参、一次一抽、
服务端原子扣减、无幂等键**。也就是说：

* 「抽 3 次」只能是 3 次串行调用，每次都要有授权额度；
* 服务端不提供幂等键，重复提交没有任何服务端保护，只能靠本地台账
  + 「未知状态先冻结再对账」来避免重复扣分；
* 提示词不能当成安全机制，所以这里用状态机把规则固化。

台账只记录必要字段（时间、活动号、消耗、结果、请求 ID），
不记录账户 ID、Token 或任何凭据。
"""

from __future__ import annotations

import json
import os
from functools import wraps
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import DrawResult

STATUS_IN_FLIGHT = "IN_FLIGHT"
STATUS_SUCCESS = "SUCCESS"
STATUS_FAILED = "FAILED"
STATUS_UNKNOWN = "UNKNOWN"
SETTLED = {STATUS_SUCCESS, STATUS_FAILED}
UNRESOLVED = {STATUS_IN_FLIGHT, STATUS_UNKNOWN}

LEDGER_VERSION = 1


class GuardError(RuntimeError):
    """守卫层错误基类。"""


class AuthorizationRequired(GuardError):
    """用户未给出明确授权。"""


class ReconcileRequired(GuardError):
    """存在状态未知的抽奖请求，必须先对账。"""


class DrawLimitExceeded(GuardError):
    """超过授权额度或单日上限。"""


class UnknownReference(GuardError):
    """引用的 intent / authorization / attempt 不存在。"""


@dataclass(frozen=True)
class Intent:
    intent_id: str
    activity_code: str
    activity_name: str
    draws: int
    cost_per_draw: Optional[Decimal]
    created_at: datetime
    summary: str

    @property
    def total_cost(self) -> Optional[Decimal]:
        if self.cost_per_draw is None:
            return None
        return self.cost_per_draw * self.draws

    def confirm_text(self) -> str:
        cost = "未明确" if self.total_cost is None else str(self.total_cost)
        return (
            f"即将参加「{self.activity_name or self.activity_code}」，"
            f"抽 {self.draws} 次，单次消耗 {self.cost_per_draw if self.cost_per_draw is not None else '未明确'} 积分，"
            f"本次最多消耗 {cost} 积分。确认后才会调用真实抽奖接口。"
        )


@dataclass(frozen=True)
class Authorization:
    auth_id: str
    intent_id: str
    activity_code: str
    draws_granted: int
    draws_used: int
    cost_per_draw: Optional[Decimal]
    created_at: datetime
    revoked: bool = False

    @property
    def remaining(self) -> int:
        return max(self.draws_granted - self.draws_used, 0)


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    auth_id: str
    activity_code: str
    cost: Optional[Decimal]
    status: str
    created_at: datetime
    win: Optional[bool] = None
    message: str = ""
    settled_at: Optional[datetime] = None
    note: str = ""

    @property
    def unresolved(self) -> bool:
        return self.status in UNRESOLVED


def ledger_transaction(method):
    """Lock, reload, check and persist as one local transaction. Busy fails closed."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if self.ledger_path is None:
            return method(self, *args, **kwargs)
        lock_path = self.ledger_path.with_suffix(self.ledger_path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise GuardError("台账正被其他进程使用，本次未执行，请稍后检查状态") from exc
            try:
                self._intents.clear()
                self._auths.clear()
                self._auth_draws.clear()
                self._revoked.clear()
                self._attempts.clear()
                self._attempt_order.clear()
                self._load()
                return method(self, *args, **kwargs)
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return wrapped


class DrawGuard:
    """事件溯源式台账，append-only 写入 JSONL。"""

    def __init__(
        self,
        ledger_path: Optional[Path | str] = None,
        *,
        max_draws_per_authorization: int = 5,
        max_draws_per_day: int = 20,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.ledger_path = Path(ledger_path).expanduser().resolve() if ledger_path else None
        self.max_draws_per_authorization = max_draws_per_authorization
        self.max_draws_per_day = max_draws_per_day
        self._clock = clock or datetime.now
        self._intents: Dict[str, Intent] = {}
        self._auths: Dict[str, Authorization] = {}
        self._auth_draws: Dict[str, int] = {}
        self._revoked: set[str] = set()
        self._attempts: Dict[str, Attempt] = {}
        self._attempt_order: List[str] = []
        self._load()

    # ------------------------------------------------------------------ 台账 IO
    def _load(self) -> None:
        if not self.ledger_path or not self.ledger_path.exists():
            return
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise GuardError("台账损坏，暂停抽奖；请核实原始记录，不能跳过或清空") from exc
            self._apply(event)

    def _append(self, event: Dict[str, Any]) -> None:
        event.setdefault("v", LEDGER_VERSION)
        if self.ledger_path:
            self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        self._apply(event)

    def _apply(self, event: Dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "intent":
            self._intents[event["intent_id"]] = Intent(
                intent_id=event["intent_id"],
                activity_code=event.get("activity_code", ""),
                activity_name=event.get("activity_name", ""),
                draws=int(event.get("draws", 1)),
                cost_per_draw=_dec(event.get("cost_per_draw")),
                created_at=_dt(event.get("created_at")),
                summary=event.get("summary", ""),
            )
        elif kind == "authorization":
            auth = Authorization(
                auth_id=event["auth_id"],
                intent_id=event["intent_id"],
                activity_code=event.get("activity_code", ""),
                draws_granted=int(event.get("draws", 1)),
                draws_used=0,
                cost_per_draw=_dec(event.get("cost_per_draw")),
                created_at=_dt(event.get("created_at")),
            )
            self._auths[auth.auth_id] = auth
            self._auth_draws.setdefault(auth.auth_id, 0)
        elif kind == "revoke":
            self._revoked.add(event["auth_id"])
        elif kind == "attempt":
            attempt = Attempt(
                attempt_id=event["attempt_id"],
                auth_id=event["auth_id"],
                activity_code=event.get("activity_code", ""),
                cost=_dec(event.get("cost")),
                status=event.get("status", STATUS_IN_FLIGHT),
                created_at=_dt(event.get("created_at")),
            )
            self._attempts[attempt.attempt_id] = attempt
            self._attempt_order.append(attempt.attempt_id)
            self._auth_draws[attempt.auth_id] = self._auth_draws.get(attempt.auth_id, 0) + 1
        elif kind in {"settle", "reconcile"}:
            attempt = self._attempts.get(event["attempt_id"])
            if attempt is None:
                return
            self._attempts[attempt.attempt_id] = Attempt(
                attempt_id=attempt.attempt_id,
                auth_id=attempt.auth_id,
                activity_code=attempt.activity_code,
                cost=attempt.cost,
                status=event.get("status", attempt.status),
                created_at=attempt.created_at,
                win=event.get("win", attempt.win),
                message=event.get("message", attempt.message),
                settled_at=_dt(event.get("settled_at")) or self._clock(),
                note=event.get("note", attempt.note),
            )

    # ------------------------------------------------------------------ 状态查询
    def unresolved_attempts(self) -> Tuple[Attempt, ...]:
        return tuple(
            self._attempts[aid] for aid in self._attempt_order
            if self._attempts[aid].unresolved
        )

    def attempts_today(self) -> Tuple[Attempt, ...]:
        today = self._clock().date()
        return tuple(
            self._attempts[aid] for aid in self._attempt_order
            if self._attempts[aid].created_at.date() == today
        )

    def get_authorization(self, auth_id: str) -> Authorization:
        auth = self._auths.get(auth_id)
        if auth is None:
            raise UnknownReference(f"未找到授权记录：{auth_id}")
        used = self._auth_draws.get(auth_id, 0)
        return Authorization(
            auth_id=auth.auth_id,
            intent_id=auth.intent_id,
            activity_code=auth.activity_code,
            draws_granted=auth.draws_granted,
            draws_used=used,
            cost_per_draw=auth.cost_per_draw,
            created_at=auth.created_at,
            revoked=auth.auth_id in self._revoked,
        )

    @ledger_transaction
    def status(self) -> Dict[str, Any]:
        unresolved = self.unresolved_attempts()
        return {
            "draws_today": len(self.attempts_today()),
            "max_draws_per_day": self.max_draws_per_day,
            "unresolved": [
                {
                    "attempt_id": a.attempt_id,
                    "status": a.status,
                    "activity_code": a.activity_code,
                    "cost": str(a.cost) if a.cost is not None else None,
                    "created_at": a.created_at.isoformat(timespec="seconds"),
                }
                for a in unresolved
            ],
            "blocked": bool(unresolved),
            "ledger": str(self.ledger_path) if self.ledger_path else "in-memory",
        }

    # ------------------------------------------------------------------ 流程
    @ledger_transaction
    def propose(
        self,
        *,
        activity_code: str,
        activity_name: str,
        draws: int,
        cost_per_draw: Optional[Decimal],
        preflight_ok: bool,
        blockers: Tuple[str, ...] = (),
    ) -> Intent:
        """登记一次抽奖意图。preflight 未通过时直接拒绝，连意图都不产生。"""

        if not preflight_ok:
            raise GuardError(
                "前置校验未通过，不能进入授权流程：" + "；".join(blockers)
            )
        if draws <= 0:
            raise GuardError("抽奖次数必须为正整数")
        if draws > self.max_draws_per_authorization:
            raise DrawLimitExceeded(
                f"单次授权上限为 {self.max_draws_per_authorization} 次，请分批确认"
            )

        now = self._clock()
        intent_id = _new_id("intent")
        intent = Intent(
            intent_id=intent_id,
            activity_code=activity_code,
            activity_name=activity_name,
            draws=draws,
            cost_per_draw=cost_per_draw,
            created_at=now,
            summary=f"{activity_name or activity_code} × {draws} 次",
        )
        self._append(
            {
                "type": "intent",
                "intent_id": intent_id,
                "activity_code": activity_code,
                "activity_name": activity_name,
                "draws": draws,
                "cost_per_draw": None if cost_per_draw is None else str(cost_per_draw),
                "created_at": now.isoformat(timespec="seconds"),
                "summary": intent.summary,
            }
        )
        return intent

    @ledger_transaction
    def authorize(self, intent_id: str, *, explicit: bool = False) -> Authorization:
        """只有用户明确表达「抽」才能授权。

        「看看能不能中」「测测欧气」这类表达按 explicit=False 处理，会被拒绝。
        """

        if not explicit:
            raise AuthorizationRequired(
                "未获得明确授权：仅「直接帮我抽一次」这类明确表达才构成扣分授权"
            )
        intent = self._intents.get(intent_id)
        if intent is None:
            raise UnknownReference(f"未找到抽奖意图：{intent_id}")

        if any(auth.intent_id == intent_id for auth in self._auths.values()):
            raise AuthorizationRequired("该意图已授权，不能重复增加额度；新操作需新的明确授权")
        now = self._clock()
        auth_id = _new_id("auth")
        self._append(
            {
                "type": "authorization",
                "auth_id": auth_id,
                "intent_id": intent_id,
                "activity_code": intent.activity_code,
                "draws": intent.draws,
                "cost_per_draw": None
                if intent.cost_per_draw is None
                else str(intent.cost_per_draw),
                "created_at": now.isoformat(timespec="seconds"),
                "explicit": True,
            }
        )
        return self.get_authorization(auth_id)

    @ledger_transaction
    def revoke(self, auth_id: str) -> Authorization:
        auth = self.get_authorization(auth_id)
        if auth.remaining <= 0:
            return auth
        self._append({"type": "revoke", "auth_id": auth_id, "at": self._clock().isoformat(timespec="seconds")})
        return self.get_authorization(auth_id)

    @ledger_transaction
    def begin(self, auth_id: str) -> Attempt:
        """登记一次「即将调用真实抽奖」的尝试。

        任何未结算（IN_FLIGHT / UNKNOWN）的旧请求都会冻结该操作。
        """

        unresolved = self.unresolved_attempts()
        if unresolved:
            ids = "、".join(a.attempt_id for a in unresolved)
            raise ReconcileRequired(
                f"存在未结算的抽奖请求（{ids}），请先核实结果再决定是否继续，禁止盲目重试"
            )

        auth = self.get_authorization(auth_id)
        if auth.revoked:
            raise AuthorizationRequired("该授权已被撤销")
        if auth.remaining <= 0:
            raise DrawLimitExceeded("本次授权的抽奖次数已用完，需要重新确认")

        today_attempts = self.attempts_today()
        if len(today_attempts) >= self.max_draws_per_day:
            raise DrawLimitExceeded(
                f"今日抽奖次数已达上限 {self.max_draws_per_day} 次，请明日再试"
            )

        now = self._clock()
        attempt_id = _new_id("draw")
        self._append(
            {
                "type": "attempt",
                "attempt_id": attempt_id,
                "auth_id": auth_id,
                "activity_code": auth.activity_code,
                "cost": None if auth.cost_per_draw is None else str(auth.cost_per_draw),
                "status": STATUS_IN_FLIGHT,
                "created_at": now.isoformat(timespec="seconds"),
            }
        )
        return self._attempts[attempt_id]

    @ledger_transaction
    def settle(
        self,
        attempt_id: str,
        *,
        result: Optional[DrawResult] = None,
        status: Optional[str] = None,
        message: str = "",
        win: Optional[bool] = None,
    ) -> Attempt:
        """结算一次尝试。

        * 明确成功/失败 → 正常结算；
        * 超时、断连、无状态码 → 记为 UNKNOWN，并冻结后续抽奖直到对账。
        """

        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise UnknownReference(f"未找到抽奖请求：{attempt_id}")

        if attempt.status != STATUS_IN_FLIGHT:
            raise GuardError("该请求已结算；未知结果只能通过有依据的对账处理")

        if result is not None:
            if result.state_unknown:
                status = STATUS_UNKNOWN
            elif result.success:
                status = STATUS_SUCCESS
            else:
                status = STATUS_FAILED
            message = message or result.message
            win = result.win if win is None else win

        status = status or STATUS_UNKNOWN
        if status not in {STATUS_SUCCESS, STATUS_FAILED, STATUS_UNKNOWN}:
            raise GuardError(f"非法结算状态：{status}")

        now = self._clock()
        self._append(
            {
                "type": "settle",
                "attempt_id": attempt_id,
                "status": status,
                "win": win,
                "message": message,
                "settled_at": now.isoformat(timespec="seconds"),
            }
        )
        return self._attempts[attempt_id]

    @ledger_transaction
    def reconcile(self, attempt_id: str, status: str, note: str = "", *, verified: bool = False) -> Attempt:
        """人工/查询核实后解除冻结。"""

        attempt = self._attempts.get(attempt_id)
        if attempt is None:
            raise UnknownReference(f"未找到抽奖请求：{attempt_id}")
        if not attempt.unresolved:
            raise GuardError("该请求已完成结算，不能覆盖结果")
        if not verified or not note.strip():
            raise GuardError("需确认可关联原请求的官方结果并记录依据；无新增奖品不构成失败证据")
        if status not in SETTLED:
            raise GuardError("对账结果只能是 SUCCESS 或 FAILED")
        self._append(
            {
                "type": "reconcile",
                "attempt_id": attempt_id,
                "status": status,
                "note": note,
                "settled_at": self._clock().isoformat(timespec="seconds"),
            }
        )
        return self._attempts[attempt_id]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now()


def _dec(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - 台账损坏时不应阻断流程
        return None
