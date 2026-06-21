"""재무 T1 — 결정적 재계산·단위·대사. 코드의 '실행 진실'에 대응하는 *비-LLM 오라클*.

claim.kind == "finance", 데이터는 claim.meta에:
  - line_items: [{"label","amount","unit"?,"source"?}]  + total: {"amount","unit"?} | number
  - reconciliation: {"opening", "inflows":[...], "outflows":[...], "closing"}
숫자는 모델 기억이 아니라 *산술*로 검증된다(할루시네이션 차단).
"""
from __future__ import annotations

from typing import Any

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier


def _amount(x: Any) -> float:
    return float(x["amount"]) if isinstance(x, dict) else float(x)


def _unit(x: Any) -> str | None:
    return x.get("unit") if isinstance(x, dict) else None


class _FinanceBase(BaseVerifier):
    tier = Tier.T1_EXECUTION
    locality = Locality.LOCAL

    def __init__(self, tolerance: float = 0.01) -> None:
        self.tolerance = tolerance

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "finance"

    def _r(self, v: Verdict, rel: float, detail: str) -> VerifierResult:
        return VerifierResult(tier=self.tier, verdict=v, reliability=rel, detail=detail, locality=self.locality)


class RecomputeVerifier(_FinanceBase):
    """라인 항목 합 = 주장한 합계? 산술로 재계산해 대조(결정적, reliability 0.99)."""

    name = "finance.t1.recompute"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        items = claim.meta.get("line_items")
        total = claim.meta.get("total")
        if not items or total is None:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "line_items/total 없음 — 재계산 불가")
        computed = sum(_amount(i) for i in items)
        expected = _amount(total)
        if abs(computed - expected) <= self.tolerance:
            return self._r(Verdict.PASS, 0.99, f"재계산 {computed:g} == 주장 {expected:g}")
        return self._r(Verdict.FAIL, 0.99, f"재계산 {computed:g} ≠ 주장 {expected:g} (차 {computed - expected:g})")


class UnitConsistencyVerifier(_FinanceBase):
    """단위/통화 일관성 — 다른 단위를 더하면 안 된다(USD+KRW 금지)."""

    name = "finance.t1.units"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        items = claim.meta.get("line_items") or []
        units = {_unit(i) for i in items if _unit(i)}
        tu = _unit(claim.meta.get("total"))
        if tu:
            units.add(tu)
        if not units:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "단위 미선언 — 검사 불가")
        if len(units) > 1:
            return self._r(Verdict.FAIL, 0.99, f"단위 불일치(서로 다른 통화 합산): {sorted(units)}")
        return self._r(Verdict.PASS, 0.99, f"단위 일관: {next(iter(units))}")


class ReconciliationVerifier(_FinanceBase):
    """대사(reconciliation) — 기초 + 유입 − 유출 = 기말? 양변이 맞아야 한다."""

    name = "finance.t1.reconciliation"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        r = claim.meta.get("reconciliation")
        if not r:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "reconciliation 없음")
        lhs = float(r.get("opening", 0)) + sum(map(float, r.get("inflows", []))) - sum(map(float, r.get("outflows", [])))
        rhs = float(r["closing"])
        if abs(lhs - rhs) <= self.tolerance:
            return self._r(Verdict.PASS, 0.99, f"대사 일치: 기초+유입−유출={lhs:g} == 기말 {rhs:g}")
        return self._r(Verdict.FAIL, 0.99, f"대사 불일치: 계산 {lhs:g} ≠ 기말 {rhs:g} (차 {lhs - rhs:g})")
