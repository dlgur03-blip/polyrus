"""이해 검증 (5.5) — 출력 검증의 *상류*. '맞는 질문에 답하고 있나'.

쓰레기 스펙을 완벽히 구현하면 '검증됐지만 틀림'이다. 그래서 출력 검증 앞에 이해 검증을 둔다.
네 메커니즘: ① DoD가 곧 이해 산물(dod.py) ② 가정 표면화 ③ 해석/실행 확신도 분리
④ 팔의 *해석* divergence = 모호성 감지(공짜 신호). 합의면 조용히 진행, 갈리면 게이트.

매끄러움: 매번 묻지 말고 해석 확신 낮거나 팔이 갈릴 때만, '막는 질문'이 아니라
'X로 읽고 진행 중 — 아니면 고쳐줘'(회복 가능한 가정)로.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from polyrus.models import ModelClient
from polyrus.types import LedgerItem


@dataclass
class Interpretation:
    """한 팔이 *문제를 어떻게 이해했나* (해답이 아니라 해석)."""

    summary: str            # 이 작업이 요구하는 것 한 문장
    assumptions: list[str]  # 명세에 없어 메운 빈칸
    confidence: float       # 해석 확신(실행 확신과 다름)


@dataclass
class Understanding:
    interpretation_confidence: float   # 해석 확신(이해가 맞나) — 실행 확신과 분리
    ambiguous: bool
    assumptions: list[str]
    interpretations: list[Interpretation] = field(default_factory=list)
    recovery_question: str = ""        # 모호할 때만: 회복 가능한 가정


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def interpret_task(
    model: ModelClient, *, goal: str, spec: str, k: int = 3, temps: tuple[float, ...] = (0.2, 0.6, 0.9)
) -> list[Interpretation]:
    """k개의 팔에게 *구현 말고 해석*을 시킨다. 팔마다 다른 온도로 탈상관."""
    out: list[Interpretation] = []
    for i in range(max(1, k)):
        prompt = (
            "다음 작업을 *구현하지 말고*, 네가 이해한 바만 JSON으로 답하라:\n"
            '{"interpretation":"이 작업이 요구하는 것 한 문장",'
            '"assumptions":["명세에 없어 가정한 것"],"confidence":0~1}\n\n'
            f"# 목표\n{goal}\n\n# 명세\n{spec}"
        )
        text = model.complete(
            prompt,
            system="너는 요구사항 분석가다. 모호하면 추측하지 말고 가정을 드러내라.",
            temperature=temps[i % len(temps)],
        )
        d = _parse_json(text)
        out.append(
            Interpretation(
                summary=str(d.get("interpretation", "")).strip(),
                assumptions=[str(a) for a in d.get("assumptions", []) if a],
                confidence=float(d.get("confidence", 0.5)),
            )
        )
    return out


def assess_understanding(
    interpretations: list[Interpretation], *, conf_threshold: float = 0.6
) -> Understanding:
    """해석들을 종합 — 갈리거나(divergence) 확신 낮으면 모호로 판정하고 회복 질문을 만든다."""
    confs = [i.confidence for i in interpretations] or [0.0]
    interp_conf = sum(confs) / len(confs)

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s.lower()).strip()

    distinct = {norm(i.summary) for i in interpretations if i.summary.strip()}
    diverged = len(distinct) > 1
    low_conf = interp_conf < conf_threshold
    ambiguous = diverged or low_conf

    assumptions = sorted({a for i in interpretations for a in i.assumptions})
    primary = max(interpretations, key=lambda i: i.confidence).summary if interpretations else ""
    recovery = ""
    if ambiguous:
        why = "팔의 해석이 갈림" if diverged else "해석 확신 낮음"
        recovery = f"'{primary}'로 읽고 진행 중 — 아니면 고쳐줘 ({why})"
    return Understanding(interp_conf, ambiguous, assumptions, interpretations, recovery)


class Understander:
    """이해 게이트. 생성/검증 *전에* 해석을 점검 — 팔이 갈리면 게이트, 합의면 조용히 진행."""

    def __init__(self, model: ModelClient, *, k: int = 3, conf_threshold: float = 0.6) -> None:
        self.model = model
        self.k = k
        self.conf_threshold = conf_threshold

    def assess(self, item: LedgerItem) -> Understanding:
        interps = interpret_task(self.model, goal=item.goal, spec=item.dod.spec, k=self.k)
        return assess_understanding(interps, conf_threshold=self.conf_threshold)
