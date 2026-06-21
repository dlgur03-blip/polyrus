"""빌드 위임 실연 — plan.to_task() → Session.run이 히어로 카피를 결정적 검증(No-Pass).

'빌딩은 위임, 검증은 우리'(마누스 회피). 빌더(모델)가 카피를 쓰고, 홈페이지 뱅크(AI-slop 등)가
production-grade로 잠근다. 깨끗하면 검증완료, AI 티면 *조용히 통과 안 하고* 에스컬레이션.
"""
from __future__ import annotations

from polyrus.planner import ProactivePlanner, ScriptedAnswers
from polyrus.session import Session
from polyrus.skeleton import HOMEPAGE
from polyrus.types import Termination


class FakeModel:
    """고정 응답 모델(ModelClient 계약). 빌더 산출을 흉내."""

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0) -> str:
        return self.reply


def _plan_task():
    ans = ScriptedAnswers(answers={
        "goal_action": "문의하기", "references": "stripe.com", "tone_guide": "정돈된",
        "accent_section": "히어로", "palette": "신뢰", "features": "문의폼",
    })
    return ProactivePlanner(HOMEPAGE).run(ans).to_task("build")


def test_clean_copy_verifies_complete() -> None:
    # 사람 냄새 나는 구체 카피 → AI-slop 없음 → 검증 완료.
    model = FakeModel("두 줄짜리 메모로 시작한 가게입니다. 궁금한 건 편하게 물어보세요.")
    result = Session.for_homepage_build(model, preflight_tools=[]).run(_plan_task())
    assert result.termination is Termination.VERIFIED_COMPLETE
    assert all(i.closed for i in result.items)
    assert result.weighted_confidence > 0


def test_ai_slop_copy_does_not_pass_silently() -> None:
    # AI 티 가득한 카피 → 결정적 검증 FAIL → 재시도/막힘 → 에스컬레이션(조용한 패스 없음).
    model = FakeModel("혁신적인 올인원 솔루션으로 고객님의 니즈를 충족합니다! 🚀✨ unlock the power!")
    result = Session.for_homepage_build(model, preflight_tools=[]).run(_plan_task())
    assert result.termination is not Termination.VERIFIED_COMPLETE
    assert not any(i.closed for i in result.items)
    assert result.items[0].escalated  # 포기 대신 명시적 에스컬레이션(No-Silent-Stop)


def test_build_uses_homepage_bank_not_code() -> None:
    # 빌드 산출이 'copy' kind로 생성되고 홈페이지 뱅크가 적용됐는지(코드 뱅크 아님).
    model = FakeModel("작은 동네 빵집입니다. 오늘 구운 빵을 보러 오세요.")
    sess = Session.for_homepage_build(model, preflight_tools=[])
    assert sess.arms_kind == "copy"
    result = sess.run(_plan_task())
    # 코퍼스에 카피 검증 기록(ai_slop)이 남는다.
    tiers = {r.tier for r in result.corpus_records}
    assert tiers  # 검증이 실제로 돌았다
