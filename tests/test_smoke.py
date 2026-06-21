"""스모크 테스트: 스캐폴드가 import되고 계약이 존재하는지 (구현 전에도 green)."""
from __future__ import annotations

import polyrus
from polyrus.harness import HarnessConfig, adaptive_k
from polyrus.types import AggregateVerdict, RiskLevel, Tier, Verdict, VerifierResult
from polyrus.verifiers.registry import VerifierBank, default_code_bank


def test_version() -> None:
    assert polyrus.__version__


def test_tier_strength_ordering() -> None:
    assert Tier.T1_EXECUTION.base_strength > Tier.T4_ADVERSARIAL.base_strength


def test_confidence_zero_when_not_pass() -> None:
    r = VerifierResult(tier=Tier.T1_EXECUTION, verdict=Verdict.FAIL, reliability=0.99)
    assert r.confidence == 0.0


def test_confidence_is_tier_weighted() -> None:
    r = VerifierResult(tier=Tier.T4_ADVERSARIAL, verdict=Verdict.PASS, reliability=1.0)
    assert r.confidence == Tier.T4_ADVERSARIAL.base_strength  # 약한 티어 PASS != 확신도 1


def test_t1_fail_blocks_aggregate() -> None:
    agg = AggregateVerdict(
        results=[
            VerifierResult(tier=Tier.T1_EXECUTION, verdict=Verdict.FAIL, reliability=0.99),
            VerifierResult(tier=Tier.T2_CROSS, verdict=Verdict.PASS, reliability=0.8),
        ]
    )
    assert agg.passed is False


def test_adaptive_k_scales_with_risk() -> None:
    cfg = HarnessConfig()
    assert adaptive_k(RiskLevel.HIGH, cfg) >= adaptive_k(RiskLevel.LOW, cfg)


def test_default_bank_constructs() -> None:
    assert isinstance(default_code_bank(), VerifierBank)
