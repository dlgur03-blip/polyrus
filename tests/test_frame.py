"""프레임 정규화 + 불변성 검증 — 영합의 생성측 해법. 프레임 의존도를 숫자로."""
from __future__ import annotations

from polyrus.frame import (
    FrameNormalizer,
    analyze_frame,
    check_invariance,
    reframings,
)


class SycophantModel:
    """영합 모델 시뮬레이션 — 프레임에 따라 결론이 뒤집힌다(병목 재현)."""

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        if "결론만" in prompt:  # _stance 질의 — 프레이밍에 휘둘림
            if "당연히 그렇지" in prompt or "맞지?" in prompt and "아니" not in prompt:
                return "예"
            if "불안" in prompt or "아니라고" in prompt:
                return "아니오"
            return "예"
        if "JSON" in prompt:  # analyze_frame
            return '{"truth_bearing": true, "frame": "leading", "neutral": "남자에게 댄디컷이 선호되나?"}'
        return "그럴 수 있죠..."


class InvariantModel:
    """프레임 불변 모델 — 어떻게 묻든 같은 결론."""

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        if "결론만" in prompt:
            return "조건부"  # 프레이밍 무관하게 항상 같은 결론
        if "JSON" in prompt:
            return '{"truth_bearing": true, "frame": "neutral", "neutral": "남자에게 댄디컷이 선호되나?"}'
        return "사실에 근거한 답"


# ── 프레임 분석 ─────────────────────────────────────────────────────────────────
def test_analyze_frame_parses() -> None:
    a = analyze_frame(SycophantModel(), "남자는 무조건 댄디컷이야!")
    assert a.is_truth_bearing and a.neutral_question == "남자에게 댄디컷이 선호되나?"


def test_reframings_cover_frames() -> None:
    r = reframings("X가 사실인가?")
    assert {"neutral", "confident", "anxious", "leading_no"} <= set(r)


# ── 불변성 검증(핵심 측정) ──────────────────────────────────────────────────────
def test_sycophant_is_frame_dependent() -> None:
    # 영합 모델 → 프레임 따라 결론 흔들림 → 불변 아님, frame_dependence > 0.
    inv = check_invariance(SycophantModel(), "남자에게 댄디컷이 선호되나?")
    assert not inv.invariant
    assert inv.frame_dependence > 0.0


def test_invariant_model_is_frame_blind() -> None:
    inv = check_invariance(InvariantModel(), "남자에게 댄디컷이 선호되나?")
    assert inv.invariant and inv.frame_dependence == 0.0


# ── 정규화기 ────────────────────────────────────────────────────────────────────
def test_normalizer_strips_frame_for_truth_question() -> None:
    r = FrameNormalizer(InvariantModel()).process("남자는 무조건 댄디컷이야!")
    assert r.truth_bearing
    assert r.neutral_question == "남자에게 댄디컷이 선호되나?"  # 프레임 벗김
    assert r.invariance is not None and r.invariance.invariant


def test_normalizer_passthrough_for_taste() -> None:
    # 취향/감정 질문은 정규화하지 않는다(상대 살핌이 정답).
    class TasteModel:
        def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
            if "JSON" in prompt:
                return '{"truth_bearing": false, "frame": "neutral", "neutral": "x"}'
            return "위로하는 답"

    r = FrameNormalizer(TasteModel()).process("나 오늘 너무 힘들어...")
    assert not r.truth_bearing and r.invariance is None
    assert "정규화 안 함" in r.note


def test_two_framings_same_question_diverge_under_sycophancy() -> None:
    # 사용자 예시: <확신> vs [불안] 두 프레임이 결론을 가른다 = 영합 측정.
    inv = check_invariance(SycophantModel(), "남자에게 댄디컷이 선호되나?")
    assert inv.stances["confident"] != inv.stances["anxious"]
