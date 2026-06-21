"""스코어러·요약기·함의기 업그레이드 — context(Select/Compress)·retrieval(함의)의 주입 가능 훅.

기본(어휘 토큰)은 한글 조사에 약하다('매출' vs '매출은'). 두 업그레이드:
  - ngram_score: *문자 n-gram* 자카드 — 의존성 0, 한글 교착어에 강함('매출' ⊂ '매출은').
  - EmbeddingScorer: 임베딩 코사인 — embed 함수 주입(OpenAI/sentence-transformers 등).
  - LLMSummarizer / LLMEntailment: ModelClient로 압축·함의 판정(품질 업그레이드).
모두 주입식이라 테스트는 fake로 네트워크 0. 기존 seam(ContextEngine(scorer=), SupportVerifier(entailment=))에 끼운다.
"""
from __future__ import annotations

import math
from typing import Callable, Sequence

from polyrus.models import ModelClient


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _ngrams(text: str, n: int) -> set[str]:
    s = "".join(text.lower().split())
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def ngram_score(query: str, text: str, *, n: int = 2) -> float:
    """문자 n-gram 자카드. 한글 조사/교착에 강함(어휘 토큰보다 나은 결정적 베이스라인)."""
    q, t = _ngrams(query, n), _ngrams(text, n)
    if not q or not t:
        return 0.0
    return len(q & t) / len(q | t)


class EmbeddingScorer:
    """임베딩 코사인 관련도. embed: text → 벡터(OpenAI embeddings·sentence-transformers 등 주입)."""

    def __init__(self, embed: Callable[[str], Sequence[float]]) -> None:
        self.embed = embed

    def __call__(self, query: str, text: str) -> float:
        return cosine(self.embed(query), self.embed(text))


class LLMSummarizer:
    """ContextEngine compress용 — overflow 컨텍스트를 LLM으로 압축 요약."""

    def __init__(self, model: ModelClient, *, max_chars: int = 400) -> None:
        self.model = model
        self.max_chars = max_chars

    def __call__(self, items: list) -> str:
        body = "\n".join(getattr(it, "text", str(it)) for it in items)
        return self.model.complete(
            f"다음을 {self.max_chars}자 이내로 핵심만 요약하라(사실 보존):\n{body}",
            system="너는 정확한 요약가다. 사실을 바꾸거나 지어내지 마라.",
            temperature=0.0,
        ).strip()


class LLMEntailment:
    """retrieval SupportVerifier용 — 출처가 주장을 지지하는지 LLM 판정(어휘 함의보다 정확)."""

    def __init__(self, model: ModelClient) -> None:
        self.model = model

    def __call__(self, claim_text: str, source_text: str) -> float:
        v = self.model.complete(
            f"출처가 주장을 지지하는가? '지지' 또는 '불충분' 한 단어로만.\n주장: {claim_text}\n출처: {source_text}",
            system="너는 엄격한 사실검증가다. 출처에 명시되지 않으면 '불충분'.",
            temperature=0.0,
        )
        return 1.0 if "지지" in v else 0.0
