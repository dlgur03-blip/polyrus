from __future__ import annotations

import ast
import importlib
import importlib.util

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult
from polyrus.verifiers.base import BaseVerifier


class ApiExistenceVerifier(BaseVerifier):
    """T3 출처대조(중). 환각 API 차단 — *비-LLM* 결정적 대조.

    코드가 부르는 외부 심볼이 실제로 존재하는지를 모델 기억이 아니라
    *설치된 패키지*에 대조한다(PRB 환각 미끼 패밀리가 노리는 바로 그 실패).

    검사 범위(정직한 경계):
      - import 모듈 존재 (importlib.util.find_spec)
      - from-import 이름 존재 (모듈 import 후 hasattr)
      - `모듈.속성` 접근 존재 (예: numpy.quick_sort → 없음)
    인스턴스 메서드 환각(예: df.optimize_memory)은 타입 추론이 필요 → 타입체커(T1) 영역.
    claim 코드 자체는 *실행하지 않는다* — 참조된 라이브러리만 import해 대조한다.
    """

    tier = Tier.T3_PROVENANCE
    name = "code.t3.api_existence"
    locality = Locality.LOCAL

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        try:
            tree = ast.parse(claim.content)
        except SyntaxError as e:
            # 구문 오류는 T1(실행)의 영역. T3는 판단 보류.
            return self._result(Verdict.INCONCLUSIVE, 0.0, f"구문 오류로 대조 불가: {e}")

        imports: dict[str, str] = {}   # alias → 전체 모듈명
        from_imports: list[tuple[str, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # 상대 임포트는 외부 심볼 아님 → 스킵
                    continue
                for a in node.names:
                    from_imports.append((node.module or "", a.name))

        missing: list[str] = []
        resolved: dict[str, object | None] = {}

        def resolve(modname: str) -> object | None:
            if modname not in resolved:
                try:
                    resolved[modname] = importlib.import_module(modname)
                except Exception:  # noqa: BLE001 — import 실패 = 미존재로 취급
                    resolved[modname] = None
            return resolved[modname]

        # 1) import 모듈 존재
        for full in set(imports.values()):
            top = full.split(".")[0]
            if not self._module_exists(top):
                missing.append(f"모듈 '{full}' 없음")

        # 2) from-import 이름 존재
        for mod, name in from_imports:
            if name == "*" or not mod:
                continue
            if not self._module_exists(mod.split(".")[0]):
                missing.append(f"모듈 '{mod}' 없음")
                continue
            m = resolve(mod)
            if m is not None and not hasattr(m, name):
                missing.append(f"'{mod}.{name}' 없음 (임포트 심볼 미존재)")

        # 3) 모듈.속성 접근 존재 (환각 API의 핵심 케이스)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                base = node.value.id
                if base in imports and self._module_exists(imports[base].split(".")[0]):
                    m = resolve(imports[base])
                    if m is not None and not hasattr(m, node.attr):
                        missing.append(f"'{base}.{node.attr}' 없음 (환각 API)")

        missing = list(dict.fromkeys(missing))  # 순서 보존 dedup
        if missing:
            return self._result(Verdict.FAIL, 0.7, "환각/미존재 심볼: " + "; ".join(missing))
        if not imports and not from_imports:
            return self._result(Verdict.INCONCLUSIVE, 0.0, "검사할 외부 심볼 없음")
        return self._result(Verdict.PASS, 0.7, "참조 심볼 전부 존재 확인")

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _module_exists(top_level: str) -> bool:
        try:
            return importlib.util.find_spec(top_level) is not None
        except (ModuleNotFoundError, ValueError, ImportError):
            return False

    def _result(self, verdict: Verdict, reliability: float, detail: str) -> VerifierResult:
        return VerifierResult(
            tier=self.tier, verdict=verdict, reliability=reliability, detail=detail, locality=self.locality
        )
