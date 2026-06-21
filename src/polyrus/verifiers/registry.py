from __future__ import annotations

from polyrus.types import AggregateVerdict, Claim, DoD, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import Verifier


class VerifierBank:
    """주장 → 검증기 라우팅. 진짜 해자가 사는 곳: 도메인별 검증기 라이브러리."""

    def __init__(self) -> None:
        self._verifiers: list[Verifier] = []

    def register(self, verifier: Verifier) -> None:
        self._verifiers.append(verifier)

    def for_claim(self, claim: Claim) -> list[Verifier]:
        return [v for v in self._verifiers if v.applies_to(claim)]

    def run(self, claim: Claim, dod: DoD) -> AggregateVerdict:
        """비용순 티어 사다리(5.7): T1(싸고 결정적) 먼저, T1 FAIL이면 단락 — 비싼 티어 안 돌림."""
        applicable = self.for_claim(claim)
        t1 = [v for v in applicable if v.tier is Tier.T1_EXECUTION]
        rest = [v for v in applicable if v.tier is not Tier.T1_EXECUTION]

        results: list[VerifierResult] = []
        for v in t1:
            r = v.verify(claim, dod)
            results.append(r)
            if r.verdict is Verdict.FAIL:
                return AggregateVerdict(results=results)  # 단락: 실행 진실이 깨지면 끝
        for v in rest:
            results.append(v.verify(claim, dod))
        return AggregateVerdict(results=results)


def default_code_bank() -> VerifierBank:
    """경량 기본 스택(빠름·항상 켜짐): T1 실행 + T3 환각 API."""
    from polyrus.sandbox import Sandbox
    from polyrus.verifiers.code.t1_execution import ExecutionVerifier
    from polyrus.verifiers.code.t3_api_existence import ApiExistenceVerifier

    bank = VerifierBank()
    bank.register(ExecutionVerifier(Sandbox()))   # T1 실행 진실(강)
    bank.register(ApiExistenceVerifier())          # T3 환각 API 차단(중)
    return bank


def full_code_bank() -> VerifierBank:
    """깊은 스택: 기본 + T1 뮤테이션 + T2 차등 + T4 적대(퍼징). 느리지만 강하다."""
    from polyrus.sandbox import Sandbox
    from polyrus.verifiers.code.t1_mutation import MutationVerifier
    from polyrus.verifiers.code.t2_differential import DifferentialVerifier
    from polyrus.verifiers.code.t4_adversarial import AdversarialVerifier

    bank = default_code_bank()
    bank.register(MutationVerifier(Sandbox()))      # T1 메타: 테스트 강도(굿하트 방어)
    bank.register(DifferentialVerifier(Sandbox()))  # T2 교차검산(참조 있을 때 활성)
    bank.register(AdversarialVerifier(Sandbox()))   # T4 적대 퍼징(견고성, 약)
    return bank


def default_finance_bank() -> VerifierBank:
    """재무 도메인 뱅크 — *같은 골격, 오라클만 교체*. 코드 실행 → 숫자 재계산·단위·대사·출처.
    해자가 코드에 묶이지 않음을 보이는 실증(도메인별 검증기 = 복리 해자)."""
    from polyrus.verifiers.finance.t1_recompute import (
        RecomputeVerifier,
        ReconciliationVerifier,
        UnitConsistencyVerifier,
    )
    from polyrus.verifiers.finance.t3_source import SourceCheckVerifier

    bank = VerifierBank()
    bank.register(RecomputeVerifier())       # T1 재계산(합계)
    bank.register(UnitConsistencyVerifier())  # T1 단위/통화 일관성
    bank.register(ReconciliationVerifier())   # T1 대사
    bank.register(SourceCheckVerifier())      # T3 출처대조
    return bank


def default_homepage_bank() -> VerifierBank:
    """홈페이지 도메인 뱅크 — 또 같은 골격, 오라클만 교체(결정적 디자인/카피 룰). 4번째 도메인 실증.

    빌더(Claude Code·마누스 등)가 만든 산출물을 *production-grade*로 검증한다(마누스 회피 핵심).
    전부 결정적(무-LLM) T1·T3 — '결정적 검증 우선'.
    """
    from polyrus.research import ReferenceProvenanceVerifier
    from polyrus.verifiers.plan import (
        AccentCountVerifier,
        AISlopVerifier,
        ContrastVerifier,
        EvasionVerifier,
        FrameAlignmentVerifier,
    )

    bank = VerifierBank()
    bank.register(AISlopVerifier())          # T1 카피 AI-slop 차단(design-review 흡수)
    bank.register(ContrastVerifier())        # T1 WCAG 대비
    bank.register(AccentCountVerifier())     # T1 포인트 과용 차단
    bank.register(FrameAlignmentVerifier())  # T1 목적 정렬
    bank.register(EvasionVerifier())         # T1 완료 주장 변명 차단
    bank.register(ReferenceProvenanceVerifier())  # T3 레퍼런스 출처 실재
    return bank


def default_digest_bank() -> VerifierBank:
    """다이제스트 도메인 뱅크 — 5번째 도메인. 발송 전 *충실성*(날조 차단) + AI-slop을 결정적으로 잠근다.
    '빌더가 뭘 만들었든 production-grade인지 검증'을 자동화 산출물에도 적용."""
    from polyrus.digest import DigestFaithfulnessVerifier
    from polyrus.verifiers.plan import AISlopVerifier

    slop = AISlopVerifier()
    slop.kind = "digest"  # 같은 digest claim에 슬롭 검사도 걸리게(applies_to는 self.kind 사용)

    bank = VerifierBank()
    bank.register(DigestFaithfulnessVerifier())  # T1 날조 차단(레포·스타 실재 대조)
    bank.register(slop)                          # T1 슬롭 차단(카피 품질)
    return bank


def default_retrieval_bank() -> VerifierBank:
    """검색/RAG 도메인 뱅크 — 또 같은 골격, 오라클만 교체(출처 대조). 3번째 도메인 실증."""
    from polyrus.verifiers.retrieval.citation import (
        CitationExistenceVerifier,
        QuoteVerifier,
        SupportVerifier,
    )

    bank = VerifierBank()
    bank.register(QuoteVerifier())              # T1 날조 인용 차단(축자 대조)
    bank.register(CitationExistenceVerifier())   # T3 출처 환각 차단
    bank.register(SupportVerifier())             # T3 근거 없는 주장 차단(함의)
    return bank
