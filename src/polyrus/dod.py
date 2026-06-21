from __future__ import annotations

import re
from typing import TYPE_CHECKING

from polyrus.types import DoD, LedgerItem, RiskLevel, Task

if TYPE_CHECKING:
    from polyrus.models import ModelClient

# 다(多)하위목표 분해용 마커: 번호목록 / 불릿 / 줄바꿈 / 접속어.
_SPLIT = re.compile(r"(?:^|\n)\s*(?:\d+[.)]\s+|[-*•]\s+)|;\s*|\n+|\s+그리고\s+")
_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


class DoDGenerator:
    """스펙 → 동결된 수용 기준. '정당성'의 첫 방어선 (검증기는 정확성만 잠근다).

    스펙-우선 원칙: 수용 테스트/프로퍼티는 *구현 전에* 작성·동결되고,
    구현 패스는 이를 수정할 수 없다 (굿하트 차단).

    *구조적* 분해(번호목록/불릿/접속어로 하위목표 쪼개기) + 동결은 LLM 없이 한다.
    model을 주면 스펙 프로즈에서 수용 테스트를 *합성*한다(#2 완전 해소: task.json을 손으로 안 써도 됨).
    합성된 테스트도 생성 *전에* 동결된다(굿하트). model 없으면 명시 테스트만 쓰고, 없으면 INCONCLUSIVE.
    """

    def __init__(self, model: ModelClient | None = None) -> None:
        self.model = model

    def decompose(self, task: Task) -> list[LedgerItem]:
        """요청을 하위 목표로 분해. 이미 항목이 있으면 그대로(동결만 보장)."""
        if task.items:
            for it in task.items:
                self._ensure_frozen(it.dod)
            return task.items

        goals = self._split_goals(task.request)
        return [
            LedgerItem(
                id=f"{task.id}-{i}",
                goal=goal,
                dod=self.derive_dod(goal),
                risk=RiskLevel.MEDIUM,
            )
            for i, goal in enumerate(goals)
        ]

    def derive_dod(
        self,
        spec: str,
        *,
        acceptance_tests: list[str] | None = None,
        properties: list[str] | None = None,
    ) -> DoD:
        """스펙 + (선택) 명시 수용 기준 → *동결된* DoD.

        동결(frozen=True)이 핵심 — 생성 패스가 수용 기준을 못 고치게 한다.
        acceptance_tests를 안 주고 model이 있으면 LLM이 합성한다. 둘 다 없으면 빈 채로 동결되고
        검증은 INCONCLUSIVE가 된다(수용 기준 없이는 '검증된 완료'를 주장하지 않는다 = 정직).
        """
        tests = list(acceptance_tests) if acceptance_tests else []
        if not tests and self.model is not None:
            tests = self.synthesize_acceptance_tests(spec)
        return DoD(
            spec=spec.strip(),
            acceptance_tests=tests,
            properties=list(properties or []),
            frozen=True,
        )

    def synthesize_acceptance_tests(self, spec: str, *, module: str = "solution") -> list[str]:
        """LLM으로 스펙 → pytest 수용 테스트 합성(생성 전 동결용). model 필수."""
        if self.model is None:
            raise ValueError("synthesize_acceptance_tests에는 model이 필요하다")
        prompt = (
            f"다음 명세에 대한 pytest 수용 테스트를 작성하라.\n"
            f"- 구현은 모듈 `{module}`에서 import한다(예: from {module} import ...).\n"
            f"- 정상 케이스 + 경계/엣지 케이스를 포함하라.\n"
            f"- 테스트 코드만 하나의 코드블록으로 출력하라.\n\n# 명세\n{spec}"
        )
        text = self.model.complete(
            prompt, system="너는 꼼꼼한 테스트 엔지니어다. 구현이 아니라 *테스트*를 쓴다.", temperature=0.2
        )
        blocks = _FENCE.findall(text)
        code = (max(blocks, key=len) if blocks else text).strip()
        # 실제 테스트로 보일 때만 채택(프로즈를 테스트로 오인 금지) → 아니면 빈 채(검증 불가=잔소리 금지).
        if "def test" not in code and "assert" not in code:
            return []
        return [code + "\n"]

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _split_goals(request: str) -> list[str]:
        parts = [p.strip() for p in _SPLIT.split(request) if p and p.strip()]
        return parts or [request.strip()]

    @staticmethod
    def _ensure_frozen(dod: DoD) -> None:
        # 검증 전 DoD는 동결돼 있어야 한다(굿하트). 미동결이면 동결로 고정.
        if not dod.frozen:
            dod.frozen = True
