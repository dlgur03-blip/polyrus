"""Session — 통합 실행 파사드. 흩어진 코어를 하나의 경로로 묶는다.

Dials(4.3) 하나가 Budget·HarnessConfig·검증 깊이를 파생하고, 거기에 컨텍스트 엔진(v2 B)·
이해검증(5.5)·코퍼스 보정(5.4)·멱등 액션을 끼워 No-Pass 루프를 돈다. '추천 다이얼 → 한 실행'.

위계(CSS 캐스케이드): 프로젝트 기본값 → 작업별 추천(dials.recommend) → 사용자 override.
"""
from __future__ import annotations

from polyrus.calibration import compute_reliability
from polyrus.context import ContextEngine, ContextItem, ContextStore
from polyrus.core.arms import Arms
from polyrus.dials import Dials, preset
from polyrus.escalation import Escalator
from polyrus.harness import Harness
from polyrus.memory import SkillStore
from polyrus.models import ModelClient
from polyrus.scoring import ngram_score
from polyrus.store import Store
from polyrus.types import LoopResult, Task
from polyrus.understanding import Understander
from polyrus.verifiers.registry import default_code_bank, full_code_bank


class Session:
    """한 작업을 모든 레이어를 켜고 실행. 컴포넌트는 dials에서 파생되거나 주입된다."""

    def __init__(
        self,
        model: ModelClient,
        *,
        dials: Dials | None = None,
        store: Store | None = None,
        context_engine: ContextEngine | None = None,
        escalator: Escalator | None = None,
        enable_understanding: bool = False,
        skills: SkillStore | None = None,
        bank: object | None = None,
        arms_kind: str = "code",
        arms_system: str | None = None,
        arms_transform: object | None = None,
        arms_output_hint: str | None = None,
        preflight_tools: list[str] | None = None,
    ) -> None:
        self.model = model
        self.dials = dials or Dials()
        self.store = store
        self.context_engine = context_engine
        self.escalator = escalator or Escalator()
        self.enable_understanding = enable_understanding
        self.skills = skills  # 검증된 스킬 메모리(자기개선). None이면 학습 안 함.
        # 도메인 비종속 빌드: bank/arms_*를 주면 코드 아닌 산출물(웹 카피 등)을 생성·검증한다.
        self.bank = bank
        self.arms_kind = arms_kind
        self.arms_system = arms_system
        self.arms_transform = arms_transform
        self.arms_output_hint = arms_output_hint
        # 초보자 온보딩: 빌드 전 이 도구들이 PATH에 있는지 검사. 없으면 ENV_BLOCKED로 안내.
        self.preflight_tools = preflight_tools or []

    @classmethod
    def from_preset(cls, model: ModelClient, preset_name: str, **kw: object) -> Session:
        return cls(model, dials=preset(preset_name), **kw)  # type: ignore[arg-type]

    @classmethod
    def for_homepage_build(cls, model: ModelClient, **kw: object) -> Session:
        """홈페이지 빌드 위임용 — 결정적 홈페이지 뱅크로 빌더 산출(히어로 카피 등)을 검증.

        '빌딩은 위임, 검증은 우리'(마누스 회피)의 실행 경로: 모델이 카피를 쓰고, AI-slop 등
        결정적 검증기가 production-grade로 잠근다.
        """
        from polyrus.verifiers.registry import default_homepage_bank

        from polyrus.skeleton import HOMEPAGE

        return cls(
            model,
            bank=default_homepage_bank(),
            arms_kind="copy",
            arms_system="너는 사람 냄새 나는 웹 카피라이터다. AI 티(클리셰·이모지 남발) 없이 구체적으로 써라.",
            arms_transform=lambda t: t.strip(),
            arms_output_hint="히어로 카피 한두 문장만 출력하라(설명·머리말 없이).",
            preflight_tools=kw.pop("preflight_tools", list(HOMEPAGE.requires)),  # type: ignore[arg-type]
            **kw,  # type: ignore[arg-type]
        )

    def run(self, task: Task, *, resume: bool = False) -> LoopResult:
        # 초보자 온보딩 게이트: 빌드 전 기본 도구가 없으면 *크래시 말고* ENV_BLOCKED로 안내.
        # (환경 미비는 코드 FAIL도 변명도 아니다 — 결정적 which 검사로 셋을 가른다.)
        if self.preflight_tools:
            blocked = self._preflight_gate(task)
            if blocked is not None:
                return blocked
        # 절제된 자율(자율축, 하트비트 없음): 끊겼다 다시 돌리면 *이미 검증된 항목은 건너뛴다*.
        # 긴 다단계 작업의 체크포인트 — store에 남은 완료 상태를 읽어 재개.
        if resume and self.store is not None:
            done = {r["item_id"] for r in self.store.items(task.id) if r["closed"]}
            for item in task.items:
                if item.id in done:
                    item.closed = True
        budget = self.dials.to_budget()
        cfg = self.dials.to_harness_config()
        # 주입 뱅크가 있으면 그걸(도메인 빌드), 없으면 철저 다이얼로 코드 뱅크 깊이 선택.
        if self.bank is not None:
            bank = self.bank
        else:
            bank = full_code_bank() if self.dials.thoroughness >= 0.7 else default_code_bank()
        # 자기개선: 검증된 스킬을 recall해 컨텍스트로 주입(쓸수록 똑똑해짐, 검증된 것만).
        context_engine = self._context_with_skills(task)
        arms = Arms(
            self.model,
            max_workers=max(1, budget.max_arms),
            context_engine=context_engine,
            kind=self.arms_kind,
            system=self.arms_system,
            transform=self.arms_transform,  # type: ignore[arg-type]
            output_hint=self.arms_output_hint,
        )
        understander = (
            Understander(self.model, conf_threshold=self.dials.ambiguity_threshold)
            if self.enable_understanding
            else None
        )
        # 코퍼스가 있으면 그 보정 곡선을 확신도에 적용(5.4 루프).
        reliability = compute_reliability(self.store.corpus_rows()) if self.store else None
        harness = Harness(
            arms,
            bank,
            self.escalator,
            cfg=cfg,
            understander=understander,
            reliability_map=reliability,
        )
        result = harness.run(task, budget=budget, store=self.store)
        self._learn(result)  # 검증 통과한 것만 스킬로 기록(자기개선)
        return result

    def _preflight_gate(self, task: Task) -> LoopResult | None:
        """필요 도구 검사 → 빠지면 모든 항목을 환경미비로 보류하고 ENV_BLOCKED 반환(안내 게이트)."""
        from polyrus.preflight import preflight_check
        from polyrus.types import Termination

        report = preflight_check(self.preflight_tools)
        if report.ok:
            return None
        reason = report.popup  # 초보자 팝업(친절 안내) — 스택트레이스 아님
        for item in task.items:
            item.escalated = True
            item.escalation_reason = reason
            self.escalator.raise_to_human(item, reason)
        return LoopResult(termination=Termination.ENV_BLOCKED, task_id=task.id, items=task.items)

    def _context_with_skills(self, task: Task) -> ContextEngine | None:
        """기존 컨텍스트 + recall된 검증 스킬을 합쳐 Arms에 줄 엔진을 만든다."""
        if self.skills is None:
            return self.context_engine
        recalled = []
        for item in task.items:
            recalled.extend(self.skills.recall(item.goal, k=2))
        if not recalled:
            return self.context_engine
        cs = ContextStore()
        for s in recalled:
            cs.add(ContextItem(f"skill-{s.id}", f"이전 검증된 해법({s.goal}):\n{s.solution}", kind="memory"))
        return ContextEngine(cs, scorer=ngram_score)

    def _learn(self, result: LoopResult) -> None:
        if self.skills is None:
            return
        for item in result.items:
            if item.closed and item.solution:  # *검증된* 것만 — 틀린 걸 학습하지 않는다
                self.skills.record(kind="code", goal=item.goal, solution=item.solution,
                                   confidence=item.confidence)
