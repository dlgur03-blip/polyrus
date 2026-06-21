"""검색/RAG 검증기 — 인용 존재(T3)·함의 지지(T3)·인용문 정확(T1).

claim.kind == "retrieval", 데이터는 claim.meta에:
  - citations: [{"text": 주장, "source": 출처키, "quote"?: 원문 인용}]
  - corpus: {출처키: "출처 전문"}
RAG 최상위 실패(출처 환각·근거 없는 주장·날조 인용)를 *모델 기억이 아니라 출처 대조*로 잡는다.
"""
from __future__ import annotations

import re
from typing import Callable

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier

_TOKEN = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


def lexical_support(claim_text: str, source_text: str) -> float:
    """함의 베이스라인 = 주장 토큰이 출처에 덮인 비율. NLI 모델로 교체 가능."""
    c = _tokens(claim_text)
    if not c:
        return 0.0
    return len(c & _tokens(source_text)) / len(c)


class _RetrievalBase(BaseVerifier):
    locality = Locality.LOCAL

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "retrieval"

    def _r(self, v: Verdict, rel: float, detail: str) -> VerifierResult:
        return VerifierResult(tier=self.tier, verdict=v, reliability=rel, detail=detail, locality=self.locality)


class CitationExistenceVerifier(_RetrievalBase):
    """인용한 출처가 실제로 코퍼스에 존재하나(출처 환각 차단)."""

    tier = Tier.T3_PROVENANCE
    name = "retrieval.t3.citation_exists"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        cites = claim.meta.get("citations") or []
        corpus = claim.meta.get("corpus") or {}
        if not cites:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "인용 없음")
        missing = [c["source"] for c in cites if c.get("source") not in corpus]
        if missing:
            return self._r(Verdict.FAIL, 0.7, f"존재하지 않는 출처 인용(환각): {sorted(set(missing))}")
        return self._r(Verdict.PASS, 0.7, f"인용 출처 {len(cites)}건 모두 존재")


class SupportVerifier(_RetrievalBase):
    """각 주장이 인용 출처에 *실제로 지지*되나(근거 없는 주장 차단). 함의 함수 주입 가능."""

    tier = Tier.T3_PROVENANCE
    name = "retrieval.t3.support"

    def __init__(self, *, threshold: float = 0.6, entailment: Callable[[str, str], float] = lexical_support) -> None:
        self.threshold = threshold
        self.entailment = entailment

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        cites = claim.meta.get("citations") or []
        corpus = claim.meta.get("corpus") or {}
        checkable = [c for c in cites if c.get("source") in corpus and c.get("text")]
        if not checkable:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "지지 검사 대상 없음")
        unsupported = [
            c["text"] for c in checkable
            if self.entailment(c["text"], corpus[c["source"]]) < self.threshold
        ]
        if unsupported:
            return self._r(Verdict.FAIL, 0.7, f"출처에 지지되지 않는 주장: {unsupported}")
        return self._r(Verdict.PASS, 0.7, f"주장 {len(checkable)}건 출처가 지지")


class QuoteVerifier(_RetrievalBase):
    """인용문(quote)이 출처에 *그대로* 있나(날조 인용 차단). 정확 문자열 대조 = 결정적(T1)."""

    tier = Tier.T1_EXECUTION
    name = "retrieval.t1.quote"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        cites = claim.meta.get("citations") or []
        corpus = claim.meta.get("corpus") or {}
        quoted = [c for c in cites if c.get("quote")]
        if not quoted:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "인용문 없음")
        fabricated = [
            c["quote"] for c in quoted
            if c.get("source") not in corpus or c["quote"] not in corpus[c["source"]]
        ]
        if fabricated:
            return self._r(Verdict.FAIL, 0.95, f"출처에 없는 인용문(날조): {fabricated}")
        return self._r(Verdict.PASS, 0.95, f"인용문 {len(quoted)}건 출처와 일치(축자)")
