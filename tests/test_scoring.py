"""스코어러·요약기·함의기 업그레이드 — 한글 robust + 주입식 LLM/임베딩."""
from __future__ import annotations

from polyrus.context import ContextEngine, ContextItem, ContextStore, lexical_score
from polyrus.scoring import (
    EmbeddingScorer,
    LLMEntailment,
    LLMSummarizer,
    cosine,
    ngram_score,
)
from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.retrieval.citation import SupportVerifier


# ── n-gram이 한글 조사 문제를 고친다 ───────────────────────────────────────────
def test_ngram_beats_lexical_on_korean_particles() -> None:
    q, t = "매출 100억원", "회사 매출은 100억원이다"
    # 어휘 토큰: '매출' ≠ '매출은'이라 손해. n-gram은 '매출' 부분을 잡는다.
    assert ngram_score(q, t) > lexical_score(q, t)
    assert ngram_score(q, t) > 0.3


def test_ngram_low_for_unrelated() -> None:
    assert ngram_score("매출 보고서", "우주 탐사 로켓") < 0.15


# ── 임베딩 코사인 ──────────────────────────────────────────────────────────────
def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == 1.0
    assert abs(cosine([1, 0], [0, 1])) < 1e-9


def test_embedding_scorer() -> None:
    # 가짜 임베더: 같은 텍스트는 같은 벡터.
    table = {"고양이": [1.0, 0.0], "고양이과 동물": [0.9, 0.1], "주식 시세": [0.0, 1.0]}
    sc = EmbeddingScorer(lambda s: table[s])
    assert sc("고양이", "고양이과 동물") > sc("고양이", "주식 시세")


# ── ContextEngine에 업그레이드 스코어러 주입 ───────────────────────────────────
def test_context_engine_with_ngram_scorer() -> None:
    store = ContextStore()
    store.add(ContextItem("hit", "매출은 100억원이다"))   # 조사 붙은 텍스트
    store.add(ContextItem("miss", "점심 메뉴는 김치찌개"))
    out = ContextEngine(store, scorer=ngram_score).assemble("매출 100억", max_tokens=1000)
    assert "hit" in {it.id for it in out.items}  # 어휘 스코어러로는 놓쳤을 것


# ── LLM 요약기/함의기(주입식, 네트워크 0) ──────────────────────────────────────
class FakeModel:
    def __init__(self, out: str) -> None:
        self.out = out
        self.calls: list[str] = []

    def complete(self, prompt, *, system=None, model=None, temperature=None) -> str:
        self.calls.append(prompt)
        return self.out


def test_llm_summarizer() -> None:
    s = LLMSummarizer(FakeModel("핵심 요약문"))
    assert s([ContextItem("a", "긴 내용 1"), ContextItem("b", "긴 내용 2")]) == "핵심 요약문"


def test_llm_entailment_supports() -> None:
    e = LLMEntailment(FakeModel("지지"))
    assert e("매출 100억", "회사 매출은 100억원") == 1.0


def test_llm_entailment_insufficient() -> None:
    assert LLMEntailment(FakeModel("불충분"))("주장", "무관한 출처") == 0.0


def test_support_verifier_with_llm_entailment() -> None:
    # SupportVerifier에 LLM 함의기 주입 — 출처에 없으면 LLM이 '불충분' → FAIL.
    corpus = {"doc1": "직원 수는 50명이다"}
    claim = Claim("c", "", kind="retrieval", meta={
        "citations": [{"text": "직원이 천 명이다", "source": "doc1"}], "corpus": corpus})
    v = SupportVerifier(entailment=LLMEntailment(FakeModel("불충분")))
    assert v.verify(claim, DoD(spec="q", frozen=True)).verdict is Verdict.FAIL
