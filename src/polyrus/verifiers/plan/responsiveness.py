"""응답성 검증 — '답을 했다 치고 넘어가는' 회피·변명·비답변을 결정적으로 잡는다.

두 방향(글로벌 CLAUDE.md '중단/회피 금지'의 코드화):
- 사용자 측 비답변: "몰라요·아무거나·알아서" — 답은 텍스트로 왔지만 *결정을 안 한 것*.
  → 조용히 넘어가지 말고(No-Silent-Stop) 회복 질문/선택지로 다시.
- 모델 측 변명: "제 변경 때문이 아닙니다·알려진 제한입니다·일단 ~했습니다" — 완료를 주장하나
  사실은 회피. → No-Pass를 *완료 주장*에 적용해 FAIL.

그리고 질문 자체의 난이도 가드: 우리가 던지는 질문이 어렵거나 답을 어렵게 만들면 안 된다
(비개발자 UX의 핵심). frame.py(프레임 의존도)와 상보적 — 저긴 '휘둘림', 여긴 '회피/난이도'.

전부 결정적(룰)이라 재현 가능하고, 휴리스틱이라 reliability는 정직하게 1 미만.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult

# 사용자 비답변(결정 회피) — '답은 왔는데 고른 게 없음'.
_NON_ANSWER = (
    "몰라", "모르겠", "아무거나", "아무렇게", "알아서", "상관없", "다 좋", "아무 거나",
    "글쎄", "그냥 알아서", "패스", "스킵", "건너", "나중에", "딱히",
    "whatever", "anything", "idk", "dunno", "don't know", "dont care", "no preference",
)
# 모델 변명/책임회피 — 완료를 주장하나 회피. 강신호(오탐 적음)와 약신호(흔한 말, 문맥의존) 분리.
_EXCUSE_STRONG = (
    "변경 때문이 아", "변경 탓이 아", "제 탓이 아", "내 탓이 아",  # 활용형(아니/아닙) 모두 매칭
    "알려진 제한", "어쩔 수 없", "환경 문제", "환경 탓", "그쪽 문제",
    "not caused by my changes", "known limitation", "out of scope",
    "should i continue", "계속할까요", "진행할까요",
)
_EXCUSE_WEAK = ("원래 그렇", "일단 ", "우선 ", "추후", "나중에 보완", "대략", "임시로", "as expected")
_EXCUSE = _EXCUSE_STRONG + _EXCUSE_WEAK
# 헤지(약한 신호 — 단독으론 약함, 변명과 겹치면 가중).
_HEDGE = ("인 것 같", "아마", "확실하지 않", "추정", "maybe", "probably", "i think")


@dataclass
class EvasionReport:
    is_evasive: bool
    flags: list[str] = field(default_factory=list)
    kind: str = ""  # 'non_answer' | 'excuse' | ''


def check_evasion(text: str, *, mode: str = "any", strict: bool = False) -> EvasionReport:
    """회피/변명/비답변 결정적 탐지. mode: 'user'(비답변)·'model'(변명)·'any'(둘 다).

    strict=True면 *강신호 변명만* 본다(흔한 말 '일단/우선' 제외) — stop-hook처럼 오탐이 비싼 곳용.
    """
    low = text.lower().strip()
    if not low:
        return EvasionReport(True, ["빈 답변"], "non_answer")
    flags: list[str] = []
    kind = ""

    if mode in ("user", "any"):
        hits = [p for p in _NON_ANSWER if p in low]
        if hits:
            flags += [f"비답변: {h}" for h in hits]
            kind = "non_answer"

    if mode in ("model", "any"):
        excuse_set = _EXCUSE_STRONG if strict else _EXCUSE
        ex = [p for p in excuse_set if p in low]
        if ex:
            flags += [f"변명/회피: {h.strip()}" for h in ex]
            kind = "excuse" if not kind else "non_answer+excuse"
        hedges = [p for p in _HEDGE if p in low]
        if ex and hedges:  # 변명 + 헤지 동반 = 확신 없는 회피
            flags.append(f"헤지 동반: {hedges[0]}")

    return EvasionReport(bool(flags), flags, kind)


def answer_responsive(answer: str) -> bool:
    """사용자 답이 *실제 결정*인가 — 비거나 회피면 False(조용히 넘어가지 마라)."""
    return not check_evasion(answer, mode="user").is_evasive


def completion_excuse(response: str) -> EvasionReport:
    """완료 선언 텍스트에서 *강신호 변명만* 탐지(오탐 비싼 stop-hook용).

    응답이 없으면(검사 불가) 변명 아님 — *부재*를 변명으로 막지 않는다(비답변 규칙과 분리).
    """
    if not response.strip():
        return EvasionReport(False, [], "")
    return check_evasion(response, mode="model", strict=True)


# ── 질문 난이도 가드: '질문이 어렵거나 답을 어렵게 만들면 안 된다' ──────────────────────
_JARGON = (
    "cta", "히어로", "hero", "lcp", "cls", "seo", "oklch", "wcag", "퍼널", "funnel",
    "컨버전", "와이어프레임", "페르소나", "스키마", "api 라우트", "ssr", "csr",
)
_OPTION_HINT = ("/", "·", "또는", "vs", "쪽인가요", "골라", "선택")
_EXAMPLE_HINT = ("예:", "예：", "예시", "(예", "e.g.")


@dataclass
class EaseReport:
    easy: bool
    issues: list[str] = field(default_factory=list)


def question_ease(question: str, *, max_len: int = 90) -> EaseReport:
    """질문이 비개발자가 쉽게 답할 수 있는가. 어려우면 issues로 표면화.

    원칙: 전문용어는 *선택지·예시를 동반하면* OK(용어를 몰라도 고를 수 있으니). 동반 없으면 거부.
    답을 어렵게 만드는 것(장황·복합질문·예시 없는 개방형)을 막는다.
    """
    q = re.sub(r"[*_`]", "", question).strip()
    issues: list[str] = []
    if not q:
        return EaseReport(False, ["빈 질문"])

    if len(q) > max_len:
        issues.append(f"너무 김({len(q)}자 > {max_len})")
    if q.count("?") + q.count("？") >= 2:
        issues.append("복합 질문(한 번에 여러 개를 물음)")

    low = q.lower()
    has_option = any(h in low for h in _OPTION_HINT)
    has_example = any(h in low for h in _EXAMPLE_HINT)
    jargon = [j for j in _JARGON if j in low]
    if jargon and not (has_option or has_example):
        issues.append(f"전문용어인데 선택지/예시 없음: {', '.join(jargon)}")

    return EaseReport(not issues, issues)


# ── Verifier 프로토콜 구현 ─────────────────────────────────────────────────────
class _PlanVerifier:
    tier: Tier = Tier.T1_EXECUTION
    name: str = "plan"
    locality: Locality = Locality.LOCAL
    kind: str = "plan"
    reliability: float = 0.6

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == self.kind

    def _result(self, verdict: Verdict, detail: str, evidence: list[str] | None = None) -> VerifierResult:
        return VerifierResult(self.tier, verdict, self.reliability, detail, evidence or [], self.locality)


class EvasionVerifier(_PlanVerifier):
    """완료 주장이 회피/변명인지 — No-Pass를 *주장*에 적용. 모델이 '됐다'며 둘러대면 FAIL."""

    name = "evasion"
    kind = "completion"
    reliability = 0.7

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        rep = check_evasion(claim.content, mode="model")
        if rep.is_evasive:
            return self._result(Verdict.FAIL, f"회피/변명 감지({rep.kind})", rep.flags)
        return self._result(Verdict.PASS, "변명 없음 — 직답")


class QuestionEaseVerifier(_PlanVerifier):
    """선제질문이 비개발자가 쉽게 답할 수 있는가(난이도 가드)."""

    name = "question_ease"
    kind = "question"
    reliability = 0.7

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        rep = question_ease(claim.content)
        if rep.easy:
            return self._result(Verdict.PASS, "쉬운 질문")
        return self._result(Verdict.FAIL, "답하기 어려운 질문", rep.issues)
