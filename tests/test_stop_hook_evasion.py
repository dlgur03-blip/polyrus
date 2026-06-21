"""stop-hook 변명 게이트 — 래핑된 모델이 *둘러대며 완료 선언*하면 막는다(No-Pass on 주장).

글로벌 CLAUDE.md '회피 금지'의 집행면. 코드 검증과 *독립* — 변명 자체가 실패다.
"""
from __future__ import annotations

from polyrus.adapters.claude_code.auto import AutoStopDecider, last_assistant_response
from polyrus.types import Termination
from polyrus.verifiers.plan import completion_excuse


class _Model:
    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0) -> str:
        return "def test(): assert True"


def _decider(response: str, *, code=None) -> AutoStopDecider:
    return AutoStopDecider(
        _Model(),
        get_request=lambda _p: "함수 구현해줘",          # 코드 의도 있음
        get_response=lambda _p: response,
        get_code=lambda _cwd: code,                       # 코드 산출물 유무
    )


# ── 강신호 변명 → block (코드 유무 무관) ───────────────────────────────────────────
def test_excuse_blocks_even_without_code() -> None:
    d = _decider("테스트가 실패하지만 제 변경 때문이 아닙니다. 알려진 제한입니다.")
    r = d.decide({}, continues=0)
    assert r.block is True
    assert "변명" in r.reason or "회피" in r.reason


def test_should_continue_blocks() -> None:
    r = _decider("일부 구현했습니다. 계속할까요?").decide({}, continues=0)
    assert r.block is True


def test_excuse_budget_escalates() -> None:
    d = _decider("이건 알려진 제한입니다.")
    r = d.decide({}, continues=3)  # continue 예산 소진 → 강제 stop + 에스컬레이션(조용한 패스 아님)
    assert r.block is False and r.termination is Termination.BUDGET_ESCALATED


# ── 오탐 방지: 흔한 말('일단')·정상 완료는 통과 ───────────────────────────────────
def test_benign_completion_not_flagged() -> None:
    # 코드 산출물 없음 + 변명 아님 → 통과(침묵), block 아님.
    r = _decider("일단 함수를 구현했고 테스트도 통과합니다.", code=None).decide({}, continues=0)
    assert r.block is False


def test_empty_response_not_evasive() -> None:
    assert not completion_excuse("").is_evasive  # 부재를 변명으로 막지 않는다


def test_strict_ignores_weak_signals() -> None:
    # strict(stop-hook): '일단/추후'는 무시, 강신호만.
    assert not completion_excuse("일단 추후에 보완하겠습니다").is_evasive
    assert completion_excuse("제 변경 때문이 아닙니다").is_evasive


def test_last_assistant_response_from_payload() -> None:
    assert last_assistant_response({"response": "끝났습니다"}) == "끝났습니다"
