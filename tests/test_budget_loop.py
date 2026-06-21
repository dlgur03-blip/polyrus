"""v0.5 경계 테스트 — No-Silent-Stop(3 합법 종료), 예산 봉투, 보정 코퍼스, 어댑터.

스텁 Arms/Bank로 코어 루프를 실제로 돌려, 종료가 셋 중 하나로만 끝나고
예산/막힘이 강제 에스컬레이션으로 변환되는지 증명한다(타입만 있고 안 쓰이는 죽은 코드 방지).
"""
from __future__ import annotations

from typing import Any

from polyrus.harness import Harness, HarnessConfig
from polyrus.types import (
    AgentAdapter,
    AggregateVerdict,
    Budget,
    Claim,
    CorpusRecord,
    DoD,
    LedgerItem,
    Locality,
    LoopResult,
    RiskLevel,
    Task,
    Termination,
    Tier,
    Verdict,
    VerifierResult,
)


# ── 스텁(생성기/검증기) ────────────────────────────────────────────────────────
class StubArms:
    def __init__(self, content: str = "candidate") -> None:
        self.content = content
        self.calls = 0
        self.diversified = 0

    def generate(self, item: LedgerItem, k: int) -> list[Claim]:
        self.calls += 1
        return [Claim(id=f"c{self.calls}", content=self.content)]

    def select(self, candidates: list[Claim]) -> Claim:
        return candidates[0]

    def diversify(self, item: LedgerItem, blocker: str) -> None:
        self.diversified += 1


class StubBank:
    """미리 정한 판정 시퀀스를 차례로 반환(마지막 판정을 이후 계속 반복)."""

    def __init__(self, verdicts: list[AggregateVerdict]) -> None:
        self._verdicts = verdicts
        self.i = 0

    def run(self, claim: Claim, dod: DoD) -> AggregateVerdict:
        v = self._verdicts[min(self.i, len(self._verdicts) - 1)]
        self.i += 1
        return v


def _pass() -> AggregateVerdict:
    return AggregateVerdict(
        results=[VerifierResult(tier=Tier.T1_EXECUTION, verdict=Verdict.PASS, reliability=0.95)]
    )


def _fail(detail: str) -> AggregateVerdict:
    return AggregateVerdict(
        results=[
            VerifierResult(tier=Tier.T1_EXECUTION, verdict=Verdict.FAIL, reliability=0.95, detail=detail)
        ]
    )


def _task() -> Task:
    dod = DoD(spec="x", frozen=True)
    return Task(id="t", request="r", items=[LedgerItem(id="i1", goal="g", dod=dod, risk=RiskLevel.LOW)])


def _harness(arms: Any, bank: Any) -> Harness:
    from polyrus.escalation import Escalator

    return Harness(arms, bank, Escalator(), cfg=HarnessConfig(max_retries=4, stuck_threshold=2))


# ── 1) 세 합법 종료 상태 ───────────────────────────────────────────────────────
def test_termination_verified_complete() -> None:
    h = _harness(StubArms(), StubBank([_pass()]))
    res = h.run(_task())
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert res.all_verified
    assert res.items[0].closed
    assert res.weighted_confidence > 0


def test_termination_escalated_on_retries() -> None:
    # 매번 *다른* 블로커 → 막힘 감지 안 걸리고 재시도 소진 → 평범한 에스컬레이션.
    h = _harness(StubArms(), StubBank([_fail("e1"), _fail("e2"), _fail("e3"), _fail("e4")]))
    res = h.run(_task())
    assert res.termination is Termination.ESCALATED
    assert res.items[0].escalated and not res.items[0].closed


def test_termination_budget_escalated_on_stuck() -> None:
    # 같은 블로커 반복 = 팔 다양성 붕괴 → 막힘 감지 → 예산경계 에스컬레이션.
    h = _harness(StubArms(), StubBank([_fail("same"), _fail("same"), _fail("same")]))
    res = h.run(_task())
    assert res.termination is Termination.BUDGET_ESCALATED
    assert "막힘" in res.items[0].escalation_reason


