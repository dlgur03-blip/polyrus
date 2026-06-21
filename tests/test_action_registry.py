"""액션 스키마 레지스트리 + 게이트웨이 (v2 기둥 A) — 환각 차단 + 검증된 멱등 실행."""
from __future__ import annotations

from polyrus.action_registry import (
    ActionGateway,
    ActionRegistry,
    ActionSchema,
    Param,
)
from polyrus.actions import ActionExecutor


def _registry(handler=None) -> ActionRegistry:
    r = ActionRegistry()
    r.register(ActionSchema(
        name="send_email", kind="email", reversible=False,
        params=[Param("to", "str"), Param("subject", "str"), Param("cc", "list", required=False)],
        description="이메일 전송", handler=handler,
    ))
    return r


# ── 스키마 검증(환각·파라미터) ──────────────────────────────────────────────────
def test_validate_rejects_hallucinated_action() -> None:
    v = _registry().validate("send_emial", {"to": "a", "subject": "b"})  # 오타=환각
    assert not v.ok and "환각" in v.errors[0]
    assert "send_email" in v.errors[0]  # 가까운 이름 제안


def test_validate_rejects_missing_required() -> None:
    v = _registry().validate("send_email", {"to": "a"})  # subject 누락
    assert not v.ok and any("subject" in e for e in v.errors)


def test_validate_rejects_wrong_type() -> None:
    v = _registry().validate("send_email", {"to": 123, "subject": "b"})  # to는 str이어야
    assert not v.ok and any("타입 불일치" in e for e in v.errors)


def test_validate_rejects_unknown_param() -> None:
    v = _registry().validate("send_email", {"to": "a", "subject": "b", "bcc": "x"})
    assert not v.ok and any("알 수 없는" in e for e in v.errors)


def test_validate_accepts_valid() -> None:
    v = _registry().validate("send_email", {"to": "a@b.c", "subject": "안녕", "cc": ["x@y.z"]})
    assert v.ok and v.errors == []


# ── 게이트웨이: 의도 → 검증 → 멱등 게이트 실행 → 감사 ──────────────────────────
def test_gateway_rejects_invalid_without_executing() -> None:
    calls = []
    gw = ActionGateway(_registry(handler=lambda p: calls.append(p)))
    r = gw.submit("send_email", {"to": "a"}, key="k1")  # subject 누락
    assert r.status == "rejected_invalid"
    assert calls == []  # 검증 실패 → 실행 안 함


def test_gateway_executes_valid_reversible() -> None:
    calls = []
    reg = ActionRegistry()
    reg.register(ActionSchema("log_event", "http_write", reversible=True,
                              params=[Param("msg", "str")], handler=lambda p: calls.append(p["msg"])))
    r = ActionGateway(reg).submit("log_event", {"msg": "hi"}, key="k")
    assert r.status == "executed" and calls == ["hi"]


def test_gateway_idempotent() -> None:
    calls = []
    gw = ActionGateway(_registry(handler=lambda p: calls.append(1)),
                       executor=ActionExecutor(approve=lambda a: True))
    p = {"to": "a", "subject": "b"}
    gw.submit("send_email", p, key="same")
    r2 = gw.submit("send_email", p, key="same")  # 재시도
    assert r2.status == "skipped_idempotent" and len(calls) == 1  # 부수효과 한 번


def test_gateway_irreversible_defers_without_approver() -> None:
    calls = []
    gw = ActionGateway(_registry(handler=lambda p: calls.append(1)))  # 승인자 없음
    r = gw.submit("send_email", {"to": "a", "subject": "b"}, key="k")
    assert r.status == "deferred" and calls == []  # 트레이로 보류
    assert len(gw.executor.tray) == 1


def test_gateway_irreversible_with_approval_executes() -> None:
    calls = []
    gw = ActionGateway(_registry(handler=lambda p: calls.append(1)),
                       executor=ActionExecutor(approve=lambda a: True))  # 텔레그램 등 끼울 자리
    r = gw.submit("send_email", {"to": "a", "subject": "b"}, key="k")
    assert r.status == "executed" and calls == [1]
