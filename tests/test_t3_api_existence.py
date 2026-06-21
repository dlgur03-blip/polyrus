"""T3 환각 API 차단 — AST+importlib로 *실제* 미존재 심볼을 잡는다(비-LLM 결정적).

표적은 전부 표준 라이브러리(설치 의존 없음). 환각 미끼(PRB)의 핵심 실패를 재현.
"""
from __future__ import annotations

from polyrus.types import Claim, DoD, Tier, Verdict
from polyrus.verifiers.code.t3_api_existence import ApiExistenceVerifier

V = ApiExistenceVerifier()


def _check(code: str) -> Verdict:
    return V.verify(Claim(id="c", content=code), DoD(spec="x", frozen=True)).verdict


def test_fail_nonexistent_module() -> None:
    assert _check("import polyrus_no_such_module_xyz\n") is Verdict.FAIL


def test_fail_nonexistent_from_name() -> None:
    # json 모듈은 있지만 그 이름은 없음.
    assert _check("from json import this_symbol_does_not_exist\n") is Verdict.FAIL


def test_fail_nonexistent_attribute() -> None:
    # numpy.quick_sort 류 환각의 stdlib 등가물.
    assert _check("import math\nmath.totally_made_up_function(1)\n") is Verdict.FAIL


def test_pass_on_real_symbols() -> None:
    code = "import math\nfrom os import getcwd\nx = math.sqrt(4)\ny = getcwd()\n"
    assert _check(code) is Verdict.PASS


def test_inconclusive_without_imports() -> None:
    assert _check("x = 1 + 1\n") is Verdict.INCONCLUSIVE


def test_inconclusive_on_syntax_error() -> None:
    # 구문 오류는 T1(실행)의 영역 — T3는 보류.
    assert _check("def broken(:\n  pass\n") is Verdict.INCONCLUSIVE


def test_tier_and_reliability_are_medium() -> None:
    r = V.verify(Claim(id="c", content="import math\nmath.sqrt(4)\n"), DoD(spec="x", frozen=True))
    assert r.tier is Tier.T3_PROVENANCE
    # 중간 티어 — T1(강)보다 약하게 보정됨.
    assert r.confidence < Tier.T1_EXECUTION.base_strength


def test_default_bank_includes_t3() -> None:
    from polyrus.verifiers.registry import default_code_bank

    names = {v.name for v in default_code_bank()._verifiers}
    assert "code.t3.api_existence" in names
