"""가중치 다이얼 A~D + 프리셋(4.3) — 하나의 정책 객체가 Budget·Config·모드·게이트를 파생."""
from __future__ import annotations

from polyrus.dials import PRESETS, Dials, preset, recommend


def test_mode_from_convergence() -> None:
    assert Dials(convergence=1.0).mode == "convergent"
    assert Dials(convergence=0.0).mode == "divergent"


def test_thoroughness_scales_budget() -> None:
    fast = Dials(thoroughness=0.0).to_budget()
    deep = Dials(thoroughness=1.0).to_budget()
    assert deep.max_arms > fast.max_arms and deep.max_retries > fast.max_retries


def test_thoroughness_scales_k() -> None:
    assert Dials(thoroughness=1.0).to_harness_config().k_high > Dials(thoroughness=0.0).to_harness_config().k_high


def test_caution_gates_reversible() -> None:
    assert Dials(caution=1.0).gate_reversible is True
    assert Dials(caution=0.0).gate_reversible is False


def test_autonomy_sets_ambiguity_threshold() -> None:
    # 개입(autonomy 낮음) → 더 잘 게이트(임계 높음).
    assert Dials(autonomy=0.0).ambiguity_threshold > Dials(autonomy=1.0).ambiguity_threshold


def test_presets_exist() -> None:
    assert {"브레인스토밍", "출시", "리서치"} <= set(PRESETS)
    assert PRESETS["브레인스토밍"].mode == "divergent"
    assert PRESETS["출시"].mode == "convergent"


def test_preset_returns_copy() -> None:
    d = preset("출시")
    d.convergence = 0.0
    assert PRESETS["출시"].convergence == 1.0  # 원본 불변


def test_recommend_code_is_convergent() -> None:
    dials, reason = recommend("code")
    assert dials.mode == "convergent" and "검증" in reason


def test_recommend_naming_is_divergent() -> None:
    dials, reason = recommend("naming")
    assert dials.mode == "divergent" and "발산" in reason
