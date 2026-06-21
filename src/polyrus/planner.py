"""선제질문 기획 엔진 — 뼈대를 걸으며 '질문은 우리가, 사용자는 대답만'.

흐름(삼분 프레임):
  ASK     → AnswerProvider로 사용자에게 묻는다(모호하면 회복 질문으로 1회 재시도 = understanding 사상).
  DEFAULT → 검증 스킬 위키에서 전문가 지식을 당겨 채운다(없으면 의견형 스택 디폴트로 폴백).
  VERIFY  → 각 단계 합격기준을 결정적 검증기로 집행한다(§4-3).

산출: PlanResult — 사용자가 읽을 brief + *동결된* DoD + No-Pass 루프로 넘길 Task.
UI 비종속: provider만 갈아끼우면 CLI(now)·웹(later) 동일 엔진.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from polyrus.dod import DoDGenerator
from polyrus.memory import SkillStore
from polyrus.skeleton import Classification, Skeleton, SkeletonStep
from polyrus.types import (
    AggregateVerdict,
    Claim,
    DoD,
    LedgerItem,
    RiskLevel,
    Task,
)
from polyrus.verifiers.plan import (
    AccentCountVerifier,
    AISlopVerifier,
    ContrastVerifier,
    EvasionVerifier,
    FrameAlignmentVerifier,
    answer_responsive,
    check_evasion,
    question_ease,
)


@runtime_checkable
class AnswerProvider(Protocol):
    """선제질문의 대답원(源). CLI/웹/테스트가 각자 구현. recovery=True는 회복 질문 재시도."""

    def ask(self, step: SkeletonStep, *, recovery: bool = False) -> str: ...


@dataclass
class ScriptedAnswers:
    """테스트/비대화용 대답원. output_key → 답. clarifications는 회복 재시도 답."""

    answers: dict[str, str] = field(default_factory=dict)
    clarifications: dict[str, str] = field(default_factory=dict)
    asked_log: list[str] = field(default_factory=list)

    def ask(self, step: SkeletonStep, *, recovery: bool = False) -> str:
        self.asked_log.append(f"{step.output_key}{'*' if recovery else ''}")
        if recovery and step.output_key in self.clarifications:
            return self.clarifications[step.output_key]
        return self.answers.get(step.output_key, "")


@dataclass
class InteractiveAnswers:
    """대화형(CLI) 대답원 — stdin에서 읽는다. 웹은 다른 provider로 갈아끼우면 됨.

    prompt_fn/out_fn은 None이면 호출 시점에 builtins input/print를 쓴다(monkeypatch 가능).
    """

    prompt_fn: object = None
    out_fn: object = None

    def ask(self, step: SkeletonStep, *, recovery: bool = False) -> str:
        say = self.out_fn or print
        read = self.prompt_fn or input
        q = step.question or step.micro_question or step.title
        if recovery:
            # 막는 질문이 아니라 회복 질문 — 선택지/예시로 *더 쉽게* 다시 묻는다.
            say(f"  ↻ 그 답으론 진행이 어려워요. 하나만 골라 주세요 — {q}")  # type: ignore[operator]
        else:
            say(f"\n[{step.title}] {q}")  # type: ignore[operator]
        return str(read("  > ")).strip()  # type: ignore[operator]


@dataclass
class PlanStepRecord:
    step: SkeletonStep
    value: str                    # ASK=답 / DEFAULT=가이드 요약 / 미해결=''
    source: str                   # 'user' | 'wiki:<id>' | 'stack-default' | 'fallback' | 'unresolved'
    skill_id: int | None = None
    blocker: str = ""             # 모호·미해결 시 채워짐(No-Silent-Stop)
    report: object | None = None  # 조사 단계: ReferenceReport(읽어서 검증한 출처)
    taste: str = ""               # DEFAULT 단계의 micro_question 취향(원본 답)


@dataclass
class PlanResult:
    domain: str
    records: list[PlanStepRecord]
    brief: str
    dod: DoD
    stack_defaults: dict[str, str]
    question_issues: list[str] = field(default_factory=list)  # 난이도 가드: 어려운 질문 표면화

    @property
    def answers(self) -> dict[str, str]:
        return {r.step.output_key: r.value for r in self.records if r.step.classification is Classification.ASK}

    @property
    def defaults(self) -> dict[str, str]:
        return {
            r.step.output_key: r.value
            for r in self.records
            if r.step.classification is Classification.DEFAULT
        }

    @property
    def asked(self) -> int:
        return sum(1 for r in self.records if r.source == "user")

    @property
    def defaulted(self) -> int:
        return sum(1 for r in self.records if r.source.startswith("wiki") or r.source in ("stack-default", "fallback"))

    @property
    def blockers(self) -> list[str]:
        return [f"{r.step.title}: {r.blocker}" for r in self.records if r.blocker]

    @property
    def reference_report(self) -> object | None:
        for r in self.records:
            if r.report is not None:
                return r.report
        return None

    def record_for(self, step_id: str) -> PlanStepRecord | None:
        return next((r for r in self.records if r.step.id == step_id), None)

    def digest_config(self) -> object:
        """digest 기획 결과 → 실행 설정(DigestConfig). 스케줄은 cron으로, 채널은 정규화."""
        from polyrus.digest import DigestConfig
        from polyrus.verifiers.plan import detect_channel, parse_schedule

        a = self.answers
        crit = self.record_for("criteria")
        length = self.record_for("length")
        return DigestConfig(
            source=a.get("source", ""),
            criteria=(crit.taste if crit else "") or "스타 급상승",
            schedule_cron=parse_schedule(a.get("schedule", "")) or "",
            channel=detect_channel(a.get("channel", "")) or "",
            length=(length.taste if length else "") or "짧게",
        )

    def to_task(self, task_id: str = "plan-0") -> Task:
        """동결 DoD를 단일 빌드 항목으로 싸서 No-Pass 루프(Session.run)에 넘긴다."""
        item = LedgerItem(
            id=f"{task_id}-build",
            goal=f"{self.domain} 구현 (선제질문 기획 결과)",
            dod=self.dod,
            risk=RiskLevel.MEDIUM,
        )
        return Task(id=task_id, request=self.brief, items=[item])

    def verify(self, draft: dict[str, object]) -> AggregateVerdict:
        """결정적 검증기로 초안을 집행(§4-3). draft 키: copy/palette/accent_count/sections/completion.

        completion(빌더의 '완료' 주장)이 있으면 회피/변명인지도 검사 — No-Pass를 *주장*에 적용.
        """
        results = []
        completion = str(draft.get("completion", ""))
        if completion:
            results.append(
                EvasionVerifier().verify(Claim("done", completion, kind="completion"), self.dod)
            )
        report = self.reference_report
        if report is not None:
            from polyrus.research import ReferenceProvenanceVerifier

            c = Claim("ref", "", kind="reference", meta={"report": report})
            results.append(ReferenceProvenanceVerifier().verify(c, self.dod))
        # 자동화 도메인: 스케줄·전달채널 답을 결정적으로 검증(cron 파싱·채널 설정).
        sched = self.answers.get("schedule")
        if sched:
            from polyrus.verifiers.plan import ScheduleVerifier

            results.append(ScheduleVerifier().verify(Claim("sched", sched, kind="schedule"), self.dod))
        chan = self.answers.get("channel")
        if chan:
            from polyrus.verifiers.plan import ChannelVerifier

            results.append(ChannelVerifier().verify(Claim("chan", chan, kind="channel"), self.dod))
        copy = str(draft.get("copy", ""))
        if copy:
            results.append(AISlopVerifier().verify(Claim("copy", copy, kind="copy"), self.dod))
        palette = str(draft.get("palette", ""))
        if palette:
            results.append(ContrastVerifier().verify(Claim("palette", palette, kind="palette"), self.dod))
        if "accent_count" in draft:
            c = Claim("accent", "", kind="accent", meta={"count": int(draft["accent_count"])})  # type: ignore[arg-type]
            results.append(AccentCountVerifier().verify(c, self.dod))
        if "sections" in draft:
            c = Claim(
                "frame", "", kind="frame",
                meta={"goal_action": self.answers.get("goal_action", ""), "sections": draft["sections"]},
            )
            results.append(FrameAlignmentVerifier().verify(c, self.dod))
        return AggregateVerdict(results=results)


def _unresponsive(answer: str) -> bool:
    """답이 *실제 결정*이 아니면 True → 회복 질문. 비었거나(understanding) 회피(몰라/아무거나).

    '답을 했다 치고 넘어가는' 사고를 막는다 — 텍스트가 와도 결정이 없으면 미응답으로 취급.
    """
    return len(answer.strip()) < 2 or not answer_responsive(answer)


def audit_questions(skeleton: Skeleton) -> list[str]:
    """뼈대의 모든 질문이 비개발자가 쉽게 답할 수 있는지 감사(난이도 가드).

    '질문이 어렵거나 답을 어렵게 만들면 안 된다'를 설계 시점에 잡는다. 반환: 문제 목록(빈=합격).
    """
    issues: list[str] = []
    for step in skeleton.steps:
        for q in (step.question, step.micro_question):
            if not q:
                continue
            rep = question_ease(q)
            if not rep.easy:
                issues.append(f"[{step.id}] {q!r}: {', '.join(rep.issues)}")
    return issues


class ProactivePlanner:
    """뼈대 + 위키 → 선제질문을 진행해 동결 기획을 만든다."""

    def __init__(
        self,
        skeleton: Skeleton,
        *,
        wiki: SkillStore | None = None,
        researcher: object | None = None,
        searcher: object | None = None,
        scope_min_weight: float = 0.0,
    ) -> None:
        # 비례 원칙: 가벼운 스코프면 저-weight 단계를 미리 잘라낸다(안 볼 답은 안 묻는다).
        self.skeleton = skeleton.scoped(min_weight=scope_min_weight) if scope_min_weight else skeleton
        self.wiki = wiki
        self.researcher = researcher  # ReferenceFetcher — 조사 단계에서 읽어서 검증(없으면 조사 생략)
        self.searcher = searcher      # Searcher — 업종만 줬을 때 후보 레퍼런스를 찾아옴(없으면 막다른길)

    def run(self, provider: AnswerProvider) -> PlanResult:
        records: list[PlanStepRecord] = []
        for step in self.skeleton.steps:
            if step.classification is Classification.ASK:
                records.append(self._do_ask(step, provider))
            elif step.classification is Classification.DEFAULT:
                records.append(self._do_default(step, provider))
            else:  # VERIFY — 합격기준은 DoD로 옮겨지며, 집행은 verify()/루프에서.
                records.append(PlanStepRecord(step, value=step.acceptance, source="verify"))

        brief = self._render_brief(records)
        dod = self._freeze_dod(brief, records)
        return PlanResult(
            self.skeleton.domain, records, brief, dod,
            dict(self.skeleton.stack_defaults),
            question_issues=audit_questions(self.skeleton),  # 난이도 가드 결과 동봉
        )

    # ── 단계 처리 ────────────────────────────────────────────────────────────
    def _do_ask(self, step: SkeletonStep, provider: AnswerProvider) -> PlanStepRecord:
        ans = provider.ask(step)
        if step.required and _unresponsive(ans):
            # 막는 질문이 아니라 회복 질문 — 1회 재시도(understanding 사상).
            ans = provider.ask(step, recovery=True)
        if step.required and _unresponsive(ans):
            # 회피/비답변을 *조용히 통과시키지 않는다*(No-Silent-Stop).
            why = "필수 답 미확보" if len(ans.strip()) < 2 else "회피/비답변(결정 없음)"
            blocker = f"{why} — 진행 전 응답 필요"
            ev = check_evasion(ans, mode="user")
            if ev.flags:
                blocker += f" ({ev.flags[0]})"
            return PlanStepRecord(step, value="", source="unresolved", blocker=blocker)
        ans = ans.strip()
        rec = PlanStepRecord(step, value=ans, source="user")
        if step.research and self.researcher is not None and ans:
            # 답을 받아 *읽어서 조사*한다(읽기우선·출처검증). 업종만이면 searcher로 찾아온다.
            from polyrus.research import research_references

            report = research_references(ans, self.researcher, searcher=self.searcher)  # type: ignore[arg-type]
            rec.report = report
            rec.value = f"{ans} → {report.summary}"
        return rec

    def _do_default(self, step: SkeletonStep, provider: AnswerProvider) -> PlanStepRecord:
        # 취향 한 개만 곁들이는 micro_question(있으면)은 묻되, 핵심 지식은 위키에서 채운다.
        taste = ""
        if step.micro_question:
            taste = provider.ask(step).strip()
        if self.wiki is not None and step.default_skill:
            skill = self.wiki.recall_default(step.default_skill)
            if skill is not None:
                summary = skill.solution.strip().splitlines()[0] if skill.solution.strip() else step.default_skill
                value = f"{summary} (취향:{taste})" if taste else summary
                return PlanStepRecord(step, value=value, source=f"wiki:{skill.id}", skill_id=skill.id, taste=taste)
        # 폴백: 의견형 스택 디폴트 또는 스킬명 자체(무-위키여도 진행은 멈추지 않는다).
        fallback = self.skeleton.stack_defaults.get(step.output_key) or step.default_skill or step.title
        src = "stack-default" if step.output_key in self.skeleton.stack_defaults else "fallback"
        value = f"{fallback} (취향:{taste})" if taste else fallback
        return PlanStepRecord(step, value=value, source=src, taste=taste)

    # ── 산출 ────────────────────────────────────────────────────────────────
    def _render_brief(self, records: list[PlanStepRecord]) -> str:
        lines = [f"# {self.skeleton.domain} 기획 (선제질문 결과)", ""]
        for r in records:
            if r.step.classification is Classification.VERIFY:
                continue
            tag = {"user": "묻음", "verify": "검증"}.get(r.source, "채움")
            val = r.value or f"⚠️ {r.blocker}"
            lines.append(f"- **{r.step.title}**[{tag}]: {val}")
        if self.skeleton.stack_defaults:
            lines.append("")
            lines.append("## 의견형 스택 디폴트")
            for k, v in self.skeleton.stack_defaults.items():
                lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def _freeze_dod(self, brief: str, records: list[PlanStepRecord]) -> DoD:
        # 합격기준(검증 항목)을 properties로, 검증기 이름을 함께 — 생성 전 동결(굿하트 차단).
        props = [
            f"[{r.step.verifier or 'n/a'}] {r.step.acceptance}"
            for r in records
            if r.step.acceptance
        ]
        return DoDGenerator().derive_dod(brief, properties=props)
