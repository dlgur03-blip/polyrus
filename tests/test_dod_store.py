"""DoD 분해·동결 + SQLite 영속화(코퍼스 플라이휠) — 실측."""
from __future__ import annotations

from polyrus.dod import DoDGenerator
from polyrus.escalation import Escalator
from polyrus.harness import Harness, HarnessConfig
from polyrus.store import Store
from polyrus.types import (
    Claim,
    CorpusRecord,
    DoD,
    LedgerItem,
    Locality,
    RiskLevel,
    Task,
    Termination,
    Tier,
    Verdict,
)
from polyrus.verifiers.registry import default_code_bank
from tests.test_t1_execution import GOOD, TEST  # 코드/테스트 픽스처 재사용


class _Arms:
    """데모 스크립트 생성기 (진짜 LLM Arms는 Phase 1)."""

    def __init__(self, code: str) -> None:
        self.code = code

    def generate(self, item: LedgerItem, k: int) -> list[Claim]:
        return [Claim(id=f"{item.id}-c", content=self.code, meta={"module": "solution.py"})]

    def select(self, c: list[Claim]) -> Claim:
        return c[0]

    def diversify(self, item: LedgerItem, blocker: str) -> None:
        pass


def _harness(code: str) -> Harness:
    return Harness(_Arms(code), default_code_bank(), Escalator(), cfg=HarnessConfig(max_retries=3))


# ── DoD 분해·동결 ─────────────────────────────────────────────────────────────
def test_decompose_splits_multi_goal() -> None:
    task = Task(id="t", request="1. 로그인 구현\n2. 비밀번호 재설정\n3. 이메일 인증")
    items = DoDGenerator().decompose(task)
    assert len(items) == 3
    assert all(it.dod.frozen for it in items)  # 모든 DoD 동결(굿하트)
    assert items[0].goal == "로그인 구현"


def test_decompose_single_goal() -> None:
    items = DoDGenerator().decompose(Task(id="t", request="sum_even_squares 구현"))
    assert len(items) == 1


def test_decompose_freezes_existing_items() -> None:
    dod = DoD(spec="x", frozen=False)
    task = Task(id="t", request="r", items=[LedgerItem(id="i", goal="g", dod=dod)])
    items = DoDGenerator().decompose(task)
    assert items[0].dod.frozen


def test_derive_dod_freezes() -> None:
    dod = DoDGenerator().derive_dod("스펙", acceptance_tests=["def test_x(): pass"])
    assert dod.frozen and dod.acceptance_tests


# ── SQLite 영속화 ─────────────────────────────────────────────────────────────
def test_store_corpus_roundtrip() -> None:
    s = Store(":memory:")
    s.append_corpus([
        CorpusRecord("t", "i", "c", Tier.T1_EXECUTION.value, Verdict.PASS.value, 0.99, 0.99,
                     Locality.LOCAL.value),
    ])
    assert s.corpus_count() == 1
    assert s.reliability_summary()[Tier.T1_EXECUTION.value][Verdict.PASS.value] == 1
    s.close()


def test_store_file_persists_across_reopen(tmp_path) -> None:
    db = str(tmp_path / "polyrus.db")
    with Store(db) as s:
        s.append_corpus([CorpusRecord("t", "i", "c", "t1_execution", "pass", 0.99, 0.99, "local")])
    with Store(db) as s2:  # 닫았다 다시 열어도 남아 있다(플라이휠은 영속)
        assert s2.corpus_count() == 1


def test_store_override_label() -> None:
    s = Store(":memory:")
    s.append_corpus([CorpusRecord("t", "i", "c", "t1_execution", "pass", 0.99, 0.99, "local")])
    s.set_override(1, "false_positive")  # 사람 정정 = 보정 ground-truth
    assert s.corpus_rows()[0]["override"] == "false_positive"
    s.close()


def test_harness_persists_corpus_and_items() -> None:
    s = Store(":memory:")
    dod = DoDGenerator().derive_dod("짝수 제곱합", acceptance_tests=[TEST])
    task = Task(id="tk", request="r",
                items=[LedgerItem(id="i1", goal="g", dod=dod, risk=RiskLevel.LOW)])
    res = _harness(GOOD).run(task, store=s)
    assert res.termination is Termination.VERIFIED_COMPLETE
    assert s.corpus_count() >= 1            # 코퍼스 emit DB 영속
    rows = s.items("tk")
    assert rows and rows[0]["closed"] == 1  # 원장 결과 영속
    s.close()
