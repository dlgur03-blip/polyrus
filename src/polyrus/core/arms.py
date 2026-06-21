from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from polyrus.context import ContextEngine
from polyrus.models import ModelClient
from polyrus.types import Claim, LedgerItem

_CODE_SYSTEM = "너는 정확한 코드를 쓰는 엔지니어다. 요청된 모듈 코드만 코드블록으로 출력하라."

# 서로 다른 페르소나·온도로 팔을 *탈상관*시킨다(같은 식으로 틀리지 않게).
_PERSONAS = [
    ("간결하고 표준적으로. 표준 라이브러리 우선.", 0.2),
    ("엣지 케이스를 방어적으로 다뤄라(빈 입력·경계값).", 0.5),
    ("가장 단순한 정답을 노려라. 과설계 금지.", 0.7),
    ("성능보다 명확성. 읽기 쉬운 구현.", 0.9),
]

_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str:
    """모델 응답에서 코드 블록을 추출. 펜스가 있으면 그 안, 없으면 원문."""
    blocks = _FENCE.findall(text)
    if blocks:
        return max(blocks, key=len).strip() + "\n"
    return text.strip() + "\n"


class Arms:
    """추론 코어(문어). 상관관계를 분리한 병렬 팔 + 콜드스타트 팔.

    앵커링 연쇄를 막기 위해 각 팔은 서로의 결과를 *생성 중* 보지 않는다(독립 호출).
    콜드스타트 팔(arm 0)은 이전 맥락·실패 피드백을 무시하고 *스펙만으로* 재유도한다 —
    워밍 사슬과 결론이 갈리면 그 자체가 위험 신호(5.5 모호성 감지의 입력).
    """

    def __init__(
        self,
        model: ModelClient,
        *,
        max_workers: int = 4,
        module: str = "solution.py",
        context_engine: ContextEngine | None = None,
        context_tokens: int = 1500,
        kind: str = "code",
        system: str | None = None,
        transform: Callable[[str], str] | None = None,
        output_hint: str | None = None,
    ) -> None:
        self.model = model
        self.max_workers = max_workers
        self.module = module
        self.context_engine = context_engine      # 컨텍스트 엔지니어링(v2 B). None이면 미주입.
        self.context_tokens = context_tokens
        # 도메인 비종속: kind/system/transform을 갈아끼우면 코드 아닌 산출물(웹 카피 등)도 생성.
        self.kind = kind
        self.system = system or _CODE_SYSTEM
        self.transform = transform or extract_code
        self.output_hint = output_hint  # None이면 코드 출력 지시(기본). 비코드면 이걸로 교체.
        self._feedback: dict[str, list[str]] = {}  # item_id → 누적 실패 블로커(M4)

    def generate(self, item: LedgerItem, k: int) -> list[Claim]:
        feedback = self._feedback.get(item.id, [])
        n = max(1, k)
        # arm 0 = 콜드스타트 — 단, 팔이 2개 이상일 때만(단일 팔은 워밍: 컨텍스트·피드백 사용).
        specs = [(i, i == 0 and n >= 2, feedback) for i in range(n)]

        def _one(arg: tuple[int, bool, list[str]]) -> Claim:
            idx, cold, fb = arg
            prompt = self._build_prompt(item, cold=cold, feedback=fb, arm=idx)
            persona, temp = _PERSONAS[idx % len(_PERSONAS)]
            text = self.model.complete(
                prompt,
                system=self.system,
                temperature=0.0 if cold else temp,
            )
            return Claim(
                id=f"{item.id}-arm{idx}{'c' if cold else ''}",
                content=self.transform(text),
                kind=self.kind,
                meta={"module": self.module, "arm": idx, "cold": cold},
            )

        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            return list(ex.map(_one, specs))

    def select(self, candidates: list[Claim]) -> Claim:
        # 조정 뇌는 단일 선택을 늦춘다(이견 보존). 검증 입력용으로 콜드스타트가 아닌
        # 첫 후보를 기본 제출 — 검증기 판정이 진짜 선택자다.
        warm = [c for c in candidates if not c.meta.get("cold")]
        return (warm or candidates)[0]

    def diversify(self, item: LedgerItem, blocker: str) -> None:
        # M4: 실패 사유를 누적해 다음 시도의 접근을 바꾼다(콜드스타트 팔은 이를 무시).
        self._feedback.setdefault(item.id, []).append(blocker)

    # ── 프롬프트 ───────────────────────────────────────────────────────────────
    def _build_prompt(self, item: LedgerItem, *, cold: bool, feedback: list[str], arm: int) -> str:
        dod = item.dod
        parts = [
            f"# 목표\n{item.goal}",
            f"\n# 명세\n{dod.spec}",
        ]
        if dod.acceptance_tests:
            joined = "\n\n".join(dod.acceptance_tests)
            parts.append(f"\n# 통과해야 할 수용 테스트(동결)\n```python\n{joined}\n```")
        if dod.properties:
            parts.append("\n# 프로퍼티\n" + "\n".join(f"- {p}" for p in dod.properties))
        # 컨텍스트 주입은 워밍 팔만 — 콜드스타트는 스펙만으로 재유도(Isolate, 앵커링 회피).
        if not cold and self.context_engine is not None:
            assembled = self.context_engine.assemble(item.goal, max_tokens=self.context_tokens)
            if assembled.items:
                ctx = "\n".join(f"- {it.text}" for it in assembled.items)
                note = f" (인젝션 {len(assembled.dropped_injections)}건 차단됨)" if assembled.dropped_injections else ""
                parts.insert(2, f"\n# 관련 컨텍스트{note}\n{ctx}")
        hint = self.output_hint or f"모듈 `{self.module}`의 전체 코드만 하나의 코드블록으로."
        parts.append(f"\n# 출력\n{hint}")
        if not cold and feedback:
            # 워밍 팔만 실패 피드백을 본다(콜드스타트는 앵커링 회피).
            fb = "\n".join(f"- {b}" for b in feedback[-3:])
            parts.append(f"\n# 이전 시도 실패(다르게 접근하라)\n{fb}")
        return "".join(parts)
