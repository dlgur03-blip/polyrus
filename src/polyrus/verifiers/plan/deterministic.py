"""결정적 기획 검증기 — 룰·파서로만 판정(무-LLM). §4-3 '결정적 검증 우선'의 실체.

여기 4개:
- AISlopVerifier   : 'AI 티' 결정적 탐지(클리셰·이모지 스팸·과한 대시/느낌표). design-review·/글쓰기 흡수본.
- ContrastVerifier : WCAG 대비비 계산(컬러 단계 합격기준).
- AccentCountVerifier : 포인트 요소 과용 차단(1~2개).
- FrameAlignmentVerifier : 섹션들이 단일 목적 행동에 정렬됐는지(목적 단계).

전부 결정적이지만 *휴리스틱*이라, 정직하게 reliability를 1 미만으로 보고한다
(base.py 계약: 약한 검증기의 PASS는 확신도 1이 아니다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult

# ── AI-slop 사전 (design-review의 'AI slop 패턴' + /글쓰기 'AI 균일성 깨기' 흡수) ──────────
# 마케팅·AI가 남발하는 공허한 상투구. 한국어 + 영어.
_CLICHES_KO = (
    "혁신적", "최첨단", "원스톱", "최적의 솔루션", "고객님의 니즈", "고객 만족", "최고의 품질",
    "합리적인 가격", "선도하는", "차별화된", "믿음과 신뢰", "새로운 패러다임", "완벽한 솔루션",
    "함께하겠습니다", "고객 중심", "세상을 바꾸", "미래를 선도",
)
_CLICHES_EN = (
    "in today's", "fast-paced world", "unlock the power", "unlock your", "seamless", "elevate your",
    "game-chang", "cutting-edge", "revolutioniz", "take it to the next level", "empower", "synergy",
    "world-class", "state-of-the-art", "best-in-class", "supercharge", "robust solution",
    "leverage", "delve into", "tapestry", "testament to",
)
# 마케팅 이모지(불릿/장식 스팸 신호).
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF✨✅✔➡]"
)


@dataclass
class SlopReport:
    score: float                         # 0=깨끗, 높을수록 AI 티
    flags: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.score < 1.0


def check_slop(text: str) -> SlopReport:
    """결정적 AI-slop 점수. 임계 미만이면 clean. (룰 기반 — 재현 가능.)"""
    low = text.lower()
    flags: list[str] = []
    score = 0.0

    hit_ko = [c for c in _CLICHES_KO if c in text]
    hit_en = [c for c in _CLICHES_EN if c in low]
    for c in hit_ko + hit_en:
        flags.append(f"클리셰: {c}")
    score += len(hit_ko) + len(hit_en)

    emoji_n = len(_EMOJI.findall(text))
    if emoji_n >= 3:
        flags.append(f"이모지 스팸: {emoji_n}개")
        score += (emoji_n - 2) * 0.5

    excl = text.count("!") + text.count("！")
    if excl >= 3:
        flags.append(f"느낌표 남발: {excl}개")
        score += (excl - 2) * 0.5

    dashes = text.count("—") + text.count(" - ")
    if dashes >= 4:
        flags.append(f"대시 남발: {dashes}개")
        score += (dashes - 3) * 0.3

    return SlopReport(score=round(score, 2), flags=flags)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 상대명도 대비비(1~21). 결정적 계산. 입력은 '#rrggbb'."""

    def _lin(hex_color: str) -> float:
        h = hex_color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))

        def _c(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * _c(r) + 0.7152 * _c(g) + 0.0722 * _c(b)

    l1, l2 = _lin(fg), _lin(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


def accent_count_ok(n: int, *, limit: int = 2) -> bool:
    """포인트 요소 과용 차단(1~2개). 결정적."""
    return 1 <= n <= limit


def frame_alignment(goal_action: str, sections: list[str], *, threshold: float = 0.5) -> float:
    """섹션들이 단일 목적 행동을 향하는 비율(0~1). 목적 토큰을 언급/유도하는 섹션 비율.

    결정적·휴리스틱: 영합(사용자 환심)으로 목적과 무관한 섹션이 불어나는 걸 잡는다(frame.py 사상).
    """
    if not sections:
        return 0.0
    tokens = [t for t in re.split(r"\s+", goal_action.strip()) if len(t) >= 2]
    if not tokens:
        return 1.0
    aligned = sum(1 for s in sections if any(t in s for t in tokens))
    return round(aligned / len(sections), 2)


# ── Verifier 프로토콜 구현 (verifiers/base.py 계약) ───────────────────────────────
class _PlanVerifier:
    """기획 검증기 공통: 결정적·LOCAL. reliability는 휴리스틱이라 1 미만으로 정직 보고."""

    tier: Tier = Tier.T1_EXECUTION  # 결정적·무-LLM 부류
    name: str = "plan"
    locality: Locality = Locality.LOCAL
    kind: str = "plan"
    reliability: float = 0.6  # 결정적이지만 휴리스틱 — 솔직한 신뢰도

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == self.kind

    def _result(self, verdict: Verdict, detail: str, evidence: list[str] | None = None) -> VerifierResult:
        return VerifierResult(
            tier=self.tier,
            verdict=verdict,
            reliability=self.reliability,
            detail=detail,
            evidence=evidence or [],
            locality=self.locality,
        )


class AISlopVerifier(_PlanVerifier):
    name = "ai_slop"
    kind = "copy"
    reliability = 0.6

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        rep = check_slop(claim.content)
        if rep.clean:
            return self._result(Verdict.PASS, "AI-slop 없음", [f"score={rep.score}"])
        return self._result(Verdict.FAIL, f"AI 티 감지(score={rep.score})", rep.flags)


class ContrastVerifier(_PlanVerifier):
    name = "contrast"
    kind = "palette"
    reliability = 0.95  # 대비 계산은 결정적이고 표준식 — 높게.

    def __init__(self, *, minimum: float = 4.5) -> None:
        self.minimum = minimum  # WCAG AA 본문 기준

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        # content = "#rrggbb on #rrggbb"
        m = re.findall(r"#[0-9a-fA-F]{3,6}", claim.content)
        if len(m) < 2:
            return self._result(Verdict.INCONCLUSIVE, "색 두 개가 필요(fg,bg)")
        ratio = contrast_ratio(m[0], m[1])
        if ratio >= self.minimum:
            return self._result(Verdict.PASS, f"대비 {ratio}:1 ≥ {self.minimum}", [f"{m[0]}/{m[1]}"])
        return self._result(Verdict.FAIL, f"대비 {ratio}:1 < {self.minimum}", [f"{m[0]}/{m[1]}"])


class AccentCountVerifier(_PlanVerifier):
    name = "accent_count"
    kind = "accent"
    reliability = 0.9

    def __init__(self, *, limit: int = 2) -> None:
        self.limit = limit

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        n = int(claim.meta.get("count", 0))
        if accent_count_ok(n, limit=self.limit):
            return self._result(Verdict.PASS, f"포인트 {n}개 (≤{self.limit})")
        return self._result(Verdict.FAIL, f"포인트 {n}개 — 과용/부재 (1~{self.limit} 권장)")


class FrameAlignmentVerifier(_PlanVerifier):
    name = "frame_alignment"
    kind = "frame"
    reliability = 0.55  # 휴리스틱 — 가장 낮게

    def __init__(self, *, threshold: float = 0.5) -> None:
        self.threshold = threshold

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        goal = str(claim.meta.get("goal_action", ""))
        sections = list(claim.meta.get("sections", []))
        frac = frame_alignment(goal, sections, threshold=self.threshold)
        if frac >= self.threshold:
            return self._result(Verdict.PASS, f"목적 정렬 {frac:.0%}")
        return self._result(Verdict.FAIL, f"목적 이탈 — 정렬 {frac:.0%} < {self.threshold:.0%}")
