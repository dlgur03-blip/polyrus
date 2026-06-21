"""Session 통합 — Dials 하나에서 budget·bank·컨텍스트·보정을 파생해 한 번에 실행."""
from __future__ import annotations

from polyrus.context import ContextEngine, ContextItem, ContextStore
from polyrus.dials import Dials
from polyrus.session import Session
from polyrus.store import Store
from polyrus.types import DoD, LedgerItem, RiskLevel, Task, Termination
from tests.test_phase1_arms import FakeModel, GOOD_FENCED
from tests.test_t1_execution import TEST


def _task() -> Task:
    dod = DoD(spec="짝수 제곱합", acceptance_tests=[TEST], frozen=True)
    return Task(id="t", request="sum_even_squares 구현",
                items=[LedgerItem(id="i1", goal="sum_even_squares 구현", dod=dod, risk=RiskLevel.LOW)])


# ── 통합 happy path ────────────────────────────────────────────────────────────
def test_session_runs_to_verified() -> None:
    res = Session(FakeModel(GOOD_FENCED), dials=Dials(thoroughness=0.3)).run(_task())
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert res.corpus_records


def test_session_from_preset_launch_uses_deep_bank() -> None:
    # '출시' 프리셋(thoroughness 1.0) → full bank(뮤테이션·차등·적대)로도 통과.
    res = Session.from_preset(FakeModel(GOOD_FENCED), "출시").run(_task())
    assert res.termination is Termination.VERIFIED_COMPLETE


def test_session_persists_corpus_to_store() -> None:
    store = Store(":memory:")
    Session(FakeModel(GOOD_FENCED), dials=Dials(thoroughness=0.2), store=store).run(_task())
    assert store.corpus_count() >= 1
    assert store.items("t")  # 원장 결과도 영속


def test_session_applies_calibration_from_store() -> None:
    # 코퍼스에 t1 정정 라벨을 미리 심어 신뢰도를 낮추면, 다음 실행 확신도가 보정된다.
    store = Store(":memory:")
    from polyrus.types import CorpusRecord
    # t1 5건 중 다수 정정 → 경험 신뢰도 낮음.
    for i in range(5):
        store.append_corpus([CorpusRecord("old", "i", f"c{i}", "t1_execution", "pass", 0.99, 0.99, "local",
                                          override="false_positive" if i < 4 else None)])
    res = Session(FakeModel(GOOD_FENCED), dials=Dials(thoroughness=0.2), store=store).run(_task())
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert res.weighted_confidence < 0.5  # 자기보고 0.99가 아니라 보정으로 낮아짐


def test_session_injects_context() -> None:
    # 컨텍스트 엔진을 끼우면 워밍 팔 프롬프트에 관련 컨텍스트가 들어간다.
    # (어휘 스코어러는 한글 조사에 약함 — 토큰이 겹치게 작성. 임베딩 scorer로 업그레이드 가능.)
    cs = ContextStore()
    cs.add(ContextItem("c1", "sum_even_squares 구현 메모: 빈 리스트는 0 반환"))
    model = FakeModel(GOOD_FENCED)
    Session(model, dials=Dials(thoroughness=0.2), context_engine=ContextEngine(cs)).run(_task())
    warm_prompts = [c["prompt"] for c in model.calls if "관련 컨텍스트" in c["prompt"]]
    assert warm_prompts  # 워밍 팔이 컨텍스트를 받음
