"""컨텍스트 엔지니어링 (v2 기둥 B) — Write/Select/Compress + poisoning 감지.

플레이북: 백만 토큰 윈도우는 문제를 해결하지 않고 옮긴다. 컨텍스트를 *검증된 자원*으로 다룬다.
  - Write    : 모델 밖에 저장(원장·코퍼스와 같은 사상) → 토큰을 안 먹고 보관.
  - Select   : 지금 필요한 것만 회수(관련도 점수). 다 넣지 않는다.
  - Compress : 토큰 예산(5.7)에 맞게 요약·정리.
  - Poisoning: 오염된 입력(프롬프트 인젝션·모순·낡음)을 감지 = *입력측 No-Pass*.
    (v1 이해검증이 '맞는 질문에 답하나'라면, 이건 '맞는 *재료*로 답하나'.)

기본 구현은 LLM 없이 결정적(어휘 관련도·인젝션 패턴). scorer/summarizer 주입으로 임베딩·LLM 업그레이드.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

# 프롬프트 인젝션 표식(영/한). 오염 입력의 가장 흔한 형태.
_INJECTION = re.compile(
    r"ignore\s+(all\s+)?(previous|above)|disregard\s+(previous|above)|you\s+are\s+now|"
    r"system\s*:|이전\s*지시\s*무시|위\s*지시\s*무시|새로운\s*지시|규칙\s*무시",
    re.IGNORECASE,
)
_TOKEN = re.compile(r"\w+", re.UNICODE)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)  # 대략 4자/토큰


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


@dataclass
class ContextItem:
    id: str
    text: str
    kind: str = "fact"          # fact | decision | memory | tool_result | ...
    source: str = ""
    key: str | None = None      # 같은 key의 서로 다른 값 = 모순 후보
    stale: bool = False


@dataclass
class PoisonFlag:
    item_id: str
    kind: str                   # injection | contradiction | stale
    detail: str


class ContextStore:
    """Write — 컨텍스트를 모델 밖에 보관. (원장·코퍼스와 같은 사상의 일반화.)"""

    def __init__(self) -> None:
        self._items: list[ContextItem] = []

    def add(self, item: ContextItem) -> None:
        self._items.append(item)

    def all(self) -> list[ContextItem]:
        return list(self._items)


def lexical_score(query: str, text: str) -> float:
    """기본 관련도 = 토큰 자카드. 임베딩 scorer로 교체 가능."""
    q, t = _tokens(query), _tokens(text)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


def select(
    items: list[ContextItem],
    query: str,
    *,
    k: int = 10,
    scorer: Callable[[str, str], float] = lexical_score,
) -> list[ContextItem]:
    """Select — 관련도 상위 k개만. 안정 정렬로 동점은 원래 순서 보존."""
    ranked = sorted(items, key=lambda it: scorer(query, it.text), reverse=True)
    return [it for it in ranked if scorer(query, it.text) > 0][:k]


def compress(
    items: list[ContextItem],
    max_tokens: int,
    *,
    summarizer: Callable[[list[ContextItem]], str] | None = None,
) -> list[ContextItem]:
    """Compress — 토큰 예산 안으로. 들어가는 만큼 담고, 나머지는 요약(summarizer 있으면) 또는 버림."""
    kept: list[ContextItem] = []
    used = 0
    overflow: list[ContextItem] = []
    for it in items:
        cost = estimate_tokens(it.text)
        if used + cost <= max_tokens:
            kept.append(it)
            used += cost
        else:
            overflow.append(it)
    if overflow and summarizer is not None:
        summary = summarizer(overflow)
        if summary.strip():
            kept.append(ContextItem(id="compressed-summary", text=summary, kind="memory", source="compress"))
    return kept


def detect_poisoning(items: list[ContextItem]) -> list[PoisonFlag]:
    """입력측 No-Pass: 인젝션·모순·낡음을 감지. 인젝션은 차단, 모순/낡음은 표면화."""
    flags: list[PoisonFlag] = []
    by_key: dict[str, ContextItem] = {}
    for it in items:
        if _INJECTION.search(it.text):
            flags.append(PoisonFlag(it.id, "injection", "프롬프트 인젝션 표식 감지"))
        if it.stale:
            flags.append(PoisonFlag(it.id, "stale", "낡음/대체됨으로 표시"))
        if it.key is not None:
            prev = by_key.get(it.key)
            if prev is not None and prev.text.strip() != it.text.strip():
                flags.append(PoisonFlag(it.id, "contradiction", f"'{it.key}' 값이 {prev.id}와 충돌"))
            by_key[it.key] = it
    return flags


@dataclass
class AssembledContext:
    items: list[ContextItem]
    flags: list[PoisonFlag]
    tokens: int = 0
    dropped_injections: list[str] = field(default_factory=list)


class ContextEngine:
    """Write→(poisoning 차단)→Select→Compress 파이프라인. '맞는 재료로 채운' 컨텍스트를 조립."""

    def __init__(
        self,
        store: ContextStore | None = None,
        *,
        scorer: Callable[[str, str], float] = lexical_score,
        summarizer: Callable[[list[ContextItem]], str] | None = None,
    ) -> None:
        self.store = store or ContextStore()
        self.scorer = scorer
        self.summarizer = summarizer

    def assemble(self, query: str, *, max_tokens: int, k: int = 10) -> AssembledContext:
        items = self.store.all()
        flags = detect_poisoning(items)
        poisoned = {f.item_id for f in flags if f.kind == "injection"}
        clean = [it for it in items if it.id not in poisoned]  # 인젝션은 차단(컨텍스트에서 제외)
        selected = select(clean, query, k=k, scorer=self.scorer)
        fitted = compress(selected, max_tokens, summarizer=self.summarizer)
        return AssembledContext(
            items=fitted,
            flags=flags,
            tokens=sum(estimate_tokens(it.text) for it in fitted),
            dropped_injections=sorted(poisoned),
        )
