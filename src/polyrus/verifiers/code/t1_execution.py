from __future__ import annotations

import os
import sys
from pathlib import Path

from polyrus.sandbox import Sandbox, Workspace
from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier


def _resolve_tool(name: str) -> str | None:
    """venv-친화 도구 해석: 인터프리터 옆 bin → PATH 순. 없으면 None(스킵)."""
    sibling = Path(sys.executable).with_name(name)
    if sibling.exists():
        return str(sibling)
    from shutil import which

    return which(name)


class ExecutionVerifier(BaseVerifier):
    """T1 실행 진실(강). 결정적 무-LLM 오라클 — 린트 + 동결 수용 테스트 실행.

    입력 계약:
      - claim.content: 구현 소스. claim.meta["module"]로 파일명 지정(기본 solution.py).
      - dod.acceptance_tests: 각 항목은 *인라인 테스트 소스*('def test' 포함) 또는 디스크상 .py 경로.
        생성 *전에* 동결된 것만 쓴다(굿하트 차단). 테스트가 없으면 PASS가 아니라 INCONCLUSIVE.

    파이프라인: ruff(있으면) → pytest(수용 테스트). 모두 통과해야 PASS(reliability≈0.99).
    실행 오라클은 '세계'(인터프리터)라 생성기와 독립이다.
    """

    tier = Tier.T1_EXECUTION
    name = "code.t1.execution"
    locality = Locality.LOCAL  # 사용자 머신에서 도는 결정적 오라클

    def __init__(self, sandbox: Sandbox | None = None, *, run_lint: bool = True) -> None:
        self.sandbox = sandbox or Sandbox()
        self.run_lint = run_lint

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        module = str(claim.meta.get("module", "solution.py"))
        with self.sandbox.workspace() as ws:
            ws.write(module, claim.content)
            tests = self._materialize_tests(dod, ws)
            if not tests:
                return self._result(
                    Verdict.INCONCLUSIVE, 0.0, "수용 테스트 없음/미존재 — 검증 불가(DoD 미동결?)"
                )

            # 1) 린트 (도구 있으면). 미설치면 스킵(스퍼리어스 FAIL 금지).
            if self.run_lint:
                ruff = _resolve_tool("ruff")
                if ruff is not None:
                    lint = self.sandbox.run([ruff, "check", ws.path], cwd=ws.path)
                    if lint.timed_out:
                        return self._result(Verdict.FAIL, 0.99, "린트 타임아웃")
                    if lint.returncode != 0:
                        return self._result(
                            Verdict.FAIL, 0.99, f"린트 실패: {self._tail(lint.stdout or lint.stderr)}"
                        )

            # 2) 동결 수용 테스트. 모듈 import 가능하도록 PYTHONPATH=워크스페이스.
            test = self.sandbox.run(
                [sys.executable, "-m", "pytest", "-q", *tests],
                cwd=ws.path,
                env={"PYTHONPATH": ws.path},
            )
            if test.timed_out:
                return self._result(Verdict.FAIL, 0.99, "pytest 타임아웃(무한루프?)")
            if test.returncode == 5:  # pytest: 수집된 테스트 0
                return self._result(Verdict.INCONCLUSIVE, 0.0, "수집된 테스트 0 — 검증 불가")
            if test.returncode != 0:
                return self._result(
                    Verdict.FAIL, 0.99, f"수용 테스트 실패: {self._tail(test.stdout or test.stderr)}"
                )
            return VerifierResult(
                tier=self.tier,
                verdict=Verdict.PASS,
                reliability=0.99,
                detail="린트+수용 테스트 통과",
                evidence=[self._tail(test.stdout)],
                locality=self.locality,
            )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _materialize_tests(self, dod: DoD, ws: Workspace) -> list[str]:
        files: list[str] = []
        for i, entry in enumerate(dod.acceptance_tests):
            if "def test" in entry:
                files.append(ws.write(f"test_acc_{i}.py", entry))
            elif os.path.exists(entry):
                files.append(ws.write(f"test_acc_{i}.py", Path(entry).read_text(encoding="utf-8")))
            # 존재하지 않는 경로 항목은 스킵 → 테스트 0이면 위에서 INCONCLUSIVE 처리.
        return files

    def _result(self, verdict: Verdict, reliability: float, detail: str) -> VerifierResult:
        return VerifierResult(
            tier=self.tier, verdict=verdict, reliability=reliability, detail=detail, locality=self.locality
        )

    @staticmethod
    def _tail(text: str, n: int = 500) -> str:
        return text.strip()[-n:]
