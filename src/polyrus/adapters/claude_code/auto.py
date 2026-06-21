"""무설정 Stop-hook — task.json 없이, 대화에서 자동으로.

UX 원칙(트라이얼에서 배운 것):
  - 무설정: 사용자가 시킨 걸 transcript에서 읽어 합격 기준을 *LLM이 자동 생성*(DoDGenerator).
  - 침묵: 일반 대화("하이")·검증 불가·통과는 *아무 말 안 함*(끼어들지 않음).
  - 사람 말: 실패는 pytest 덤프가 아니라 humanize() 한 줄.
  - 옵트인/보수적: 기준을 못 만들면 막지 않는다(잔소리 금지).

완전 인터랙티브 '원탭 확인'은 UI 영역 — 훅은 일방향이라 *무엇을 검증했는지 투명하게 보여주는* 선까지.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from polyrus.adapters.claude_code.stop_hook import HookResult
from polyrus.dod import DoDGenerator
from polyrus.humanize import humanize
from polyrus.models import ModelClient
from polyrus.types import Claim, Termination
from polyrus.verifiers.registry import VerifierBank, default_code_bank

# 코드 작업 의도 신호(한/영). 일반 대화는 여기 안 걸려 → 침묵.
_CODE_INTENT = re.compile(
    r"구현|만들|짜줘|짜라|작성|고쳐|수정|버그|리팩터|함수|클래스|메서드|코드|테스트|"
    r"implement|fix|refactor|write\s+(a\s+)?(function|code|class)|debug",
    re.IGNORECASE,
)


def read_transcript(path: str | Path) -> list[tuple[str, str]]:
    """Claude Code transcript(JSONL) → [(role, text)]. 스키마에 관대하게."""
    out: list[tuple[str, str]] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = obj.get("message", obj)
        role = msg.get("role") or obj.get("type") or ""
        out.append((role, _text_of(msg.get("content"))))
    return out


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def last_user_request(payload: dict[str, Any]) -> str:
    tp = payload.get("transcript_path")
    if tp:
        for role, text in reversed(read_transcript(tp)):
            if role == "user" and text.strip():
                return text.strip()
    return str(payload.get("prompt", "")).strip()


def last_assistant_response(payload: dict[str, Any]) -> str:
    """모델의 마지막 응답 텍스트(완료 선언) — 변명/회피 검사 대상."""
    tp = payload.get("transcript_path")
    if tp:
        for role, text in reversed(read_transcript(tp)):
            if role in ("assistant", "model") and text.strip():
                return text.strip()
    return str(payload.get("response", "")).strip()


def has_code_intent(request: str) -> bool:
    return bool(_CODE_INTENT.search(request))


def find_recent_py(cwd: str | Path) -> tuple[str, str] | None:
    """cwd에서 가장 최근 수정된 .py(테스트·숨김·venv 제외)를 산출물로 본다."""
    root = Path(cwd)
    cands = [
        f for f in root.glob("*.py")
        if not f.name.startswith("test_") and not f.name.startswith("_")
    ]
    if not cands:
        return None
    newest = max(cands, key=lambda f: f.stat().st_mtime)
    return newest.name, newest.read_text(encoding="utf-8")


class AutoStopDecider:
    """무설정 결정기. 컴포넌트는 주입 가능(테스트 네트워크/파일 0)."""

    def __init__(
        self,
        model: ModelClient,
        bank: VerifierBank | None = None,
        *,
        get_request: Callable[[dict], str] = last_user_request,
        get_code: Callable[[str], "tuple[str, str] | None"] = find_recent_py,
        get_response: Callable[[dict], str] = last_assistant_response,
        max_continues: int = 3,
    ) -> None:
        self.model = model
        self.bank = bank or default_code_bank()
        self.get_request = get_request
        self.get_code = get_code
        self.get_response = get_response
        self.max_continues = max_continues

    def decide(self, payload: dict[str, Any], continues: int = 0) -> HookResult:
        request = self.get_request(payload)
        # ① 일반 대화·비코드 → 침묵(끼어들지 않음).
        if not request or not has_code_intent(request):
            return self._allow("일반 대화 — 검증 안 함")

        # ② 변명/회피 게이트: 작업 완료를 선언하며 *둘러대면* 코드 검증과 무관하게 막는다.
        #    (글로벌 CLAUDE.md '회피 금지'의 집행 — '알려진 제한/내 변경 탓 아님' 등 강신호만, 오탐 방지.)
        from polyrus.verifiers.plan import completion_excuse

        response = self.get_response(payload)
        ev = completion_excuse(response)
        if ev.is_evasive:
            reason = (
                "Polyrus: 변명/회피로 끝내지 마라. 완료 선언 금지 — 실제로 해결하라.\n"
                + "\n".join(f"  ⚠ {f}" for f in ev.flags)
            )
            if continues >= self.max_continues:
                return HookResult(False, "여러 번 둘러댐 — 사람 확인 필요:\n" + reason,
                                  Termination.BUDGET_ESCALATED)
            return HookResult(True, reason, None)  # block → 변명 말고 이어서 해결

        code = self.get_code(payload.get("cwd", "."))
        if code is None:
            return self._allow("코드 산출물 없음 — 통과")
        name, source = code

        # ② 요청에서 합격 기준 자동 생성(무설정) + 검증.
        #    안전: 어떤 실패(LLM·검증)도 사용자 세션을 막지 않는다 → 통과(잔소리 금지).
        try:
            dod = DoDGenerator(model=self.model).derive_dod(request)
            if not dod.acceptance_tests:
                return self._allow("자동 기준 생성 실패 — 통과")
            verdict = self.bank.run(Claim("auto", source, kind="code", meta={"module": name}), dod)
        except Exception:  # noqa: BLE001 - 절대 사용자 흐름을 깨지 않는다
            return self._allow("검증 불가(오류) — 통과")

        if verdict.passed:
            return self._allow("검증 통과 — 조용히 완료")

        # ③ 실패 → 사람 말 한 줄 + 무엇을 검증했는지 투명하게.
        reason = (
            "Polyrus가 검증했는데 아직 안 맞아요:\n"
            f"  ❌ {humanize(verdict.blocker)}\n"
            f"  (검증 기준: ‘{request[:50]}’에서 자동 생성한 수용 테스트)"
        )
        if continues >= self.max_continues:
            return HookResult(False, "여러 번 시도했지만 미해결 — 사람 확인이 필요해요:\n" + reason,
                              Termination.BUDGET_ESCALATED)
        return HookResult(True, reason, None)  # block → Claude가 이어서 고침

    @staticmethod
    def _allow(note: str) -> HookResult:
        return HookResult(False, note, Termination.VERIFIED_COMPLETE)


def _resolve_model() -> ModelClient:
    """무설정 훅의 LLM. API 키 있으면 Anthropic, 없으면 계정 기반 claude CLI."""
    import os

    from polyrus.models import AnthropicModel, claude_cli

    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicModel()
    return claude_cli()


def auto_main(argv: list[str] | None = None) -> int:  # pragma: no cover - 실제 훅 진입점
    """`polyrus-auto-hook` 진입점 — task.json 없이 대화에서 자동 검증."""
    import sys

    from polyrus.adapters.claude_code.stop_hook import run_hook

    raw = sys.stdin.read()
    payload = json.loads(raw) if raw.strip() else {}
    decider = AutoStopDecider(_resolve_model())
    out = run_hook(payload, decider)  # decide()/HookResult 계약 동일 → 그대로 재사용
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0
