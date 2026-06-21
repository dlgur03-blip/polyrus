"""보정 재계산 (5.4 해자 *작동*) — 코퍼스 → 검증기 신뢰도 곡선 → 적용된 확신도.

emit/저장(store)된 코퍼스에 사람의 override(정정) 라벨이 쌓이면, 그게 검증기의 위양성/위음성
신호가 된다. 티어별 경험적 신뢰도 = 1 − 정정률. 이 곡선을 다시 확신도에 *적용*해, 약한 검증기의
PASS가 더 이상 높은 확신을 못 받게 한다. 이게 '확신도 = 검증기 신뢰도'를 실제로 닫는 루프.

플라이휠: 태스크↑ → 코퍼스↑ → 보정↑ → 확신 신뢰↑. 여기 함수가 그 '보정↑' 단계다.
"""
from __future__ import annotations

from dataclasses import replace

from polyrus.types import AggregateVerdict, VerifierResult


def compute_reliability(rows: list, *, min_samples: int = 1) -> dict[str, float]:
    """코퍼스 행 → 티어별 경험적 신뢰도. row는 tier·override 필드를 갖는 매핑/Row.

    신뢰도 = 1 − (정정된 행 / 전체 행). override가 있으면 검증기가 틀렸다는 사람 라벨.
    샘플이 min_samples 미만인 티어는 생략(과적합 방지).
    """
    total: dict[str, int] = {}
    wrong: dict[str, int] = {}
    for r in rows:
        tier = r["tier"]
        total[tier] = total.get(tier, 0) + 1
        if r["override"]:
            wrong[tier] = wrong.get(tier, 0) + 1
    out: dict[str, float] = {}
    for tier, n in total.items():
        if n >= min_samples:
            out[tier] = max(0.0, 1.0 - wrong.get(tier, 0) / n)
    return out


def recalibrate_result(result: VerifierResult, reliability_map: dict[str, float]) -> VerifierResult:
    """검증기가 보고한 reliability를 코퍼스 기반 경험값으로 교체(있으면). confidence는 파생되므로 자동 갱신."""
    cal = reliability_map.get(result.tier.value)
    if cal is None:
        return result
    return replace(result, reliability=cal)


def recalibrate_verdict(verdict: AggregateVerdict, reliability_map: dict[str, float]) -> AggregateVerdict:
    return AggregateVerdict(results=[recalibrate_result(r, reliability_map) for r in verdict.results])
