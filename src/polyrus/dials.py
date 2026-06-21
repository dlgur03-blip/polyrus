"""가중치 다이얼 A~D + 프리셋 (4.3) — 오케스트레이션 정책을 하나의 객체로.

다이얼 = HOW(팔 수·온도·검증 티어·게이트 강도 = 하니스가 집행). 페르소나/CLAUDE.md = WHAT(프롬프트).
*분리*가 핵심 — 프로젝트 파일을 고치지 않고 작업 단위로 모드를 바꾼다.

투명한 추천: 시스템이 작업마다 추천 + 한 줄 이유를 주고, 사용자는 받아들이거나 틀거나 무시(기본값).
위계는 CSS 캐스케이드 — 프로젝트 기본값 → 작업별 추천 → 사용자 override.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from polyrus.harness import HarnessConfig
from polyrus.types import Budget


@dataclass
class Dials:
    """값 0..1. 각 다이얼의 양 극(본 문서가 발견한 네 긴장)."""

    convergence: float = 1.0   # A: 0=발산(폭·취향) … 1=수렴(검증기 왕·하나의 답)
    thoroughness: float = 0.5  # B: 0=속도(단일패스·T1만) … 1=철저(k↑·티어 깊게·재시도↑)
    autonomy: float = 0.5      # C: 0=개입(자주 묻기) … 1=자율(트레이에 모으고 진행)
    caution: float = 1.0       # D: 0=과감(되돌릴수있는행동 자동) … 1=신중(더 게이트)

    @property
    def mode(self) -> str:
        return "convergent" if self.convergence >= 0.5 else "divergent"

    def to_budget(self) -> Budget:
        # 철저할수록 팔 더 많이·재시도 더(느림·확신↑).
        return Budget(max_arms=1 + round(self.thoroughness * 5), max_retries=1 + round(self.thoroughness * 4))

    def to_harness_config(self) -> HarnessConfig:
        return HarnessConfig(
            max_retries=1 + round(self.thoroughness * 4),
            k_low=1,
            k_medium=max(1, round(self.thoroughness * 3)),
            k_high=2 + round(self.thoroughness * 4),
        )

    @property
    def gate_reversible(self) -> bool:
        # 매우 신중하면 되돌릴 수 있는 행동도 게이트(보통은 안 게이트).
        return self.caution >= 0.8

    @property
    def ambiguity_threshold(self) -> float:
        # 개입(autonomy 낮음)일수록 더 잘 게이트 → 이해 확신 임계를 높인다(0.4..0.8).
        return 0.4 + (1.0 - self.autonomy) * 0.4


PRESETS: dict[str, Dials] = {
    "브레인스토밍": Dials(convergence=0.0, thoroughness=0.2, autonomy=1.0, caution=0.0),
    "출시": Dials(convergence=1.0, thoroughness=1.0, autonomy=0.0, caution=1.0),
    "리서치": Dials(convergence=0.2, thoroughness=1.0, autonomy=0.5, caution=0.7),
}


def preset(name: str) -> Dials:
    """프리셋 복사본(원본 불변)."""
    return replace(PRESETS[name])


def recommend(task_kind: str) -> tuple[Dials, str]:
    """작업 성격 → 추천 다이얼 + 한 줄 이유(투명한 추천). 사용자가 덮어쓸 수 있다."""
    kind = task_kind.lower()
    if kind in {"code", "data", "math", "fact", "sql"}:
        return preset("출시"), "검증 가능한 작업 → 수렴·철저·신중(검증기로 잠금)"
    if kind in {"naming", "copy", "design", "angle", "brand"}:
        return preset("브레인스토밍"), "정답 오라클 없는 창의 작업 → 발산·빠름·과감(스프레드 보존)"
    if kind in {"research", "analysis", "search"}:
        return preset("리서치"), "탐색 작업 → 발산·철저"
    return Dials(), "기본값(수렴·중간·신중) — 작업 성격 미상"
