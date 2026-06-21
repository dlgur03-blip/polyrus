"""검색/RAG 도메인 — 인용 존재·함의 지지·인용문 정확. 같은 골격, 출처 오라클."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Tier, Verdict
from polyrus.verifiers.registry import default_retrieval_bank
from polyrus.verifiers.retrieval.citation import (
    CitationExistenceVerifier,
    QuoteVerifier,
    SupportVerifier,
    lexical_support,
)

_DOD = DoD(spec="검색 답변", frozen=True)
_CORPUS = {"doc1": "2025년 회사 매출은 100억원이며 영업이익은 20억원이다.", "doc2": "직원 수는 50명이다."}


def _ret(citations) -> Claim:
    return Claim(id="c", content="", kind="retrieval", meta={"citations": citations, "corpus": _CORPUS})


# ── 인용 존재(T3) ──────────────────────────────────────────────────────────────
def test_citation_exists_pass() -> None:
    c = _ret([{"text": "매출 100억", "source": "doc1"}])
    assert CitationExistenceVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_citation_hallucinated_source_fails() -> None:
    c = _ret([{"text": "매출 100억", "source": "doc99"}])  # 없는 출처
    r = CitationExistenceVerifier().verify(c, _DOD)
    assert r.verdict is Verdict.FAIL and "환각" in r.detail


# ── 함의 지지(T3) ──────────────────────────────────────────────────────────────
def test_support_pass_when_grounded() -> None:
    c = _ret([{"text": "회사 매출은 100억원", "source": "doc1"}])
    assert SupportVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_support_fails_when_ungrounded() -> None:
    # 출처는 존재하지만 주장이 그 출처에 없음(근거 없는 주장).
    c = _ret([{"text": "해외 지사가 열 곳 있다", "source": "doc2"}])
    assert SupportVerifier().verify(c, _DOD).verdict is Verdict.FAIL


def test_lexical_support_score() -> None:
    # 한글 조사("매출" vs "매출은")로 부분 매칭 — 임베딩 scorer로 업그레이드 대상.
    assert lexical_support("매출 100억원", "회사 매출은 100억원") >= 0.5
    assert lexical_support("완전 다른 주제 우주", "회사 매출 정보") < 0.3


# ── 인용문 정확(T1, 날조 차단) ─────────────────────────────────────────────────
def test_quote_verbatim_pass() -> None:
    c = _ret([{"text": "매출", "source": "doc1", "quote": "매출은 100억원"}])
    assert QuoteVerifier().verify(c, _DOD).verdict is Verdict.PASS


def test_quote_fabricated_fails() -> None:
    c = _ret([{"text": "매출", "source": "doc1", "quote": "매출은 300억원"}])  # 출처에 없는 인용
    r = QuoteVerifier().verify(c, _DOD)
    assert r.verdict is Verdict.FAIL and "날조" in r.detail


# ── 같은 골격: 검색 뱅크 ────────────────────────────────────────────────────────
def test_retrieval_bank_passes_grounded_answer() -> None:
    c = _ret([{"text": "회사 매출은 100억원", "source": "doc1", "quote": "매출은 100억원"}])
    agg = default_retrieval_bank().run(c, _DOD)
    assert agg.passed


def test_retrieval_bank_blocks_fabricated_quote() -> None:
    # 날조 인용은 T1에서 단락(블록).
    c = _ret([{"text": "매출", "source": "doc1", "quote": "매출은 999억원"}])
    agg = default_retrieval_bank().run(c, _DOD)
    assert not agg.passed
    assert all(r.tier is Tier.T1_EXECUTION for r in agg.results)  # T1 단락


def test_retrieval_bank_blocks_ungrounded_claim() -> None:
    # 인용 존재·인용문 OK여도 함의(T3)가 막는다.
    c = _ret([{"text": "직원이 천 명이다", "source": "doc2"}])  # doc2엔 50명
    agg = default_retrieval_bank().run(c, _DOD)
    assert not agg.passed
