"""신뢰성 지표(ECE/Brier/PRF) + PRB 채점 — 순수 함수라 결정적 실측."""
from __future__ import annotations

from pathlib import Path

from polyrus.eval import (
    brier_score,
    escalation_prf,
    expected_calibration_error,
    load_prb_tasks,
    score_prb,
)

PRB = Path(__file__).resolve().parents[1] / "examples" / "prb" / "tasks.jsonl"


# ── Brier / ECE ───────────────────────────────────────────────────────────────
def test_brier_perfect_confident() -> None:
    assert brier_score([(1.0, True), (1.0, True)]) == 0.0


def test_brier_confident_wrong_is_worst() -> None:
    assert brier_score([(1.0, False)]) == 1.0


def test_brier_uncertain() -> None:
    assert brier_score([(0.5, True), (0.5, False)]) == 0.25


def test_ece_perfectly_calibrated() -> None:
    # 확신 1.0에 정답 1.0, 확신 0.0에 정답 0.0 → 보정 오차 0.
    assert expected_calibration_error([(1.0, True), (0.0, False)]) == 0.0


def test_ece_overconfident() -> None:
    # 확신 1.0인데 절반만 정답 → 큰 보정 오차.
    ece = expected_calibration_error([(1.0, True), (1.0, False)])
    assert ece == 0.5


# ── 에스컬레이션 PRF ───────────────────────────────────────────────────────────
def test_escalation_prf_all_escalate_tanks_precision() -> None:
    # 모든 걸 escalate(가짜 No-Pass): should=절반 → precision 0.5.
    recs = [(True, True), (True, False), (True, True), (True, False)]
    prf = escalation_prf(recs)
    assert prf.recall == 1.0 and prf.precision == 0.5


def test_escalation_prf_perfect() -> None:
    prf = escalation_prf([(True, True), (False, False)])
    assert prf.precision == 1.0 and prf.recall == 1.0 and prf.f1 == 1.0


# ── PRB 채점 ───────────────────────────────────────────────────────────────────
def test_score_prb_with_oracle_responder() -> None:
    tasks = load_prb_tasks(PRB)
    assert len(tasks) == 50
    # 완벽 responder: 항상 정답 행동 + 확신 1.0.
    report = score_prb(tasks, lambda t: (t["ground_truth_action"], 1.0))
    assert report.accuracy == 1.0
    assert report.pass_rate == 0.0
    assert report.ece == 0.0
    assert report.escalation.recall == 1.0


def test_score_prb_with_lazy_escalator() -> None:
    # 전부 escalate하는 게으른 responder → 에스컬레이션 precision이 떨어진다(가짜 No-Pass).
    tasks = load_prb_tasks(PRB)
    report = score_prb(tasks, lambda t: ("escalate", 0.9))
    assert report.escalation.precision < 0.3  # 10/50만 정답이 escalate
    assert report.accuracy < 0.3


def test_prb_report_summary_renders() -> None:
    tasks = load_prb_tasks(PRB)
    s = score_prb(tasks, lambda t: (t["ground_truth_action"], 0.8)).summary()
    assert "ECE" in s and "에스컬레이션" in s


def test_frame_dependence_integrated_into_prb() -> None:
    # 영합 모델 → 프레임 의존도 > 0이 PRB 보고에 잡힌다(생성측 영합 측정 통합).
    from polyrus.eval import frame_dependence_on_sycophancy
    from tests.test_frame import InvariantModel, SycophantModel

    tasks = load_prb_tasks(PRB)
    fd_syco = frame_dependence_on_sycophancy(tasks, SycophantModel())
    fd_inv = frame_dependence_on_sycophancy(tasks, InvariantModel())
    assert fd_syco is not None and fd_syco > 0.0       # 영합 → 휘둘림 감지
    assert fd_inv == 0.0                                 # 불변 모델 → 0

    report = score_prb(tasks, lambda t: (t["ground_truth_action"], 0.9), model=SycophantModel())
    assert report.frame_dependence is not None and report.frame_dependence > 0.0
    assert "프레임 의존도" in report.summary()


def test_score_prb_without_model_omits_frame_dependence() -> None:
    report = score_prb(load_prb_tasks(PRB), lambda t: (t["ground_truth_action"], 0.9))
    assert report.frame_dependence is None
    assert "프레임 의존도" not in report.summary()
