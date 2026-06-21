"""레퍼런스 조사 루프 — URL 추출·읽기·출처검증(T3). 네트워크 없이 FakeFetcher로."""
from __future__ import annotations

from polyrus.planner import ProactivePlanner, ScriptedAnswers
from polyrus.research import (
    FakeFetcher,
    FakeSearcher,
    ReferenceDoc,
    ReferenceProvenanceVerifier,
    extract_signals,
    extract_urls,
    research_references,
    search_query_for,
)
from polyrus.skeleton import HOMEPAGE
from polyrus.types import Claim, DoD, Verdict


def test_extract_urls_normalizes() -> None:
    urls = extract_urls("stripe.com, https://linear.app 그리고 vercel.com/about 처럼")
    assert urls == ["https://stripe.com", "https://linear.app", "https://vercel.com/about"]


def test_extract_urls_empty_for_industry_only() -> None:
    assert extract_urls("그냥 카페 홈페이지요") == []


def test_extract_signals_multi_fallback() -> None:
    html = "<html><head><title>Acme</title></head><body><h1>주문하기</h1><h2>메뉴</h2></body></html>"
    title, headings = extract_signals(html)
    assert title == "Acme" and headings == ["주문하기", "메뉴"]
    # title 없으면 og:title 폴백.
    og = '<meta property="og:title" content="OG제목"><h1>x</h1>'
    assert extract_signals(og)[0] == "OG제목"


def _ff() -> FakeFetcher:
    return FakeFetcher({
        "https://stripe.com": ReferenceDoc("https://stripe.com", ok=True, status=200,
                                           title="Stripe", headings=["Payments"]),
        "https://linear.app": ReferenceDoc("https://linear.app", ok=True, status=200, title="Linear"),
    })


def test_research_reachable() -> None:
    rep = research_references("stripe.com 과 linear.app", _ff())
    assert len(rep.reachable) == 2 and not rep.industry_only
    assert "Stripe" in rep.summary


def test_research_industry_only_without_searcher() -> None:
    rep = research_references("동네 미용실", _ff())  # searcher 없음 → 막다른길(정직)
    assert rep.industry_only
    assert "못 찾음" in rep.summary


def test_search_resolves_industry_only() -> None:
    # 업종만 줬을 때 searcher가 후보를 찾아오고, 그걸 읽어 검증한다(막다른길 해소).
    fetcher = FakeFetcher({
        "https://ilovesalon.kr/": ReferenceDoc("https://ilovesalon.kr/", ok=True, title="아이러브살롱"),
    })
    searcher = FakeSearcher({"미용실": ["https://ilovesalon.kr/"]})
    rep = research_references("동네 미용실", fetcher, searcher=searcher)
    assert not rep.industry_only and rep.searched
    assert len(rep.reachable) == 1 and "검색으로 찾음" in rep.summary


def test_search_query_biases_homepage() -> None:
    assert search_query_for("미용실") == "미용실 공식 홈페이지"


def test_search_empty_falls_back_to_industry_only() -> None:
    rep = research_references("아주 희귀한 업종", _ff(), searcher=FakeSearcher({}))
    assert rep.industry_only  # 검색도 빈손 → 정직 복귀


def test_provenance_verifier() -> None:
    v = ReferenceProvenanceVerifier()
    ok = research_references("stripe.com", _ff())
    assert v.verify(Claim("r", "", kind="reference", meta={"report": ok}), DoD("x", frozen=True)).verdict is Verdict.PASS
    # 도달 실패(환각/오타) → FAIL.
    bad = research_references("nonexistent-xyz.com", _ff())
    assert v.verify(Claim("r", "", kind="reference", meta={"report": bad}), DoD("x", frozen=True)).verdict is Verdict.FAIL
    # 업종만 → INCONCLUSIVE(없는 걸 통과시키지 않는다).
    ind = research_references("카페", _ff())
    assert v.verify(Claim("r", "", kind="reference", meta={"report": ind}), DoD("x", frozen=True)).verdict is Verdict.INCONCLUSIVE


# ── planner 통합: 레퍼런스 단계가 읽어서 검증한다 ──────────────────────────────────
def test_planner_runs_research_on_references_step() -> None:
    ans = ScriptedAnswers(answers={
        "goal_action": "문의하기", "references": "stripe.com, linear.app",
        "tone_guide": "정돈된", "accent_section": "히어로", "palette": "신뢰", "features": "문의폼",
    })
    res = ProactivePlanner(HOMEPAGE, researcher=_ff()).run(ans)
    ref_rec = next(r for r in res.records if r.step.id == "references")
    assert ref_rec.report is not None
    assert "Stripe" in ref_rec.value  # 읽은 신호가 brief에 반영
    # 출처검증이 plan.verify에 연결된다.
    v = res.verify({})
    assert any(r.tier.value == "t3_provenance" and r.verdict is Verdict.PASS for r in v.results)


def test_planner_searches_when_industry_only() -> None:
    # 사용자가 사이트 이름을 못 대고 업종만('미용실') → planner가 찾아와서 검증.
    ans = ScriptedAnswers(answers={
        "goal_action": "예약하기", "references": "동네 미용실",
        "tone_guide": "정돈된", "accent_section": "히어로", "palette": "신뢰", "features": "예약폼",
    })
    fetcher = FakeFetcher({"https://ilovesalon.kr/": ReferenceDoc("https://ilovesalon.kr/", ok=True, title="살롱")})
    searcher = FakeSearcher({"미용실": ["https://ilovesalon.kr/"]})
    res = ProactivePlanner(HOMEPAGE, researcher=fetcher, searcher=searcher).run(ans)
    ref_rec = next(r for r in res.records if r.step.id == "references")
    assert ref_rec.report is not None and ref_rec.report.searched
    assert "검색으로 찾음" in ref_rec.value


def test_planner_without_researcher_skips() -> None:
    ans = ScriptedAnswers(answers={
        "goal_action": "문의하기", "references": "stripe.com",
        "tone_guide": "정돈된", "accent_section": "히어로", "palette": "신뢰", "features": "문의폼",
    })
    res = ProactivePlanner(HOMEPAGE, researcher=None).run(ans)
    ref_rec = next(r for r in res.records if r.step.id == "references")
    assert ref_rec.report is None  # 페처 없으면 조사 생략(멈추지 않음)
