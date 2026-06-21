from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal

from polyrus.core.arms import Arms
from polyrus.dod import DoDGenerator
from polyrus.escalation import Escalator
from polyrus.ledger import Ledger
from polyrus.store import Store
from polyrus.types import (
    AggregateVerdict,
    Budget,
    Claim,
    CorpusRecord,
    LedgerItem,
    LoopResult,
    RiskLevel,
    Task,
    Termination,
)
from polyrus.verifiers.registry import VerifierBank

# 한 항목 해결의 결과 종류 (종료 상태 산출용).
ResolveKind = Literal["verified", "retries", "budget"]


@dataclass
class HarnessConfig:
    max_retries: int = 3
    k_low: int = 1
    k_medium: int = 2
    k_high: int = 4
    stuck_threshold: int = 2  # 같은 블로커가 연속 N회 → 막힘(팔 다양성 붕괴) 감지


def adaptive_k(risk: RiskLevel, cfg: HarnessConfig) -> int:
    return {
        RiskLevel.LOW: cfg.k_low,
        RiskLevel.MEDIUM: cfg.k_medium,
        RiskLevel.HIGH: cfg.k_high,
    }[risk]


class Harness:
    """No-Pass 오케스트레이터.

    핵심 불변식: 루프는 *모델이 끝이라 선언*해서 종료되지 않는다.
    모든 원장 항목이 *검증-완료* 또는 *명시적 에스컬레이션*될 때만 종료된다.
    (경쟁자 루프는 '텍스트-only 응답 = 완료'로 종료한다. Polyrus는 그 조건을 뒤집는다.)

    No-Silent-Stop(5.7): '절대 포기 금지'는 '절대 멈춤 금지'가 아니다. 루프는 예산 봉투 안에서
    *항상* 종료하며, 종료는 셋(검증완료 / 에스컬레이션 / 예산경계-에스컬레이션) 중 하나뿐이다.
    예산 천장을 치면 합법 경로(M3 에스컬레이션)로 변환된다 — 비정지 경로는 없다.
    """

    def __init__(
        self,
        arms: Arms,
        bank: VerifierBank,
        escalator: Escalator,
        cfg: HarnessConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        decomposer: DoDGenerator | None = None,
        understander: object | None = None,
        reliability_map: dict[str, float] | None = None,
    ) -> None:
        self.arms = arms
        self.bank = bank
        self.escalator = escalator
        self.cfg = cfg or HarnessConfig()
        self.clock = clock
        self.decomposer = decomposer or DoDGenerator()
        self.understander = understander  # 이해 게이트(5.5). None이면 건너뜀.
        self.reliability_map = reliability_map  # 코퍼스 기반 보정(5.4). None이면 검증기 자기보고 사용.

    def run(self, task: Task, budget: Budget | None = None, store: Store | None = None) -> LoopResult:
        budget = budget or Budget()
        budget.start(self.clock())
        ledger = Ledger(task, decomposer=self.decomposer)
        records: list[CorpusRecord] = []
        saw_budget = False
        saw_retries = False

        while (item := ledger.next_open_item()) is not None:
            kind = self._resolve(item, ledger, budget, records)
            if kind == "budget":
                saw_budget = True
            elif kind == "retries":
                saw_retries = True

        if ledger.all_items_verified():
            term = Termination.VERIFIED_COMPLETE
        elif saw_budget:
            term = Termination.BUDGET_ESCALATED
        else:
            assert saw_retries  # 미검증인데 예산/재시도 어느 쪽도 아닌 종료는 불가능
            term = Termination.ESCALATED

        # 사후 불변식: 조용한 패스 없음 — 모든 항목은 검증-완료 또는 명시적 에스컬레이션.
        assert all(i.closed or i.escalated for i in ledger.items())

        # 영속화(선택): 코퍼스 플라이휠(5.4) + 원장 결과. None이면 인메모리만.
        if store is not None:
            store.append_corpus(records)
            for item in ledger.items():
                store.save_item(task.id, item)

        return LoopResult(
            termination=term,
            task_id=task.id,
            items=ledger.items(),
            corpus_records=records,
        )

    def _resolve(
        self, item: LedgerItem, ledger: Ledger, budget: Budget, records: list[CorpusRecord]
    ) -> ResolveKind:
        # 이해 검증(5.5): 출력 검증의 상류. 팔의 해석이 갈리면 *생성 전에* 게이트.
        if self.understander is not None:
            u = self.understander.assess(item)
            if u.ambiguous:
                self._escalate(item, ledger, f"이해 모호 — {u.recovery_question}")
                return "retries"

        k = min(adaptive_k(item.risk, self.cfg), budget.max_arms)
        retries = min(self.cfg.max_retries, budget.max_retries)
        verdict: AggregateVerdict | None = None
        last_blocker: str | None = None
        stuck = 0

        for _attempt in range(retries):
            # 예산/벽시계 천장 — 소진 시 합법 M3(예산경계 에스컬레이션)로 변환.
            if budget.exhausted(self.clock()):
                self._escalate(item, ledger, f"예산 소진 (마지막 블로커: {self._blk(verdict)})")
                return "budget"

            candidates = self.arms.generate(item, k=k)          # 병렬 팔 + 콜드스타트
            budget.spend_tokens(self._estimate_cost(candidates))
            best = self.arms.select(candidates)
            self._attach_reference(best, candidates)            # T2 차등용 독립 재구현
            verdict = self.bank.run(best, item.dod)             # 검증 뱅크 T1-T4
            if self.reliability_map:                            # 코퍼스 기반 보정 적용(5.4)
                from polyrus.calibration import recalibrate_verdict

                verdict = recalibrate_verdict(verdict, self.reliability_map)
            records.extend(self._emit_corpus(ledger.task, item, best, verdict))

            if verdict.passed:
                item.solution = best.content  # 검증된 해법 → 스킬 메모리 후보
                ledger.close(item, verdict)
                return "verified"

            # 막힘 감지: 같은 블로커 반복 = 팔 다양성 붕괴 = 새 정보 없음 → 더 태우지 말고 정지.
            if verdict.blocker == last_blocker:
                stuck += 1
                if stuck >= self.cfg.stuck_threshold:
                    self._escalate(item, ledger, f"막힘(팔 다양성 붕괴): {verdict.blocker}")
                    return "budget"
            else:
                stuck = 0
            last_blocker = verdict.blocker
            self.arms.diversify(item, blocker=verdict.blocker)  # M4 다양화 재시도

        # 재시도 한도 소진 → 포기 대신 에스컬레이션 (M3)
        self._escalate(item, ledger, self._blk(verdict))
        return "retries"

    # ── helpers ──────────────────────────────────────────────────────────────

    def _escalate(self, item: LedgerItem, ledger: Ledger, reason: str) -> None:
        item.escalation_reason = reason
        self.escalator.raise_to_human(item, reason)
        ledger.mark_escalated(item)

    @staticmethod
    def _blk(verdict: AggregateVerdict | None) -> str:
        return verdict.blocker if verdict else "no candidates"

    @staticmethod
    def _attach_reference(best: Claim, candidates: list[Claim]) -> None:
        # T2 교차검산용: best와 다른(가능하면 콜드스타트) 후보를 독립 재구현 참조로 붙인다.
        others = [c for c in candidates if c is not best and c.content != best.content]
        cold = [c for c in others if c.meta.get("cold")]
        ref = cold or others
        if ref:
            best.meta = {**best.meta, "reference": ref[0].content}

    @staticmethod
    def _estimate_cost(candidates: list[Claim]) -> int:
        # 토큰 프록시(실측 모델 클라이언트 붙기 전까지). 최소 1로 진행을 보장.
        return sum(len(c.content) for c in candidates) or 1

    @staticmethod
    def _emit_corpus(
        task: Task, item: LedgerItem, claim: Claim, verdict: AggregateVerdict
    ) -> list[CorpusRecord]:
        # 5.4 해자 플라이휠 emit 지점. *리댁션* — id·티어·판정만, 원문/비밀 없음.
        return [
            CorpusRecord(
                task_id=task.id,
                item_id=item.id,
                claim_id=claim.id,
                tier=r.tier.value,
                verdict=r.verdict.value,
                confidence=r.confidence,
                reliability=r.reliability,
                locality=r.locality.value,
            )
            for r in verdict.results
        ]
