"""검증된 스킬 메모리(자기개선축) — *검증 통과한 것만* 저장·recall. 닫힌 루프 실증."""
from __future__ import annotations

from polyrus.dials import Dials
from polyrus.memory import SkillStore
from polyrus.session import Session
from polyrus.types import DoD, LedgerItem, RiskLevel, Task, Termination
from tests.test_phase1_arms import FakeModel, GOOD_FENCED
from tests.test_t1_execution import BAD, GOOD, TEST


# ── SkillStore 기본 ─────────────────────────────────────────────────────────────
def test_record_and_recall() -> None:
    s = SkillStore(":memory:")
    s.record(kind="code", goal="sum_even_squares 구현", solution=GOOD, confidence=0.99)
    s.record(kind="code", goal="문자열 뒤집기", solution="s[::-1]", confidence=0.9)
    assert s.count() == 2
    hits = s.recall("sum_even_squares 만들어줘", k=1)
    assert len(hits) == 1 and "sum_even_squares" in hits[0].goal


def test_recall_increments_uses() -> None:
    s = SkillStore(":memory:")
    s.record(kind="code", goal="even squares sum", solution="x", confidence=0.9)
    s.recall("even squares")
    assert s.all()[0].uses == 1


def test_persists_across_reopen(tmp_path) -> None:
    db = str(tmp_path / "skills.db")
    with SkillStore(db) as s:
        s.record(kind="code", goal="g", solution="sol", confidence=0.9)
    with SkillStore(db) as s2:
        assert s2.count() == 1


def _task(goal: str) -> Task:
    dod = DoD(spec="짝수 제곱합", acceptance_tests=[TEST], frozen=True)
    return Task(id="t", request=goal, items=[LedgerItem(id="i1", goal=goal, dod=dod, risk=RiskLevel.LOW)])


# ── 닫힌 루프: 검증 통과 → 학습 → 다음에 recall ───────────────────────────────
def test_session_learns_only_verified() -> None:
    skills = SkillStore(":memory:")
    # 올바른 코드 → 검증 통과 → 스킬 기록.
    res = Session(FakeModel(GOOD_FENCED), dials=Dials(thoroughness=0.2), skills=skills).run(
        _task("sum_even_squares 구현"))
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert skills.count() == 1
    assert "sum_even_squares" in skills.all()[0].goal


def test_session_does_not_learn_failures() -> None:
    skills = SkillStore(":memory:")
    # 틀린 코드만 내는 모델 → 검증 실패·에스컬레이션 → *학습 안 함*(틀린 걸 저장하지 않는다).
    Session(FakeModel(f"```python\n{BAD}```"), dials=Dials(thoroughness=0.2), skills=skills).run(
        _task("sum_even_squares 구현"))
    assert skills.count() == 0


def test_recalled_skill_injected_into_next_task() -> None:
    # 1) 첫 작업 검증 통과 → 스킬 저장. 2) 유사 작업 → 그 스킬이 프롬프트(컨텍스트)에 주입.
    skills = SkillStore(":memory:")
    model = FakeModel(GOOD_FENCED)
    Session(model, dials=Dials(thoroughness=0.2), skills=skills).run(_task("sum_even_squares 구현"))

    model2 = FakeModel(GOOD_FENCED)
    Session(model2, dials=Dials(thoroughness=0.4), skills=skills).run(_task("sum_even_squares 다시 구현"))
    # 두 번째 실행의 워밍 팔 프롬프트에 '이전 검증된 해법'이 들어갔다.
    assert any("이전 검증된 해법" in c["prompt"] for c in model2.calls)


# ── 절제된 자율: 체크포인트/재개(하트비트 없음) ────────────────────────────────
def test_resume_skips_already_verified() -> None:
    from polyrus.store import Store

    store = Store(":memory:")
    # 1) 정상 실행 → 항목 검증·완료가 store에 영속.
    Session(FakeModel(GOOD_FENCED), dials=Dials(thoroughness=0.2), store=store).run(
        _task("sum_even_squares 구현"))

    # 2) resume=True → 같은 task를 다시 돌려도 완료 항목은 건너뛴다 → 모델 호출 0.
    model2 = FakeModel(GOOD_FENCED)
    res = Session(model2, dials=Dials(thoroughness=0.2), store=store).run(
        _task("sum_even_squares 구현"), resume=True)
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert model2.calls == []  # 이미 검증된 항목 → 재생성 안 함(체크포인트 재개)
