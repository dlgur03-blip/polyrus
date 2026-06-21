"""T1 뮤테이션(테스트 강도) + T2 차등(독립 재구현) — 실측(실제 pytest/hypothesis 구동)."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Tier, Verdict
from polyrus.verifiers.code.t1_mutation import MutationVerifier, _count_sites, _mutate
from polyrus.verifiers.code.t2_differential import DifferentialVerifier
from polyrus.verifiers.registry import full_code_bank
from tests.test_t1_execution import BAD, GOOD, TEST

WEAK_TEST = "from solution import sum_even_squares\ndef test_empty():\n    assert sum_even_squares([]) == 0\n"


def _claim(code: str, **meta) -> Claim:
    return Claim(id="c", content=code, meta={"module": "solution.py", **meta})


def _dod(tests: list[str]) -> DoD:
    return DoD(spec="짝수 제곱합", acceptance_tests=tests, frozen=True)


# ── 뮤테이션 메커니즘(순수) ─────────────────────────────────────────────────────
def test_count_and_mutate_sites() -> None:
    src = "def f(xs):\n    return sum(x * x for x in xs if x % 2 == 0)\n"
    n = _count_sites(src)
    assert n >= 3  # Eq, Mult, 상수 2/0
    assert _mutate(src, 0) != src and _mutate(src, 0) is not None
    assert _mutate(src, 999) is None  # 범위 밖


# ── 뮤테이션 검증기(굿하트 방어) ────────────────────────────────────────────────
def test_mutation_pass_with_strong_tests() -> None:
    # 강한 수용 테스트(basic+empty+odds)는 변이를 잡아낸다 → 높은 점수 → PASS.
    r = MutationVerifier(min_score=0.5).verify(_claim(GOOD), _dod([TEST]))
    assert r.verdict is Verdict.PASS


def test_mutation_fail_with_weak_tests() -> None:
    # 빈 입력만 검사하는 약한 테스트는 변이를 못 잡는다 → 낮은 점수 → FAIL(테스트 약함).
    r = MutationVerifier(min_score=0.5).verify(_claim(GOOD), _dod([WEAK_TEST]))
    assert r.verdict is Verdict.FAIL
    assert "약함" in r.detail


def test_mutation_inconclusive_without_tests() -> None:
    assert MutationVerifier().verify(_claim(GOOD), _dod([])).verdict is Verdict.INCONCLUSIVE


# ── 차등 검증기(독립 재구현) ────────────────────────────────────────────────────
def test_differential_pass_on_equivalent() -> None:
    # 동등한 두 구현 → 모든 생성 입력에서 일치 → PASS.
    r = DifferentialVerifier().verify(_claim(GOOD, reference=GOOD), _dod([TEST]))
    assert r.verdict is Verdict.PASS


def test_differential_fail_on_divergence() -> None:
    # 올바른 구현 vs 틀린 참조 → Hypothesis가 반례 발견 → FAIL.
    r = DifferentialVerifier().verify(_claim(GOOD, reference=BAD), _dod([TEST]))
    assert r.verdict is Verdict.FAIL


def test_differential_inconclusive_without_reference() -> None:
    assert DifferentialVerifier().verify(_claim(GOOD), _dod([TEST])).verdict is Verdict.INCONCLUSIVE


# ── full_code_bank: 4티어 통합 ──────────────────────────────────────────────────
def test_full_bank_passes_strong_solution() -> None:
    bank = full_code_bank()
    agg = bank.run(_claim(GOOD, reference=GOOD), _dod([TEST]))
    assert agg.passed
    # T1 둘(실행+뮤테이션) + T2 차등이 모두 돌았다.
    assert sum(1 for r in agg.results if r.tier is Tier.T1_EXECUTION) >= 2
    assert any(r.tier is Tier.T2_CROSS for r in agg.results)


def test_full_bank_short_circuits_on_t1_fail() -> None:
    # T1 실행이 FAIL이면 비싼 뮤테이션/차등을 돌리지 않는다(단락).
    bank = full_code_bank()
    agg = bank.run(_claim(BAD, reference=GOOD), _dod([TEST]))
    assert not agg.passed
    assert all(r.tier.value == "t1_execution" for r in agg.results)  # T1만 돌고 멈춤
