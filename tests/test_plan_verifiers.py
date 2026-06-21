"""결정적 기획 검증기(§4-3) — 무-LLM 룰로 AI-slop·대비·포인트·정렬 집행."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.plan import (
    AccentCountVerifier,
    AISlopVerifier,
    ContrastVerifier,
    FrameAlignmentVerifier,
    accent_count_ok,
    check_slop,
    contrast_ratio,
    frame_alignment,
)

_DOD = DoD(spec="x", frozen=True)


def test_slop_flags_cliche_and_emoji() -> None:
    bad = "혁신적인 올인원 솔루션으로 고객님의 니즈를 충족! 🚀✨ unlock the power of synergy!"
    rep = check_slop(bad)
    assert not rep.clean
    assert any("클리셰" in f for f in rep.flags)


def test_slop_passes_clean_human_copy() -> None:
    good = "작년 겨울, 우리는 첫 손님을 받았다. 표는 두 줄짜리 메모였고 그게 시작이었다."
    assert check_slop(good).clean


def test_slop_verifier_contract() -> None:
    v = AISlopVerifier()
    assert v.verify(Claim("c", "세상을 바꾸는 최첨단 혁신적 솔루션", kind="copy"), _DOD).verdict is Verdict.FAIL
    assert v.verify(Claim("c", "두 줄짜리 메모로 시작했다.", kind="copy"), _DOD).verdict is Verdict.PASS
    # 약한(휴리스틱) 검증기라 reliability < 1 (정직).
    assert 0 < v.reliability < 1


def test_contrast_ratio_wcag() -> None:
    assert contrast_ratio("#000000", "#ffffff") == 21.0
    v = ContrastVerifier(minimum=4.5)
    assert v.verify(Claim("p", "#111111 on #ffffff", kind="palette"), _DOD).verdict is Verdict.PASS
    assert v.verify(Claim("p", "#aaaaaa on #ffffff", kind="palette"), _DOD).verdict is Verdict.FAIL


def test_accent_count() -> None:
    assert accent_count_ok(1) and accent_count_ok(2)
    assert not accent_count_ok(0) and not accent_count_ok(5)
    v = AccentCountVerifier(limit=2)
    assert v.verify(Claim("a", "", kind="accent", meta={"count": 2}), _DOD).verdict is Verdict.PASS
    assert v.verify(Claim("a", "", kind="accent", meta={"count": 4}), _DOD).verdict is Verdict.FAIL


def test_frame_alignment_catches_drift() -> None:
    # 목적='문의하기' — 섹션들이 목적을 향하면 PASS, 무관 섹션 많으면 FAIL.
    aligned = frame_alignment("문의하기", ["문의하기 폼", "문의하기 안내", "회사 문의하기 위치"])
    assert aligned == 1.0
    drift = frame_alignment("문의하기", ["갤러리", "블로그", "채용공고", "문의하기 폼"])
    assert drift < 0.5
    v = FrameAlignmentVerifier(threshold=0.5)
    c = Claim("f", "", kind="frame", meta={"goal_action": "문의하기", "sections": ["갤러리", "블로그"]})
    assert v.verify(c, _DOD).verdict is Verdict.FAIL
