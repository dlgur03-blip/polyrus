"""재무 T3 — 출처대조. 주장한 수치가 *인용한 출처*에 실제로 그 값으로 있나(환각 차단).

claim.meta["line_items"] 각 항목의 "source" 키가 claim.meta["sources"]의 실제 값과 일치해야 한다.
모델이 그럴듯한 숫자를 지어낸 경우(노력 패스)를 잡는다.
"""
from __future__ import annotations

from typing import Any

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier


def _amount(x: Any) -> float:
    return float(x["amount"]) if isinstance(x, dict) else float(x)


class SourceCheckVerifier(BaseVerifier):
    tier = Tier.T3_PROVENANCE
    name = "finance.t3.source"
    locality = Locality.LOCAL

    def __init__(self, tolerance: float = 0.01) -> None:
        self.tolerance = tolerance

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "finance"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        items = claim.meta.get("line_items") or []
        sources = claim.meta.get("sources") or {}
        cited = [i for i in items if isinstance(i, dict) and i.get("source")]
        if not cited:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "출처 인용 없음 — 대조 불가")
        problems: list[str] = []
        for it in cited:
            src = it["source"]
            if src not in sources:
                problems.append(f"출처 '{src}' 없음(환각 인용)")
            elif abs(_amount(it) - _amount(sources[src])) > self.tolerance:
                problems.append(f"'{it.get('label', src)}' {_amount(it):g} ≠ 출처 {_amount(sources[src]):g}")
        if problems:
            return self._r(Verdict.FAIL, 0.7, "출처 불일치: " + "; ".join(problems))
        return self._r(Verdict.PASS, 0.7, f"인용 {len(cited)}건 출처와 일치")

    def _r(self, v: Verdict, rel: float, detail: str) -> VerifierResult:
        return VerifierResult(tier=self.tier, verdict=v, reliability=rel, detail=detail, locality=self.locality)
