"""T4 적대 퍼징 + 보정 재계산(코퍼스→신뢰도→적용된 확신) — v1 마무리."""
from __future__ import annotations

from polyrus.calibration import compute_reliability, recalibrate_result, recalibrate_verdict
from polyrus.escalation import Escalator
from polyrus.harness import Harness, HarnessConfig
from polyrus.types import (
    AggregateVerdict,
    Budget,
    Claim,
    DoD,
    LedgerItem,
    RiskLevel,
    Task,
    Termination,
    Tier,
    Verdict,
    VerifierResult,
)
from polyrus.verifiers.code.t4_adversarial import AdversarialVerifier
from polyrus.verifiers.registry import default_code_bank, full_code_bank
from tests.test_t1_execution import GOOD, TEST


def _claim(code: str, **m) -> Claim:
    return Claim(id="c", content=code, meta={"module": "solution.py", **m})


def _dod(tests: list[str]) -> DoD:
    return DoD(spec="x", acceptance_tests=tests, frozen=True)


# ── T4 적대 퍼징 ───────────────────────────────────────────────────────────────
def test_t4_survives_robust_code() -> None:
    r = AdversarialVerifier().verify(_claim(GOOD), _dod([TEST]))
    assert r.verdict is Verdict.PASS
    assert r.confidence < Tier.T1_EXECUTION.base_strength  # 약한 티어(0.4)


def test_t4_finds_crash() -> None:
    fragile = "def boom(xs):\n    return xs[0]  # 빈 리스트에서 크래시\n"
    dod = _dod(["from solution import boom\ndef test_x():\n    assert boom([1]) == 1\n"])
    r = AdversarialVerifier().verify(_claim(fragile), dod)
    assert r.verdict is Verdict.FAIL and "크래시" in r.detail


def test_t4_inconclusive_without_entry() -> None:
    assert AdversarialVerifier().verify(_claim(GOOD), _dod([])).verdict is Verdict.INCONCLUSIVE


def test_full_bank_includes_t4() -> None:
    assert any(v.tier is Tier.T4_ADVERSARIAL for v in full_code_bank()._verifiers)


# ── 보정 재계산(순수) ──────────────────────────────────────────────────────────
def test_compute_reliability_from_overrides() -> None:
    # t1: 4행 중 1개 정정 → 0.75. t3: 2행 중 1개 정정 → 0.5.
    rows = [
        {"tier": "t1_execution", "override": None},
        {"tier": "t1_execution", "override": None},
        {"tier": "t1_execution", "override": None},
        {"tier": "t1_execution", "override": "false_positive"},
        {"tier": "t3_provenance", "override": None},
        {"tier": "t3_provenance", "override": "false_positive"},
    ]
    rel = compute_reliability(rows)
    assert rel["t1_execution"] == 0.75 and rel["t3_provenance"] == 0.5


def test_recalibrate_replaces_reliability() -> None:
    r = VerifierResult(tier=Tier.T1_EXECUTION, verdict=Verdict.PASS, reliability=0.99)
    cal = recalibrate_result(r, {"t1_execution": 0.5})
    assert cal.reliability == 0.5
    assert cal.confidence == Tier.T1_EXECUTION.base_strength * 0.5  # 확신도 자동 갱신


def test_recalibrate_verdict_passthrough_when_no_map() -> None:
    v = AggregateVerdict(results=[VerifierResult(Tier.T2_CROSS, Verdict.PASS, 0.8)])
    assert recalibrate_verdict(v, {}).results[0].reliability == 0.8


# ── 보정 루프 닫힘: 하니스에 적용 ───────────────────────────────────────────────
class _Arms:
    def generate(self, item, k):
        return [Claim(id="a", content=GOOD, meta={"module": "solution.py"})]

    def select(self, c):
        return c[0]

    def diversify(self, item, b):
        pass


def test_harness_applies_calibration() -> None:
    task = Task(id="t", request="r",
                items=[LedgerItem(id="i1", goal="g", dod=_dod([TEST]), risk=RiskLevel.LOW)])
    # 코퍼스가 t1 신뢰도를 0.5로 낮췄다고 가정 → 검증 통과해도 확신도는 0.5로 보정.
    h = Harness(_Arms(), default_code_bank(), Escalator(),
                cfg=HarnessConfig(max_retries=1), reliability_map={"t1_execution": 0.5})
    res = h.run(task, Budget(max_tokens=10_000_000))
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert abs(res.weighted_confidence - 0.5) < 1e-9  # 자기보고 0.99가 아니라 보정된 0.5
