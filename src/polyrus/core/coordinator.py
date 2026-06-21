from __future__ import annotations

from polyrus.types import Claim


class Coordinator:
    """얇은 조정 뇌. 조기에 하나를 고르지 않고 이견을 보존, 보정된 불확실성을 보고한다."""

    def disagreement(self, candidates: list[Claim]) -> float:
        # TODO(phase1): 후보 간 발산도(콜드 vs 워밍 포함)를 점수화. 높으면 위험 신호.
        raise NotImplementedError("이견 점수 구현")
