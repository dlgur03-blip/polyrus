"""sandbox + T1 실행 검증기 — *실제로* subprocess/pytest를 돌려 결정적 판정을 증명한다.

가짜 PASS 금지(No-Pass 자기적용): 올바른 코드는 PASS, 틀린 코드는 FAIL,
테스트 없으면 INCONCLUSIVE. 검증기는 생성기와 독립(인터프리터=오라클)이어야 한다.
"""
from __future__ import annotations

import sys

from polyrus.sandbox import Sandbox, Workspace
from polyrus.types import Claim, DoD, Tier, Verdict
from polyrus.verifiers.code.t1_execution import ExecutionVerifier
from polyrus.verifiers.registry import VerifierBank, default_code_bank

GOOD = "def sum_even_squares(xs):\n    return sum(x * x for x in xs if x % 2 == 0)\n"
BAD = "def sum_even_squares(xs):\n    return sum(x * x for x in xs)  # 홀수도 더함 = 틀림\n"
TEST = (
    "from solution import sum_even_squares\n"
    "def test_basic():\n"
    "    assert sum_even_squares([1, 2, 3, 4]) == 20\n"
    "def test_empty():\n"
    "    assert sum_even_squares([]) == 0\n"
    "def test_odds_only():\n"
    "    assert sum_even_squares([1, 3, 5]) == 0\n"
)


def _claim(code: str) -> Claim:
    return Claim(id="c1", content=code, meta={"module": "solution.py"})


def _dod(tests: list[str]) -> DoD:
    return DoD(spec="짝수만 제곱해 합", acceptance_tests=tests, frozen=True)


# ── Sandbox ────────────────────────────────────────────────────────────────
def test_sandbox_runs_command() -> None:
    res = Sandbox().run([sys.executable, "-c", "print('hi')"])
    assert res.ok and "hi" in res.stdout


def test_sandbox_timeout() -> None:
    res = Sandbox(timeout_s=1).run([sys.executable, "-c", "import time; time.sleep(5)"])
    assert res.timed_out and not res.ok


def test_sandbox_missing_tool_returns_127() -> None:
    res = Sandbox().run(["polyrus__no_such_tool__"])
    assert res.returncode == 127


def test_workspace_path_escape_blocked() -> None:
    with Workspace() as ws:
        try:
            ws.write("../escape.py", "x = 1")
            raised = False
        except ValueError:
            raised = True
    assert raised


# ── T1 ExecutionVerifier (실측) ───────────────────────────────────────────────
def test_t1_pass_on_correct_code() -> None:
    r = ExecutionVerifier().verify(_claim(GOOD), _dod([TEST]))
    assert r.verdict is Verdict.PASS
    assert r.tier is Tier.T1_EXECUTION
    assert r.confidence > 0.9  # 강한 티어 + 높은 신뢰도


def test_t1_fail_on_wrong_code() -> None:
    r = ExecutionVerifier().verify(_claim(BAD), _dod([TEST]))
    assert r.verdict is Verdict.FAIL
    assert r.confidence == 0.0  # 비-PASS는 확신도 0
    assert "수용 테스트 실패" in r.detail


def test_t1_inconclusive_without_tests() -> None:
    r = ExecutionVerifier().verify(_claim(GOOD), _dod([]))
    assert r.verdict is Verdict.INCONCLUSIVE  # 검증 불가 ≠ PASS


def test_t1_fail_on_syntax_error() -> None:
    r = ExecutionVerifier().verify(_claim("def broken(:\n    pass\n"), _dod([TEST]))
    assert r.verdict is Verdict.FAIL


# ── 뱅크 등록 + T1 단락 ────────────────────────────────────────────────────────
def test_default_bank_has_t1() -> None:
    bank = default_code_bank()
    agg = bank.run(_claim(GOOD), _dod([TEST]))
    assert agg.passed
    assert any(rr.tier is Tier.T1_EXECUTION and rr.verdict is Verdict.PASS for rr in agg.results)


def test_bank_short_circuits_on_t1_fail() -> None:
    # T1 FAIL이면 비싼 티어를 돌리지 않는다 — 결과에 T1만.
    bank = VerifierBank()
    bank.register(ExecutionVerifier())

    class _BoomT4:
        tier = Tier.T4_ADVERSARIAL
        name = "boom"
        from polyrus.types import Locality as _L

        locality = _L.LOCAL

        def applies_to(self, claim: Claim) -> bool:
            return True

        def verify(self, claim: Claim, dod: DoD):  # 호출되면 안 됨
            raise AssertionError("T1 FAIL인데 비싼 티어가 돌았다(단락 실패)")

    bank.register(_BoomT4())
    agg = bank.run(_claim(BAD), _dod([TEST]))
    assert not agg.passed
    assert len(agg.results) == 1 and agg.results[0].tier is Tier.T1_EXECUTION
