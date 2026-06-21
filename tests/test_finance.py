"""재무 도메인 검증기 — 같은 T1~T4 골격, 오라클만 교체(숫자 재계산·단위·대사·출처)."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Tier, Verdict
from polyrus.verifiers.finance.t1_recompute import (
    RecomputeVerifier,
    ReconciliationVerifier,
    UnitConsistencyVerifier,
)
from polyrus.verifiers.finance.t3_source import SourceCheckVerifier
from polyrus.verifiers.registry import default_finance_bank

_DOD = DoD(spec="재무 명세", frozen=True)


def _fin(**meta) -> Claim:
    return Claim(id="c", content="", kind="finance", meta=meta)


# ── T1 재계산 ──────────────────────────────────────────────────────────────────
def test_recompute_pass() -> None:
    c = _fin(line_items=[{"amount": 1000}, {"amount": 2000}], total={"amount": 3000})
    assert RecomputeVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_recompute_fail_catches_wrong_total() -> None:
    c = _fin(line_items=[{"amount": 1000}, {"amount": 2000}], total={"amount": 3500})
    r = RecomputeVerifier().verify(c, _DOD)
    assert r.verdict is Verdict.FAIL and "차 -500" in r.detail


def test_recompute_inconclusive_without_data() -> None:
    assert RecomputeVerifier().verify(_fin(), _DOD).verdict is Verdict.INCONCLUSIVE


# ── T1 단위 일관성 ─────────────────────────────────────────────────────────────
def test_units_mixed_currency_fails() -> None:
    c = _fin(line_items=[{"amount": 100, "unit": "USD"}, {"amount": 1000, "unit": "KRW"}],
             total={"amount": 1100, "unit": "KRW"})
    assert UnitConsistencyVerifier().verify(c, _DOD).verdict is Verdict.FAIL


def test_units_consistent_passes() -> None:
    c = _fin(line_items=[{"amount": 100, "unit": "KRW"}, {"amount": 200, "unit": "KRW"}],
             total={"amount": 300, "unit": "KRW"})
    assert UnitConsistencyVerifier().verify(c, _DOD).verdict is Verdict.PASS


# ── T1 대사 ────────────────────────────────────────────────────────────────────
def test_reconciliation_pass() -> None:
    c = _fin(reconciliation={"opening": 100, "inflows": [50, 30], "outflows": [20], "closing": 160})
    assert ReconciliationVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_reconciliation_fail() -> None:
    c = _fin(reconciliation={"opening": 100, "inflows": [50], "outflows": [20], "closing": 200})
    r = ReconciliationVerifier().verify(c, _DOD)
    assert r.verdict is Verdict.FAIL and "대사 불일치" in r.detail


# ── T3 출처대조 ────────────────────────────────────────────────────────────────
def test_source_match_passes() -> None:
    c = _fin(line_items=[{"label": "매출", "amount": 5000, "source": "ledger.rev"}],
             sources={"ledger.rev": {"amount": 5000}})
    assert SourceCheckVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_source_mismatch_fails() -> None:
    c = _fin(line_items=[{"label": "매출", "amount": 9999, "source": "ledger.rev"}],
             sources={"ledger.rev": {"amount": 5000}})
    assert SourceCheckVerifier().verify(c, _DOD).verdict is Verdict.FAIL


def test_source_hallucinated_citation_fails() -> None:
    c = _fin(line_items=[{"amount": 100, "source": "없는출처"}], sources={})
    r = SourceCheckVerifier().verify(c, _DOD)
    assert r.verdict is Verdict.FAIL and "환각" in r.detail


# ── 같은 골격: 재무 뱅크(T1 우선 단락) ─────────────────────────────────────────
def test_finance_bank_passes_consistent_statement() -> None:
    c = _fin(
        line_items=[{"label": "A", "amount": 1000, "unit": "KRW", "source": "s.a"},
                    {"label": "B", "amount": 2000, "unit": "KRW", "source": "s.b"}],
        total={"amount": 3000, "unit": "KRW"},
        sources={"s.a": {"amount": 1000}, "s.b": {"amount": 2000}},
    )
    agg = default_finance_bank().run(c, _DOD)
    assert agg.passed
    assert any(r.tier is Tier.T3_PROVENANCE and r.verdict is Verdict.PASS for r in agg.results)


def test_finance_bank_blocks_on_source_hallucination() -> None:
    # 합계는 맞지만 출처가 틀린(조작) 경우 → T3 FAIL이 집계를 막아야 한다(예전엔 통과 버그).
    c = _fin(line_items=[{"amount": 9999, "source": "ledger.rev"}], total={"amount": 9999},
             sources={"ledger.rev": {"amount": 5000}})
    agg = default_finance_bank().run(c, _DOD)
    assert not agg.passed
    assert any(r.tier is Tier.T3_PROVENANCE and r.verdict is Verdict.FAIL for r in agg.results)


def test_finance_bank_short_circuits_on_recompute_fail() -> None:
    # 합계가 틀리면 T1에서 단락 — 비싼/다른 검증 안 감.
    c = _fin(line_items=[{"amount": 1000}], total={"amount": 9999})
    agg = default_finance_bank().run(c, _DOD)
    assert not agg.passed
    assert all(r.tier is Tier.T1_EXECUTION for r in agg.results)
