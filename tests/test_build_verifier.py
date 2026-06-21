"""빌드 검증기 — '실제로 빌드되나'를 결정적으로(3분기: 빌드/환경/실패). 실 subprocess 스모크 포함."""
from __future__ import annotations

from polyrus.sandbox import ExecResult, Sandbox
from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.build import BuildVerifier, default_build_bank

_DOD = DoD(spec="x", frozen=True)


class _FakeSandbox:
    """주입식 — 고정 ExecResult 반환(네트워크/프로세스 0)."""

    def __init__(self, result: ExecResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def run(self, cmd, *, cwd=None, env=None) -> ExecResult:
        self.calls.append((tuple(cmd), cwd))
        return self.result


def _claim(cwd="/proj") -> Claim:
    return Claim("b", "", kind="build", meta={"cwd": cwd})


def test_build_success_passes() -> None:
    v = BuildVerifier(_FakeSandbox(ExecResult(0, "done", "")), command=["npm", "run", "build"])
    r = v.verify(_claim(), _DOD)
    assert r.verdict is Verdict.PASS and "빌드 성공" in r.detail


def test_build_failure_fails() -> None:
    v = BuildVerifier(_FakeSandbox(ExecResult(1, "", "Type error in page.tsx")))
    r = v.verify(_claim(), _DOD)
    assert r.verdict is Verdict.FAIL and "빌드 실패" in r.detail
    assert any("Type error" in e for e in r.evidence)


def test_missing_tool_is_env_not_failure() -> None:
    # rc 127 = 도구 없음 → INCONCLUSIVE(환경 미비, 코드 FAIL 아님) + 설치 안내.
    v = BuildVerifier(_FakeSandbox(ExecResult(127, "", "command not found: npm")))
    r = v.verify(_claim(), _DOD)
    assert r.verdict is Verdict.INCONCLUSIVE and "환경 미비" in r.detail


def test_passes_cwd_and_command() -> None:
    fake = _FakeSandbox(ExecResult(0, "", ""))
    BuildVerifier(fake, command=["pnpm", "build"]).verify(_claim("/site"), _DOD)
    assert fake.calls[0] == (("pnpm", "build"), "/site")


def test_bank_registers_build() -> None:
    bank = default_build_bank(command=["true"], sandbox=_FakeSandbox(ExecResult(0, "", "")))
    assert bank.run(_claim(), _DOD).passed


# ── 실 subprocess 스모크: 진짜 명령을 돌려 3분기를 확인(Sandbox 실사용) ────────────────
def test_real_subprocess_true_false_missing() -> None:
    sb = Sandbox(timeout_s=10)
    assert BuildVerifier(sb, command=["true"]).verify(_claim(cwd="."), _DOD).verdict is Verdict.PASS
    assert BuildVerifier(sb, command=["false"]).verify(_claim(cwd="."), _DOD).verdict is Verdict.FAIL
    missing = BuildVerifier(sb, command=["polyrus-no-such-tool-xyz"]).verify(_claim(cwd="."), _DOD)
    assert missing.verdict is Verdict.INCONCLUSIVE  # 없는 명령 = 환경 미비
