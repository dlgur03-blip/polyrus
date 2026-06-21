"""레퍼런스 조사 루프 (선제질문 step1) — 읽기우선·출처검증(T3).

사용자가 "좋아 보이는 사이트 2~3개" 또는 업종을 주면, 우리가 *읽어서* 신호를 뽑고
출처가 실재하는지 검증한다(환각·날조 차단). 쓰기 없음(부수효과 0) → 자율(browser.py 읽기우선과 동일).

페처는 duck-typed(ReferenceFetcher). 우선순위:
  - ScraplingFetcher : 선호(CLAUDE.md '스크래핑=Scrapling 우선'). 셀렉터 자가치유. lazy import(옵셔널 extra).
  - UrllibFetcher    : stdlib 폴백 — 의존성 0으로 *항상* 동작. 다(多)신호 추출로 셀렉터 변경에 강건.
  - FakeFetcher      : 테스트.

추출은 한 셀렉터에 안 매달린다(Scrapling 사상): title → og:title → h1 → meta desc 폴백 사슬.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult

_URL = re.compile(r"(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/[\w\-./?%&=#]*)?", re.I)
_TAG = re.compile(r"<[^>]+>")
_MAX_BYTES = 2_000_000


@dataclass
class ReferenceDoc:
    url: str
    ok: bool
    status: int = 0
    title: str = ""
    headings: list[str] = field(default_factory=list)
    text_len: int = 0
    error: str = ""


@runtime_checkable
class ReferenceFetcher(Protocol):
    def fetch(self, url: str) -> ReferenceDoc: ...


def extract_urls(text: str, *, max_n: int = 3) -> list[str]:
    """레퍼런스 답에서 URL/도메인을 뽑아 https로 정규화. (업종만 적었으면 빈 리스트.)"""
    out: list[str] = []
    for m in _URL.findall(text):
        u = m if m.lower().startswith("http") else f"https://{m}"
        if u not in out:
            out.append(u)
        if len(out) >= max_n:
            break
    return out


def _strip(html_fragment: str) -> str:
    return _TAG.sub("", html_fragment).strip()


def extract_signals(html: str) -> tuple[str, list[str]]:
    """다신호 추출(셀렉터 변경에 강건): title→og:title 폴백, h1·h2 헤딩."""
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if m:
        title = _strip(m.group(1))
    if not title:
        og = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']', html, re.I)
        if og:
            title = og.group(1).strip()
    headings = [
        _strip(h) for h in re.findall(r"<h[12][^>]*>(.*?)</h[12]>", html, re.I | re.S)
    ]
    headings = [h for h in headings if h][:5]
    return title, headings


class UrllibFetcher:
    """stdlib 읽기전용 페처(의존성 0). GET만 — 부수효과 없음(읽기우선 안전)."""

    def __init__(self, *, timeout: float = 10.0, user_agent: str = "Mozilla/5.0 Polyrus") -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch(self, url: str) -> ReferenceDoc:
        if not url.lower().startswith(("http://", "https://")):
            return ReferenceDoc(url, ok=False, error="http(s)만 허용")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - http(s) 검사함
                status = getattr(resp, "status", 200) or 200
                raw = resp.read(_MAX_BYTES)
            html = raw.decode("utf-8", "replace")
            title, headings = extract_signals(html)
            return ReferenceDoc(url, ok=True, status=status, title=title,
                                headings=headings, text_len=len(html))
        except Exception as e:  # noqa: BLE001 - 네트워크/파싱 실패는 출처검증 FAIL로 다룬다
            return ReferenceDoc(url, ok=False, error=f"{type(e).__name__}: {e}")


class ScraplingFetcher:  # pragma: no cover - 옵셔널 extra(polyrus-agent[research]) + 네트워크
    """선호 어댑터 — Scrapling으로 자가치유 페치. 미설치 시 ImportError → 호출자가 urllib로 폴백."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        from scrapling.fetchers import Fetcher  # lazy: 옵셔널 의존성

        self._get = Fetcher.get
        self.timeout = timeout

    def fetch(self, url: str) -> ReferenceDoc:
        if not url.lower().startswith(("http://", "https://")):
            return ReferenceDoc(url, ok=False, error="http(s)만 허용")
        try:
            r = self._get(url, timeout=self.timeout)
            title = r.css_first("title::text") or r.css_first('meta[property="og:title"]::attr(content)') or ""
            headings = [t for t in (r.css("h1::text") + r.css("h2::text")) if t][:5]
            return ReferenceDoc(url, ok=True, status=getattr(r, "status", 200),
                                title=str(title).strip(), headings=[str(h).strip() for h in headings],
                                text_len=len(getattr(r, "html_content", "") or ""))
        except Exception as e:  # noqa: BLE001
            return ReferenceDoc(url, ok=False, error=f"{type(e).__name__}: {e}")


@dataclass
class FakeFetcher:
    """테스트용 — url → ReferenceDoc 매핑. 미등록 url은 실패(존재하지 않는 출처)."""

    docs: dict[str, ReferenceDoc] = field(default_factory=dict)

    def fetch(self, url: str) -> ReferenceDoc:
        return self.docs.get(url, ReferenceDoc(url, ok=False, error="not found"))


def default_fetcher() -> ReferenceFetcher:
    """Scrapling 가용하면 선호, 아니면 urllib 폴백(항상 동작)."""
    try:
        return ScraplingFetcher()
    except Exception:  # noqa: BLE001 - 미설치/의존성 누락 → 폴백
        return UrllibFetcher()


