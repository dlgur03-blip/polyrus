from __future__ import annotations

import re
import sys

from polyrus.sandbox import Sandbox
from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier

_IMPORT = re.compile(r"from\s+solution\s+import\s+([A-Za-z_]\w*)")

# 퍼징 하니스: 광범위한 입력으로 *크래시(미처리 예외)*를 노린다. 정답이 아니라 견고성.
_FUZZ = """\
from hypothesis import given, settings, strategies as st
from solution import {entry} as f

@given(st.lists(st.integers(min_value=-10**6, max_value=10**6)))
@settings(max_examples=120, deadline=None)
def _fuzz(xs):
    f(list(xs))  # 반환값은 안 본다 — 미처리 예외(크래시)만 잡는다

_fuzz()
print("FUZZ_OK")
"""


class AdversarialVerifier(BaseVerifier):
    """T4 적대비평(약). 정답 없는 견고성 — 레드팀/퍼징으로 깨려 시도하고 생존 여부를 본다.

    PASS도 '약'하다(reliability 0.4): 깨지지 않았다는 것이지 옳다는 증명이 아니다.
    크래시를 찾으면 FAIL + 반례. (정확성은 T1, 이건 견고성 우려를 표면화.)
    """

    tier = Tier.T4_ADVERSARIAL
    name = "code.t4.adversarial"
    locality = Locality.LOCAL

    def __init__(self, sandbox: Sandbox | None = None) -> None:
        self.sandbox = sandbox or Sandbox()

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        entry = self._entry(claim, dod)
        if not entry:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "엔트리 함수 미상 — 퍼징 대상 없음")
        with self.sandbox.workspace() as ws:
            ws.write(str(claim.meta.get("module", "solution.py")), claim.content)
            ws.write("_fuzz.py", _FUZZ.format(entry=entry))
            res = self.sandbox.run([sys.executable, "_fuzz.py"], cwd=ws.path, env={"PYTHONPATH": ws.path})
        if res.timed_out:
            return self._r(Verdict.INCONCLUSIVE, 0.0, "퍼징 타임아웃")
        if res.returncode == 0 and "FUZZ_OK" in res.stdout:
            return self._r(Verdict.PASS, 0.4, "퍼징 생존(약한 증거 — 견고성만, 정확성 아님)")
        tail = (res.stderr or res.stdout).strip()[-400:]
        return self._r(Verdict.FAIL, 0.4, f"퍼징 중 크래시(견고성 결함): {tail}")

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
