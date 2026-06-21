from __future__ import annotations

from typing import Protocol, runtime_checkable

from polyrus.types import Claim, DoD, Locality, Tier, VerifierResult


@runtime_checkable
class Verifier(Protocol):
    """모든 검증기의 계약.

    구현 규칙:
    - 생성기와 *독립*일 것. 가능하면 비-LLM 오라클(컴파일러/런타임/정적분석/메타데이터).
    - 외부 명령은 반드시 sandbox를 통해 실행할 것.
    - reliability(검증기 자체 신뢰도)를 정직하게 보고할 것 — 약한 검증기의 PASS는 확신도 1이 아니다.
    - locality(LOCAL/MANAGED)를 선언할 것 — 비밀 리댁션·무료→유료 경계의 day-1 표식(DESIGN §10).
    """

    tier: Tier
    name: str
    locality: Locality

    def applies_to(self, claim: Claim) -> bool: ...

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult: ...


class BaseVerifier:
    """편의 베이스. tier/name/locality를 설정하고 applies_to 기본 구현 제공."""

    tier: Tier
    name: str = "base"
    locality: Locality = Locality.LOCAL  # 기본은 로컬-우선(결정적 무-LLM 오라클)

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "code"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:  # pragma: no cover
        raise NotImplementedError(
            "검증기를 구현하세요. (No-Pass: 빈 구현을 완료로 보고하지 말 것)"
        )
