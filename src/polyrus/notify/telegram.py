"""텔레그램 알림 + 원탭 승인/거부 — 6.4 확인 트레이 / 6.1 증거조사 게이트의 폰 채널.

Claude Code가 자율로 돌다 막히거나(stuck) 예산 소진되면 폰으로 핑 → 사람이 버튼으로 결정.
되돌릴 수 없는 행동·비싼 검증 패스 승인도 여기로(6.1).

자격증명(6.3): 토큰은 코드/로그/원장/모델 컨텍스트에 절대 안 들어간다. *도구 실행 경계*에서
env로만 읽고(`POLYRUS_TELEGRAM_TOKEN`/`POLYRUS_TELEGRAM_CHAT_ID`), 에러 메시지는 리댁션한다.
(전체 키체인 통합은 secrets.py Phase 1.)

전송은 주입식 transport라 테스트는 네트워크 0. 실제 전송은 토큰이 있을 때만 일어난다.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Callable

_API = "https://api.telegram.org"

# (method, params) -> 파싱된 JSON 응답. 실제 HTTP를 가린다(테스트 주입용).
Transport = Callable[[str, dict[str, object]], dict]


class TelegramError(RuntimeError):
    """텔레그램 호출 실패 (메시지는 토큰 리댁션 후)."""


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str

    @classmethod
    def from_env(cls) -> TelegramConfig | None:
        tok = os.environ.get("POLYRUS_TELEGRAM_TOKEN")
        chat = os.environ.get("POLYRUS_TELEGRAM_CHAT_ID")
        if not tok or not chat:
            return None
        return cls(token=tok, chat_id=chat)

    @classmethod
    def from_resolver(cls, resolver: object, *, identity: str = "default") -> TelegramConfig | None:
        """통합 자격증명 경로(6.3): 글로벌 계정 → 키체인/env에서 해석 + 자동 리댁션."""
        try:
            return cls(
                token=resolver.get("telegram", identity, "token"),       # type: ignore[attr-defined]
                chat_id=resolver.get("telegram", identity, "chat_id"),   # type: ignore[attr-defined]
            )
        except (KeyError, AttributeError):
            return None


def redact(text: str, token: str) -> str:
    """토큰 문자열을 마스킹 (로그·에러로 새지 않게)."""
    return text.replace(token, "<redacted>") if token else text


class TelegramClient:
    def __init__(
        self,
        config: TelegramConfig,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 1.0,
        http_timeout: float = 10.0,
    ) -> None:
        self.cfg = config
        self._transport = transport or self._http
        self.clock = clock
        self.sleep = sleep
        self.poll_interval = poll_interval
        self.http_timeout = http_timeout

    @classmethod
    def from_env(cls, **kw: object) -> TelegramClient | None:
        cfg = TelegramConfig.from_env()
        return cls(cfg, **kw) if cfg else None  # type: ignore[arg-type]

    # ── 공개 API ──────────────────────────────────────────────────────────────
    def send_message(self, text: str, *, reply_markup: dict | None = None) -> int | None:
        params: dict[str, object] = {"chat_id": self.cfg.chat_id, "text": text}
        if reply_markup is not None:
            params["reply_markup"] = json.dumps(reply_markup)
        resp = self._transport("sendMessage", params)
        if not resp.get("ok"):
            raise TelegramError(f"sendMessage 실패: {resp.get('description')}")
        result = resp.get("result") or {}
        return result.get("message_id")

    def ask_approval(self, text: str, *, timeout_s: float = 300.0) -> bool | None:
        """승인/거부 버튼을 보내고 콜백을 롱폴링. True=승인 / False=거부 / None=타임아웃."""
        rid = uuid.uuid4().hex[:8]
        prefix = f"plyrs:{rid}:"
        markup = {
            "inline_keyboard": [[
                {"text": "✅ 승인", "callback_data": prefix + "approve"},
                {"text": "❌ 거부", "callback_data": prefix + "reject"},
            ]]
        }
        self.send_message(text, reply_markup=markup)

        deadline = self.clock() + timeout_s
        offset: int | None = None
        while self.clock() < deadline:
            resp = self._transport("getUpdates", self._drop_none({"offset": offset, "timeout": 0}))
            for upd in resp.get("result", []):
                offset = int(upd["update_id"]) + 1
                cq = upd.get("callback_query")
                if cq and str(cq.get("data", "")).startswith(prefix):
                    self._safe_answer(cq.get("id"))
                    return str(cq["data"])[len(prefix):] == "approve"
            self.sleep(self.poll_interval)
        return None

    # ── 내부 ──────────────────────────────────────────────────────────────────
    def _safe_answer(self, cq_id: object) -> None:
        try:
            self._transport("answerCallbackQuery", {"callback_query_id": cq_id})
        except TelegramError:
            pass  # 콜백 ack 실패는 결정에 영향 없음

    def _http(self, method: str, params: dict[str, object]) -> dict:
        url = f"{_API}/bot{self.cfg.token}/{method}"
        data = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}).encode()
        try:
            req = urllib.request.Request(url, data=data)  # noqa: S310 - https 고정 URL
            with urllib.request.urlopen(req, timeout=self.http_timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode())
        except Exception as e:  # noqa: BLE001 - 모든 실패를 토큰 리댁션 후 단일 예외로
            raise TelegramError(redact(str(e), self.cfg.token)) from None

    @staticmethod
    def _drop_none(d: dict[str, object]) -> dict[str, object]:
        return {k: v for k, v in d.items() if v is not None}


# ── 통합 헬퍼 ─────────────────────────────────────────────────────────────────
def escalation_sink(client: TelegramClient) -> Callable[[object, str], None]:
    """Escalator(sink=...)에 끼우는 알림 싱크. M3 에스컬레이션을 폰으로 보낸다."""
    def _sink(item: object, msg: str) -> None:
        client.send_message(f"🛑 [Polyrus 에스컬레이션]\n{msg}")
    return _sink


def approval_gate_fn(client: TelegramClient, *, timeout_s: float = 300.0) -> Callable[[str], bool]:
    """EvidenceGate(ask_fn=...)에 끼우는 승인 함수. 타임아웃/거부는 '진행 안 함'(안전 기본값)."""
    def _ask(question: str) -> bool:
        return bool(client.ask_approval(question, timeout_s=timeout_s))
    return _ask
