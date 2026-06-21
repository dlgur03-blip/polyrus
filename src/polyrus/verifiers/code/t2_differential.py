from __future__ import annotations

import re
import sys

from polyrus.sandbox import Sandbox
from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier

_IMPORT = re.compile(r"from\s+solution\s+import\s+([A-Za-z_]\w*)")

# 차등 하니스: 두 독립 구현을 Hypothesis 입력으로 돌려 출력 비교.
_DIFF_HARNESS = """\
from hypothesis import given, settings, strategies as st
from solution import {entry} as _a
from reference import {entry} as _b

@given(st.lists(st.integers(min_value=-100, max_value=100)))
@settings(max_examples=60, deadline=None)
def _diff(xs):
    assert _a(list(xs)) == _b(list(xs))

_diff()
print("DIFF_OK")
"""


class DifferentialVerifier(BaseVerifier):
    """T2 교차검산(중). 콜드스타트 팔의 독립 재구현과 차등 테스트.

    같은 식으로 틀리지 않는 두 구현을 Hypothesis 입력으로 돌려 출력 비교 —
    일치=증거, 발산=버그 + 반례 확보. claim.meta['reference']에 독립 재구현 소스가 있어야 활성.
    참조가 없으면 INCONCLUSIVE(다른 팔이 없으면 교차검산 불가).

    정직한 경계: 기본 입력 도메인은 정수 리스트(우리 코드 도메인의 흔한 형태).
    임의 시그니처 일반화는 전략 주입(향후)으로.
    """

    tier = Tier.T2_CROSS
    name = "code.t2.differential"
    locality = Locality.LOCAL

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        reference = claim.meta.get("reference")
        if not reference:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "참조 구현 없음 — 교차검산 불가")
        entry = self._entry(claim, dod)
        if not entry:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "엔트리 함수 미상(수용 테스트의 import에서 추론 실패)")

        with self.sandbox.workspace() as ws:
            ws.write(str(claim.meta.get("module", "solution.py")), claim.content)
            ws.write("reference.py", str(reference))
            ws.write("_diff.py", _DIFF_HARNESS.format(entry=entry))
            res = self.sandbox.run([sys.executable, "_diff.py"], cwd=ws.path, env={"PYTHONPATH": ws.path})

        if res.timed_out:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "차등 실행 타임아웃")
        if res.returncode == 0 and "DIFF_OK" in res.stdout:
            return self._r(Verdict.PASS, 0.8, "독립 재구현과 모든 생성 입력에서 일치")
        # Hypothesis가 반례를 stderr에 떨어뜨린다.
        tail = (res.stderr or res.stdout).strip()[-400:]
        return self._r(Verdict.FAIL, 0.8, f"독립 재구현과 발산(반례 존재): {tail}")

    def _entry(self, claim: Claim, dod: DoD) -> str | None:
        if claim.meta.get("entry"):
            return str(claim.meta["entry"])
        for t in dod.acceptance_tests:
            m = _IMPORT.search(t)
            if m:
                return m.group(1)
        return None

    def _r(self, verdict: Verdict, reliability: float, detail: str) -> VerifierResult:
        return VerifierResult(tier=self.tier, verdict=verdict, reliability=reliability, detail=detail,
                              locality=self.locality)
