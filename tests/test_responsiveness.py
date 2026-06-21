"""우회/변명/비답변 검증 + 질문 난이도 가드 — 결정적 룰."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.plan import (
    EvasionVerifier,
    QuestionEaseVerifier,
    answer_responsive,
    check_evasion,
    question_ease,
)

_DOD = DoD(spec="x", frozen=True)


# ── 사용자 비답변(결정 회피) ─────────────────────────────────────────────────────
def test_non_answer_detected() -> None:
    for bad in ["몰라요", "아무거나 알아서", "글쎄요", "딱히 상관없어요", "idk", "whatever"]:
        assert check_evasion(bad, mode="user").is_evasive, bad
        assert not answer_responsive(bad)


def test_real_decision_passes() -> None:
    for good in ["문의하기", "예약 버튼", "stripe.com 처럼", "파란색 톤"]:
        assert not check_evasion(good, mode="user").is_evasive, good
        assert answer_responsive(good)


def test_empty_is_evasive() -> None:
    assert check_evasion("   ", mode="user").is_evasive


# ── 모델 변명/회피(완료 주장에 No-Pass) ──────────────────────────────────────────
def test_model_excuse_detected() -> None:
    v = EvasionVerifier()
    for excuse in [
        "테스트 실패는 제 변경 때문이 아닙니다.",
        "이건 알려진 제한입니다.",
        "일단 구현했습니다 (추후 보완).",
        "계속할까요?",
    ]:
        r = v.verify(Claim("done", excuse, kind="completion"), _DOD)
        assert r.verdict is Verdict.FAIL, excuse


def test_direct_completion_passes() -> None:
    v = EvasionVerifier()
    r = v.verify(Claim("done", "문의폼 구현, 전송·검증·스팸차단 테스트 green.", kind="completion"), _DOD)
    assert r.verdict is Verdict.PASS


# ── 질문 난이도 가드 ────────────────────────────────────────────────────────────
def test_easy_questions_pass() -> None:
    assert question_ease("좋아하는 색을 한 단어로 (예: 파랑·초록)").easy
    assert question_ease("히어로 / 기능 / CTA 중 하나 고르세요").easy  # 전문용어지만 선택지 동반


def test_hard_questions_flagged() -> None:
    # 전문용어인데 선택지·예시 없음 → 어려움.
    assert not question_ease("LCP와 CLS 목표치를 정해 주세요").easy
    # 복합 질문(한 번에 둘) → 어려움.
    assert not question_ease("색은 뭘로 할까요? 그리고 폰트는 어떤 걸로 할까요?").easy
    # 장황.
    assert not question_ease("이 " + "아주 " * 40 + "긴 질문").easy


def test_question_ease_verifier_contract() -> None:
    v = QuestionEaseVerifier()
    assert v.verify(Claim("q", "색을 한 단어로 (예: 파랑)", kind="question"), _DOD).verdict is Verdict.PASS
    assert v.verify(Claim("q", "퍼널 전환 구조를 설계해 주세요", kind="question"), _DOD).verdict is Verdict.FAIL
