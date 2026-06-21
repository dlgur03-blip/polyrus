from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Locality(Enum):
    """검증기가 어디서 실행되나. 무료→유료 seam이자 비밀 리댁션 경계(6.3).
    LOCAL은 사용자 머신에서 도는 결정적·무-LLM 오라클(비밀 불출). MANAGED는 옵트인 검증 클라우드."""

    LOCAL = "local"
    MANAGED = "managed"


class Tier(Enum):
    """검증 강도 티어. 낮은 티어 판정에 높은 티어 확신을 부여하지 말 것."""

    T1_EXECUTION = "t1_execution"      # 강: 결정적 무-LLM 오라클
    T2_CROSS = "t2_cross"              # 중: 독립 재구현 차등
    T3_PROVENANCE = "t3_provenance"    # 중: 출처/존재 대조
    T4_ADVERSARIAL = "t4_adversarial"  # 약: 적대 비평/퍼징

    @property
    def base_strength(self) -> float:
        return {
            "t1_execution": 1.0,
            "t2_cross": 0.8,
            "t3_provenance": 0.7,
            "t4_adversarial": 0.4,
        }[self.value]


class Verdict(Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"  # 검증 불가 → 보정된 솔직함으로 남김


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Claim:
    """검증 대상 단위. 예: 생성된 코드 패치 하나."""

    id: str
    content: str
    kind: str = "code"  # code | data | retrieval | ...
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DoD:
    """완료 정의. 생성 *전에* 동결된다 (스펙-우선)."""

    spec: str
    acceptance_tests: list[str] = field(default_factory=list)
    properties: list[str] = field(default_factory=list)
    frozen: bool = False


@dataclass
class VerifierResult:
    tier: Tier
    verdict: Verdict
    reliability: float  # 이 검증기 자체의 신뢰도(보정값) 0..1
    detail: str = ""
    evidence: list[str] = field(default_factory=list)  # M6 출처 사슬
    locality: Locality = Locality.LOCAL  # 어디서 검증됐나 (리댁션/과금 경계)

    @property
    def confidence(self) -> float:
        """티어 강도 x 검증기 신뢰도. 완벽 검증을 가정하지 않는다."""
        if self.verdict is not Verdict.PASS:
            return 0.0
        return self.tier.base_strength * self.reliability


@dataclass
class AggregateVerdict:
    results: list[VerifierResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # 강~중 티어(T1·T2·T3) FAIL은 하드 블록(실행 오류·교차검산 불일치·출처/환각).
        # T4(적대, 약)는 '생존 + 우려' — 하드 블록 대신 확신만 깎는다.
        blocking = (Tier.T1_EXECUTION, Tier.T2_CROSS, Tier.T3_PROVENANCE)
        if any(r.tier in blocking and r.verdict is Verdict.FAIL for r in self.results):
            return False
        return any(r.verdict is Verdict.PASS for r in self.results)

    @property
    def weighted_confidence(self) -> float:
        passes = [r.confidence for r in self.results if r.verdict is Verdict.PASS]
        return max(passes, default=0.0)

    @property
    def blocker(self) -> str:
        fails = [r for r in self.results if r.verdict is Verdict.FAIL]
        return "; ".join(f"[{r.tier.value}] {r.detail}" for r in fails) or "no blocker"


@dataclass
class LedgerItem:
    id: str
    goal: str
    dod: DoD
    risk: RiskLevel = RiskLevel.MEDIUM
    closed: bool = False
    verdict: AggregateVerdict | None = None
    confidence: float = 0.0
    escalated: bool = False
    escalation_reason: str = ""  # M3: 왜 에스컬레이션됐나 (블로커/예산/막힘)
    solution: str = ""           # 검증 통과한 해법(스킬 메모리에 기록될 산출)


@dataclass
class Task:
    """사용자 요청 한 건. 여러 LedgerItem으로 분해된다."""

    id: str
    request: str
    items: list[LedgerItem] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# v0.5 경계 — 정지·예산(No-Silent-Stop), 보정 코퍼스(해자), 어댑터(wrap-first)
# 상세: ../20260618_1141_..._배포전략_wrap-first_기획안.md · DESIGN.md §10
# ─────────────────────────────────────────────────────────────────────────────


class Termination(Enum):
    """루프의 *합법* 종료 상태 — No-Silent-Stop. 이 외의 종료는 구조적으로 불가능.
    금지(불법) 상태: 조용한 가짜-완료 / 조용한 누락 / 확신에 찬 오답."""

    VERIFIED_COMPLETE = "verified_complete"  # 전 항목 검증 합치
    ESCALATED = "escalated"                  # 재시도 소진 → M3
    BUDGET_ESCALATED = "budget_escalated"    # 예산/막힘 소진 → 강제 M3
    ENV_BLOCKED = "env_blocked"              # 환경 미비(도구 없음) → 빌드 전 안내 게이트(초보자 온보딩)


@dataclass
class Budget:
    """태스크당 예산 봉투(1급 자원). 다이얼 B(속도↔철저)에서 파생. 비정지 보장의 토대 —
    매 반복이 유한 예산에서 차감되고, 소진은 결정적으로 에스컬레이션으로 라우팅된다.
    (None = 무제한; 천장이 설정된 축만 정지에 기여.)"""

    max_tokens: int | None = None
    max_arms: int = 4
    max_retries: int = 3
    wall_clock_s: float | None = None
    tokens_spent: int = 0
    _started_at: float | None = field(default=None, repr=False)

    def start(self, now: float) -> None:
        """벽시계 기준점. 멱등(첫 호출만 유효)."""
        if self._started_at is None:
            self._started_at = now

    def spend_tokens(self, n: int) -> None:
        if n < 0:
            raise ValueError("토큰 소비는 음수가 될 수 없다")
        self.tokens_spent += n

    def tokens_remaining(self) -> float:
        if self.max_tokens is None:
            return float("inf")
        return max(0, self.max_tokens - self.tokens_spent)

    def exhausted(self, now: float | None = None) -> bool:
        if self.max_tokens is not None and self.tokens_spent >= self.max_tokens:
            return True
        if (
            self.wall_clock_s is not None
            and self._started_at is not None
            and now is not None
            and now - self._started_at >= self.wall_clock_s
        ):
            return True
        return False


@dataclass
class CorpusRecord:
    """보정 코퍼스 한 점(5.4 해자 플라이휠). *리댁션됨* — id·티어·판정만, 원문/비밀 없음.
    override는 사람의 사후 정정(있으면 검증기 위양성/위음성 라벨이 된다)."""

    task_id: str
    item_id: str
    claim_id: str
    tier: str
    verdict: str
    confidence: float
    reliability: float
    locality: str = Locality.LOCAL.value
    override: str | None = None


@dataclass
class LoopResult:
    """코어 루프의 호스트-비종속 출력. 어댑터가 이걸 호스트 행동으로 렌더한다
    (미검증 → 재주입 reason, 완료 → 허용)."""

    termination: Termination
    task_id: str
    items: list[LedgerItem] = field(default_factory=list)
    corpus_records: list[CorpusRecord] = field(default_factory=list)

    @property
    def all_verified(self) -> bool:
        return bool(self.items) and all(i.closed for i in self.items)

    @property
    def weighted_confidence(self) -> float:
        # 가장 약한 고리 = 전체 확신 (검증된 항목 기준)
        confs = [i.confidence for i in self.items if i.closed]
        return min(confs, default=0.0)

    @property
    def open_blockers(self) -> list[str]:
        """재주입(continuation)에 쓸, 아직 안 닫힌 항목들의 블로커."""
        out: list[str] = []
        for i in self.items:
            if not i.closed:
                reason = i.escalation_reason or (i.verdict.blocker if i.verdict else "no candidates")
                out.append(f"{i.goal}: {reason}")
        return out


@runtime_checkable
class AgentAdapter(Protocol):
    """호스트 에이전트(Claude Code 등) ↔ 코어 루프의 비종속 경계 (wrap-first).
    코어는 어댑터를 모른다. 어댑터가 (1) 호스트 입력을 Task로 만들고,
    (2) LoopResult를 호스트 행동(미검증 → 재주입 reason)으로 렌더한다.
    흡수방지 해자의 코드적 실체 — 같은 코어가 Claude Code/Codex/CLI/게이트웨이를 떠받친다."""

    name: str

    def build_task(self, payload: dict[str, Any]) -> Task: ...

    def render_continuation(self, result: LoopResult) -> str: ...
