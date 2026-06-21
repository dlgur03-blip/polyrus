"""검증된 *행동* 실행 — 멱등성 + 되돌릴 수 없는 행동 격리(6.4) + 감사.

핵심 빈칸(v1): No-Pass 루프는 *재시도*한다. 부수효과 행동(메일·결제·전송)을 재시도하면 중복 실행.
멱등키로 같은 행동을 두 번 실행하지 않는다(Swytchcode식 'execution authority'의 핵심 보증).

비대칭(6.1/6.2): 되돌릴 수 있는 행동은 자동 실행, 되돌릴 수 없는 행동은 게이트(승인) 또는
'확인 트레이'에 모았다가 끝에 한 번에. 모든 실행은 감사 로그(store)에 남는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class ActionLog(Protocol):
    """멱등성/감사 백엔드. Store가 구현(SQLite 영속) 또는 인메모리."""

    def action_seen(self, key: str) -> bool: ...
    def action_record(self, key: str, kind: str, status: str) -> None: ...


class InMemoryActionLog:
    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def action_seen(self, key: str) -> bool:
        return key in self._seen

    def action_record(self, key: str, kind: str, status: str) -> None:
        self._seen[key] = status


@dataclass
class Action:
    """부수효과 행동 한 건. key=멱등키(같으면 한 번만), reversible=되돌릴 수 있나."""

    key: str
    kind: str                  # "email" | "payment" | "http_write" | ...
    reversible: bool
    run: Callable[[], Any]
    description: str = ""


@dataclass
class ActionResult:
    key: str
    status: str                # executed | skipped_idempotent | deferred | rejected
    value: Any = None


class ActionExecutor:
    """멱등 실행기. approve=되돌릴 수 없는 행동의 승인 게이트(예: 텔레그램). None이면 트레이로 보류."""

    def __init__(self, log: ActionLog | None = None, *, approve: Callable[[Action], bool] | None = None) -> None:
        self.log = log or InMemoryActionLog()
        self.approve = approve
        self.tray: list[Action] = []  # 승인 대기(되돌릴 수 없는 행동)

    def execute(self, action: Action) -> ActionResult:
        # 1) 멱등: 이미 실행한 키면 재실행 금지(재시도 안전).
        if self.log.action_seen(action.key):
            return ActionResult(action.key, "skipped_idempotent")

        # 2) 되돌릴 수 없는 행동은 게이트. 승인자 없으면 트레이로 보류(6.4).
        if not action.reversible:
            if self.approve is None:
                self.tray.append(action)
                return ActionResult(action.key, "deferred")
            if not self.approve(action):
                self.log.action_record(action.key, action.kind, "rejected")
                return ActionResult(action.key, "rejected")

        # 3) 실행 + 감사 기록.
        value = action.run()
        self.log.action_record(action.key, action.kind, "executed")
        return ActionResult(action.key, "executed", value)

    def approve_tray(self, approve: Callable[[Action], bool] | None = None) -> list[ActionResult]:
        """확인 트레이: 쌓인 되돌릴 수 없는 행동을 끝에 한 번에 승인/실행.
        '몇 시간 자율이되 몇 시간 리스크가 아니게'(6.4)."""
        gate = approve or self.approve or (lambda _a: True)
        results: list[ActionResult] = []
        pending, self.tray = self.tray, []
        for action in pending:
            if self.log.action_seen(action.key):
                results.append(ActionResult(action.key, "skipped_idempotent"))
                continue
            if not gate(action):
                self.log.action_record(action.key, action.kind, "rejected")
                results.append(ActionResult(action.key, "rejected"))
                continue
            value = action.run()
            self.log.action_record(action.key, action.kind, "executed")
            results.append(ActionResult(action.key, "executed", value))
        return results
