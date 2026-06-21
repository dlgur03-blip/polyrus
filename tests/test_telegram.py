"""텔레그램 알림 + 원탭 승인/거부 — 네트워크 0(주입식 transport)으로 결정 로직을 실측.

실제 전송은 토큰이 있을 때만. 여기선 가짜 transport로 sendMessage/getUpdates/콜백을 시뮬레이트.
"""
from __future__ import annotations

import json

from polyrus.gates import EvidenceGate
from polyrus.notify.telegram import (
    TelegramClient,
    TelegramConfig,
    TelegramError,
    approval_gate_fn,
    escalation_sink,
    redact,
)
from polyrus.types import DoD, LedgerItem, RiskLevel


class FakeTelegram:
    """가짜 transport. sendMessage의 버튼에서 rid를 캡쳐해 getUpdates로 콜백을 돌려준다."""

    def __init__(self, decision: str | None = "approve", *, fail_send: bool = False) -> None:
        self.decision = decision  # "approve" | "reject" | None(=타임아웃)
        self.fail_send = fail_send
        self.sent: list[dict] = []
        self.answered: list[dict] = []
        self._rid: str | None = None
        self._delivered = False

    def __call__(self, method: str, params: dict) -> dict:
        if method == "sendMessage":
            self.sent.append(params)
            if self.fail_send:
                return {"ok": False, "description": "Bad Request"}
            rm = params.get("reply_markup")
            if rm:
                kb = json.loads(rm)["inline_keyboard"][0]
                self._rid = kb[0]["callback_data"].split(":")[1]
            return {"ok": True, "result": {"message_id": len(self.sent)}}
        if method == "getUpdates":
            if self.decision is None or self._rid is None or self._delivered:
                return {"result": []}
            self._delivered = True
            return {"result": [{
                "update_id": 1,
                "callback_query": {"id": "cq1", "data": f"plyrs:{self._rid}:{self.decision}"},
            }]}
        if method == "answerCallbackQuery":
            self.answered.append(params)
            return {"ok": True}
        return {"ok": True, "result": {}}


def _client(fake: FakeTelegram, **kw) -> TelegramClient:
    return TelegramClient(
        TelegramConfig(token="T", chat_id="C"),
        transport=fake,
        clock=lambda: 0.0,        # 상수 시계: approve/reject는 첫 폴에서 결정
        sleep=lambda _s: None,
        **kw,
    )


# ── 전송 ──────────────────────────────────────────────────────────────────────
def test_send_message_returns_id() -> None:
    fake = FakeTelegram()
    mid = _client(fake).send_message("안녕")
    assert mid == 1
    assert fake.sent[0]["chat_id"] == "C" and fake.sent[0]["text"] == "안녕"


def test_send_message_raises_on_not_ok() -> None:
    try:
        _client(FakeTelegram(fail_send=True)).send_message("x")
        raised = False
    except TelegramError:
        raised = True
    assert raised


# ── 승인/거부/타임아웃 ─────────────────────────────────────────────────────────
def test_ask_approval_approve() -> None:
    fake = FakeTelegram(decision="approve")
    assert _client(fake).ask_approval("되돌릴 수 없는 행동 승인?") is True
    assert fake.answered  # 콜백 ack 됨


def test_ask_approval_reject() -> None:
    assert _client(FakeTelegram(decision="reject")).ask_approval("승인?") is False


def test_ask_approval_timeout() -> None:
    fake = FakeTelegram(decision=None)  # 아무도 안 누름
    # 증가하는 시계로 deadline 초과를 강제.
    ticks = iter([0.0, 100.0, 200.0, 300.0, 400.0])
    client = TelegramClient(
        TelegramConfig("T", "C"), transport=fake,
        clock=lambda: next(ticks), sleep=lambda _s: None,
    )
    assert client.ask_approval("승인?", timeout_s=5.0) is None


# ── 리댁션(6.3) ────────────────────────────────────────────────────────────────
def test_redact_masks_token() -> None:
    assert "SECRET" not in redact("error with token SECRET in url", "SECRET")


def test_from_env_absent(monkeypatch) -> None:
    monkeypatch.delenv("POLYRUS_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("POLYRUS_TELEGRAM_CHAT_ID", raising=False)
    assert TelegramConfig.from_env() is None
    assert TelegramClient.from_env() is None


def test_from_env_present(monkeypatch) -> None:
    monkeypatch.setenv("POLYRUS_TELEGRAM_TOKEN", "tok")
    monkeypatch.setenv("POLYRUS_TELEGRAM_CHAT_ID", "123")
    cfg = TelegramConfig.from_env()
    assert cfg is not None and cfg.chat_id == "123"


# ── 통합: 게이트(6.1) + 에스컬레이션 싱크(M3) ──────────────────────────────────
def test_evidence_gate_with_telegram_approval() -> None:
    fake = FakeTelegram(decision="approve")
    gate = EvidenceGate(ask_fn=approval_gate_fn(_client(fake)))
    item = LedgerItem(id="i", goal="g", dod=DoD(spec="x", frozen=True), risk=RiskLevel.HIGH)
    decision = gate.should_run_verification(item, est_cost="$0.40")
    assert decision.proceed is True  # 승인됨


def test_evidence_gate_telegram_reject_blocks() -> None:
    fake = FakeTelegram(decision="reject")
    gate = EvidenceGate(ask_fn=approval_gate_fn(_client(fake)))
    item = LedgerItem(id="i", goal="g", dod=DoD(spec="x", frozen=True), risk=RiskLevel.HIGH)
    assert gate.should_run_verification(item, est_cost="$0.40").proceed is False


def test_escalation_sink_sends() -> None:
    fake = FakeTelegram()
    sink = escalation_sink(_client(fake))
    sink(object(), "항목 'X' 미완료. 블로커: 막힘")
    assert fake.sent and "에스컬레이션" in fake.sent[0]["text"]
