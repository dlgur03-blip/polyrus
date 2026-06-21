"""검증된 행동 실행 — 멱등성(재시도 안전) + 되돌릴 수 없는 행동 격리 + 감사."""
from __future__ import annotations

from polyrus.actions import Action, ActionExecutor, InMemoryActionLog
from polyrus.store import Store


def _counter():
    box = {"n": 0}

    def run():
        box["n"] += 1
        return box["n"]

    return box, run


# ── 멱등성: 같은 키는 한 번만 (No-Pass 재시도 안전) ─────────────────────────────
def test_idempotent_skips_second_execution() -> None:
    box, run = _counter()
    ex = ActionExecutor()
    a = Action(key="send-email-42", kind="email", reversible=True, run=run)
    r1 = ex.execute(a)
    r2 = ex.execute(a)  # 재시도/중복 호출
    assert r1.status == "executed" and r2.status == "skipped_idempotent"
    assert box["n"] == 1  # 부수효과는 단 한 번


def test_reversible_auto_executes() -> None:
    box, run = _counter()
    r = ActionExecutor().execute(Action("k", "http_write", reversible=True, run=run))
    assert r.status == "executed" and box["n"] == 1


# ── 되돌릴 수 없는 행동 격리 ────────────────────────────────────────────────────
def test_irreversible_without_approver_defers_to_tray() -> None:
    box, run = _counter()
    ex = ActionExecutor()  # 승인자 없음
    r = ex.execute(Action("pay-1", "payment", reversible=False, run=run))
    assert r.status == "deferred"
    assert box["n"] == 0 and len(ex.tray) == 1  # 실행 안 됨, 트레이에 쌓임


def test_irreversible_approved_executes() -> None:
    box, run = _counter()
    ex = ActionExecutor(approve=lambda a: True)
    r = ex.execute(Action("pay-2", "payment", reversible=False, run=run))
    assert r.status == "executed" and box["n"] == 1


def test_irreversible_rejected_does_not_execute() -> None:
    box, run = _counter()
    ex = ActionExecutor(approve=lambda a: False)
    r = ex.execute(Action("pay-3", "payment", reversible=False, run=run))
    assert r.status == "rejected" and box["n"] == 0


# ── 확인 트레이: 끝에 한 번에 승인 (6.4) ────────────────────────────────────────
def test_approve_tray_executes_deferred() -> None:
    box, run = _counter()
    ex = ActionExecutor()
    ex.execute(Action("a", "email", reversible=False, run=run))
    ex.execute(Action("b", "email", reversible=False, run=run))
    assert box["n"] == 0  # 둘 다 보류
    results = ex.approve_tray(approve=lambda a: True)
    assert box["n"] == 2 and all(r.status == "executed" for r in results)
    assert ex.tray == []  # 비워짐


def test_approve_tray_can_reject() -> None:
    box, run = _counter()
    ex = ActionExecutor()
    ex.execute(Action("c", "payment", reversible=False, run=run))
    results = ex.approve_tray(approve=lambda a: a.kind != "payment")  # 결제는 거부
    assert results[0].status == "rejected" and box["n"] == 0


# ── Store 백엔드: 멱등성/감사 영속 ──────────────────────────────────────────────
def test_store_backed_idempotency_persists(tmp_path) -> None:
    box, run = _counter()
    db = str(tmp_path / "a.db")
    with Store(db) as s:
        ActionExecutor(s).execute(Action("once", "email", reversible=True, run=run))
    # 프로세스가 죽었다 살아나도(=DB 재오픈) 같은 키는 재실행 안 됨.
    with Store(db) as s2:
        r = ActionExecutor(s2).execute(Action("once", "email", reversible=True, run=run))
    assert r.status == "skipped_idempotent" and box["n"] == 1


def test_inmemory_log_records_status() -> None:
    log = InMemoryActionLog()
    ActionExecutor(log).execute(Action("x", "email", reversible=True, run=lambda: None))
    assert log.action_seen("x")


# ── 승인 게이트는 텔레그램 등으로 교체 가능(플러그형) ────────────────────────────
def test_approver_is_pluggable() -> None:
    # approve(action)->bool 이면 무엇이든(텔레그램 ask_approval 등) 끼울 수 있다.
    seen: list[str] = []
    ex = ActionExecutor(approve=lambda a: seen.append(a.description) or True)
    ex.execute(Action("k", "payment", reversible=False, run=lambda: 1, description="$5 환불"))
    assert seen == ["$5 환불"]