def test_termination_budget_escalated_on_tokens() -> None:
    # 토큰 천장이 먼저 닫힌다. 블로커는 매번 달라 막힘이 아니라 *토큰* 경로임을 보장.
    arms = StubArms(content="x" * 10)
    h = _harness(arms, StubBank([_fail("a"), _fail("b"), _fail("c"), _fail("d")]))
    res = h.run(_task(), Budget(max_tokens=5))  # 첫 시도 spend(10) → 다음 시도 top-check에서 소진
    assert res.termination is Termination.BUDGET_ESCALATED
    assert "예산 소진" in res.items[0].escalation_reason


# ── 2) 비정지 보장: 종료는 항상 셋 중 하나, 조용한 패스 없음 ────────────────────
def test_no_silent_stop_invariant() -> None:
    h = _harness(StubArms(), StubBank([_fail("x"), _fail("y"), _fail("z"), _fail("w")]))
    res = h.run(_task())
    assert res.termination in set(Termination)
    for item in res.items:
        assert item.closed or item.escalated  # 불법(조용한) 종료 없음


# ── 3) 예산 객체 단위 동작 ─────────────────────────────────────────────────────
def test_budget_token_accounting() -> None:
    b = Budget(max_tokens=100)
    b.spend_tokens(40)
    assert b.tokens_remaining() == 60
    assert not b.exhausted()
    b.spend_tokens(60)
    assert b.exhausted()


def test_budget_wall_clock_with_injected_clock() -> None:
    b = Budget(wall_clock_s=10)
    b.start(now=1000.0)
    assert not b.exhausted(now=1005.0)
    assert b.exhausted(now=1011.0)


def test_budget_unlimited_by_default() -> None:
    b = Budget()
    b.spend_tokens(10**9)
    assert b.tokens_remaining() == float("inf")
    assert not b.exhausted(now=10**9)


# ── 4) 보정 코퍼스 emit + 리댁션 ───────────────────────────────────────────────
def test_corpus_emitted_and_redacted() -> None:
    h = _harness(StubArms(content="SECRET-CONTENT"), StubBank([_pass()]))
    res = h.run(_task())
    assert len(res.corpus_records) >= 1
    rec = res.corpus_records[0]
    assert isinstance(rec, CorpusRecord)
    # 리댁션: 레코드 어디에도 원문(claim content)이 없어야 한다 — id·티어·판정만.
    assert "SECRET-CONTENT" not in repr(rec)
    assert rec.tier == Tier.T1_EXECUTION.value
    assert rec.verdict == Verdict.PASS.value
    assert rec.locality == Locality.LOCAL.value


# ── 5) 어댑터 경계(wrap-first) — Protocol 준수 ─────────────────────────────────
class _TinyAdapter:
    name = "tiny"

    def build_task(self, payload: dict[str, Any]) -> Task:
        return Task(id=payload["id"], request=payload["request"])

    def render_continuation(self, result: LoopResult) -> str:
        return " / ".join(result.open_blockers) or "완료"


def test_agent_adapter_protocol_conformance() -> None:
    adapter = _TinyAdapter()
    assert isinstance(adapter, AgentAdapter)  # runtime_checkable Protocol
    task = adapter.build_task({"id": "t9", "request": "r9"})
    assert task.id == "t9"


def test_render_continuation_surfaces_blockers() -> None:
    # 미검증 항목의 블로커가 재주입 reason으로 나오는지(래퍼가 block에 넣을 문자열).
    h = _harness(StubArms(), StubBank([_fail("missing X"), _fail("missing X"), _fail("missing X")]))
    res = h.run(_task())
    cont = _TinyAdapter().render_continuation(res)
    assert "막힘" in cont or "missing X" in cont