# ── 검색: 업종만 줬을 때 후보 레퍼런스를 *찾아온다*(막다른길 해소) ─────────────────────
@runtime_checkable
class Searcher(Protocol):
    def search(self, query: str, *, max_n: int = 3) -> list[str]: ...


def search_query_for(industry: str) -> str:
    """업종 → 레퍼런스 탐색 질의(홈페이지 후보 편향)."""
    return f"{industry.strip()} 공식 홈페이지"


class DuckDuckGoSearcher:  # pragma: no cover - 네트워크
    """키 없는 검색(DuckDuckGo HTML, POST). 읽기전용 — 부수효과 없음(읽기우선)."""

    def __init__(self, *, timeout: float = 12.0) -> None:
        self.timeout = timeout
        self._anchor = re.compile(r'<a\b[^>]*class="result__a"[^>]*>')
        self._href = re.compile(r'href="([^"]+)"')

    def search(self, query: str, *, max_n: int = 3) -> list[str]:
        data = urllib.parse.urlencode({"q": query}).encode()
        req = urllib.request.Request(
            "https://html.duckduckgo.com/html/", data=data,
            headers={"User-Agent": "Mozilla/5.0"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https 고정
                body = resp.read(_MAX_BYTES).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - 검색 실패는 빈손(막다른길로 정직 복귀)
            return []
        out: list[str] = []
        for tag in self._anchor.findall(body):
            m = self._href.search(tag)
            if not m:
                continue
            url = m.group(1)
            if url.startswith("//"):
                url = "https:" + url
            redir = re.search(r"uddg=([^&]+)", url)  # 혹시 리다이렉트면 디코드
            if redir:
                url = urllib.parse.unquote(redir.group(1))
            if url.startswith("http") and url not in out:
                out.append(url)
            if len(out) >= max_n:
                break
        return out


@dataclass
class FakeSearcher:
    """테스트용 — query 부분일치 → url 목록."""

    results: dict[str, list[str]] = field(default_factory=dict)

    def search(self, query: str, *, max_n: int = 3) -> list[str]:
        for key, urls in self.results.items():
            if key in query:
                return urls[:max_n]
        return []


def default_searcher() -> Searcher:
    return DuckDuckGoSearcher()


@dataclass
class ReferenceReport:
    query: str
    docs: list[ReferenceDoc]
    industry_only: bool = False  # URL도 검색결과도 없음 → 출처검증 불가(정직)
    searched: bool = False        # 업종만 줘서 우리가 *찾아온* 결과인지

    @property
    def reachable(self) -> list[ReferenceDoc]:
        return [d for d in self.docs if d.ok]

    @property
    def summary(self) -> str:
        if self.industry_only:
            return f"업종만 제시('{self.query}') — 후보를 못 찾음"
        if not self.docs:
            return "레퍼런스 없음"
        prefix = "(검색으로 찾음) " if self.searched else ""
        parts = []
        for d in self.docs:
            if d.ok:
                head = f" · {d.headings[0]}" if d.headings else ""
                parts.append(f"✓ {d.url} — {d.title or '(제목없음)'}{head}")
            else:
                parts.append(f"✗ {d.url} — 도달 실패({d.error})")
        return prefix + "; ".join(parts)


def research_references(
    query: str,
    fetcher: ReferenceFetcher,
    *,
    searcher: Searcher | None = None,
    max_refs: int = 3,
) -> ReferenceReport:
    """레퍼런스 답을 읽어 신호 추출 + 도달성 확인.

    URL이 있으면 그걸 읽고, 없으면(업종만) searcher로 *찾아와서* 읽는다 — '업종만' 막다른길 해소.
    검색기도 없거나 빈손이면 industry_only로 정직 표시(없는 걸 통과시키지 않는다).
    """
    urls = extract_urls(query, max_n=max_refs)
    searched = False
    if not urls:
        if searcher is None:
            return ReferenceReport(query, docs=[], industry_only=True)
        urls = searcher.search(search_query_for(query), max_n=max_refs)
        searched = True
        if not urls:
            return ReferenceReport(query, docs=[], industry_only=True)
    return ReferenceReport(query, docs=[fetcher.fetch(u) for u in urls], searched=searched)


# ── 출처 검증(T3): 레퍼런스가 실재·도달 가능한가(환각 차단) ──────────────────────────
class ReferenceProvenanceVerifier:
    """레퍼런스 출처 존재/도달 검증. claim.meta['report']=ReferenceReport."""

    tier: Tier = Tier.T3_PROVENANCE
    name: str = "provenance"
    locality: Locality = Locality.LOCAL
    reliability: float = 0.7

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "reference"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        report: ReferenceReport | None = claim.meta.get("report")
        if report is None or report.industry_only:
            # 검증 불가 → 솔직하게 INCONCLUSIVE(없는 걸 통과시키지 않는다).
            return VerifierResult(self.tier, Verdict.INCONCLUSIVE, self.reliability,
                                  "URL 없음 — 출처검증 불가", locality=self.locality)
        if not report.reachable:
            return VerifierResult(self.tier, Verdict.FAIL, self.reliability,
                                  "레퍼런스 전부 도달 실패(환각/오타 의심)",
                                  evidence=[d.error for d in report.docs], locality=self.locality)
        return VerifierResult(self.tier, Verdict.PASS, self.reliability,
                              f"레퍼런스 {len(report.reachable)}/{len(report.docs)} 도달",
                              evidence=[d.url for d in report.reachable], locality=self.locality)
