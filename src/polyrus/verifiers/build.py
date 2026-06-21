"""빌드 검증기 — 산출물이 *실제로 빌드되나*를 결정적으로 잠근다(말 말고 실행).

to_task → 빌더 위임 → 이 검증기가 진짜 빌드 명령(예: `npm run build`)을 Sandbox로 돌려
exit code로 판정한다. '빌딩은 위임, 검증은 우리'(마누스 회피)의 실행 오라클.

3분기(환경 미비를 코드 결함·변명과 섞지 않는다 — preflight 사상):
  - rc 0      → PASS  (실제로 빌드됨, 강한 신호)
  - rc 127    → INCONCLUSIVE (빌드 도구 없음 = 환경 미비 → 설치 안내, 코드 FAIL 아님)
  - 그 외     → FAIL  (진짜 빌드 에러)

도메인 무관: Next.js `npm run build`, 파이썬 `pytest`, 무엇이든 명령만 갈아끼우면 된다.
"""
from __future__ import annotations

from collections.abc import Sequence

from polyrus.sandbox import Sandbox
from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult


class BuildVerifier:
    """프로젝트 디렉토리에서 빌드 명령을 실행해 성공 여부로 판정. claim.meta['cwd']=프로젝트 경로."""

    tier: Tier = Tier.T1_EXECUTION
    name: str = "build"
    locality: Locality = Locality.LOCAL
    reliability: float = 0.95  # 실제 빌드 = 강한 결정적 신호

    def __init__(
        self,
        sandbox: Sandbox | None = None,
        *,
        command: Sequence[str] = ("npm", "run", "build"),
    ) -> None:
        self.sandbox = sandbox or Sandbox(timeout_s=300)
        self.command = list(command)

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "build"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        cwd = str(claim.meta.get("cwd") or claim.content).strip()
        res = self.sandbox.run(self.command, cwd=cwd or None)
        cmd_str = " ".join(self.command)
        if res.ok:
            return self._r(Verdict.PASS, f"빌드 성공 ({cmd_str})")
        if res.returncode == 127:
            # 빌드 도구 미설치 = 환경 미비(코드 FAIL 아님) → 설치 안내로 라우팅.
            from polyrus.preflight import translate_toolchain_error

            friendly = translate_toolchain_error(res.stderr) or f"빌드 도구가 없어요({cmd_str[0:20]})"
            return self._r(Verdict.INCONCLUSIVE, f"환경 미비 — {friendly}")
        tail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        timed = " (타임아웃)" if res.timed_out else ""
        return self._r(Verdict.FAIL, f"빌드 실패{timed} ({cmd_str})", tail)

    def _r(self, v: Verdict, detail: str, ev: list[str] | None = None) -> VerifierResult:
        return VerifierResult(self.tier, v, self.reliability, detail, ev or [], self.locality)


def default_build_bank(command: Sequence[str] = ("npm", "run", "build"), *, sandbox: Sandbox | None = None):
    """빌드 검증 뱅크 — to_task 산출물이 실제로 빌드되는지 잠근다."""
    from polyrus.verifiers.registry import VerifierBank

    bank = VerifierBank()
    bank.register(BuildVerifier(sandbox, command=command))
    return bank
