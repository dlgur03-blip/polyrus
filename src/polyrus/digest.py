"""다이제스트 실행 엔진 — '매일 9시 GitHub 유망한 거 요약해서 보내줘'를 *진짜* 돌린다.

흐름: GitHub 조회(읽기전용) → 유망 필터(결정적) → 요약 렌더(날조 금지) → 검증(No-Pass) → 전송.
스케줄(cron)은 이 실행을 *언제* 부를지일 뿐 — 실행 자체는 `run_digest` 한 번이다.

원칙:
- 읽기우선: GitHub 조회는 GET(부수효과 없음).
- 날조 금지: 요약은 *조회한 실제 데이터*만(스타·이름·설명). 없는 숫자 지어내지 않는다.
- No-Pass: 보내기 전에 검증한다(AI-slop·빈 다이제스트) — 슬롭/빈 걸 조용히 보내지 않는다.
- 자격증명 경계: 전송 채널 설정(텔레그램 토큰 등)은 env로만(telegram.py 규약 그대로).
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult

_MAX_BYTES = 2_000_000
# 흔한 언어만(소스 텍스트에서 language: 절 추출용).
_LANGS = ("python", "javascript", "typescript", "rust", "go", "java", "kotlin", "swift", "c++", "ruby")
# 소스 텍스트에서 떼어낼 한국어 군더더기(키워드만 남기기).
_FILLER = ("토픽", "키워드", "특정", "언어", "전체", "트렌딩", "관련", "쪽", "것", "들")


@dataclass(frozen=True)
class Repo:
    full_name: str
    description: str
    stars: int
    url: str
    pushed_at: str = ""
    language: str = ""


@runtime_checkable
class RepoSource(Protocol):
    def search(self, query: str, *, max_n: int = 20) -> list[Repo]: ...


def build_github_query(source: str, criteria: str = "") -> str:
    """소스 답('python LLM 토픽') + 유망 기준 → GitHub 검색 q. 결정적."""
    low = source.lower()
    lang = next((x for x in _LANGS if x in low), "")
    words = [w for w in re.split(r"[\s,]+", source.strip()) if w and w not in _FILLER]
    # 언어 단어는 q 키워드에서 빼고 language: 절로(중복 방지).
    kw = [w for w in words if w.lower() != lang]
    q = " ".join(kw) if kw else "stars:>100"
    if lang:
        q += f" language:{lang}"
    q += " fork:false archived:false"
    return q.strip()


class GitHubSource:  # pragma: no cover - 네트워크
    """GitHub 검색 API(읽기전용 GET). 비인증 10req/분이면 다이제스트엔 충분. 토큰 있으면 헤더로."""

    def __init__(self, *, token: str | None = None, timeout: float = 12.0) -> None:
        self.token = token
        self.timeout = timeout

    def search(self, query: str, *, max_n: int = 20) -> list[Repo]:
        params = urllib.parse.urlencode(
            {"q": query, "sort": "stars", "order": "desc", "per_page": max_n}
        )
        req = urllib.request.Request(
            f"https://api.github.com/search/repositories?{params}",
            headers={"User-Agent": "Polyrus", "Accept": "application/vnd.github+json",
                     **({"Authorization": f"Bearer {self.token}"} if self.token else {})},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - https 고정
            data = json.loads(resp.read(_MAX_BYTES).decode("utf-8", "replace"))
        return [_repo_from(it) for it in data.get("items", [])]


def _repo_from(it: dict) -> Repo:
    return Repo(
        full_name=str(it.get("full_name", "")),
        description=str(it.get("description") or "").strip(),
        stars=int(it.get("stargazers_count", 0)),
        url=str(it.get("html_url", "")),
        pushed_at=str(it.get("pushed_at", "")),
        language=str(it.get("language") or ""),
    )


@dataclass
class FakeSource:
    repos: list[Repo] = field(default_factory=list)

    def search(self, query: str, *, max_n: int = 20) -> list[Repo]:
        return self.repos[:max_n]


def length_count(length: str) -> int:
    """분량 → 항목 수. '자세히'면 10, 기본(짧게) 5."""
    return 10 if ("자세" in length or "detail" in length.lower()) else 5


def select_promising(repos: list[Repo], *, top_n: int) -> list[Repo]:
    """유망 선별(결정적): 스타 내림차순 상위 N. (소스 쿼리에서 fork/archived 이미 제외.)"""
    return sorted(repos, key=lambda r: r.stars, reverse=True)[:top_n]


def render_digest(repos: list[Repo], *, length: str, title: str = "오늘의 GitHub 유망 레포") -> str:
    """요약 렌더 — *조회한 실제 데이터만*(날조 금지). 짧게=3줄/항목, 자세히=설명+링크."""
    if not repos:
        return f"📭 {title}: 조건에 맞는 레포를 찾지 못했어요."
    detailed = "자세" in length or "detail" in length.lower()
    lines = [f"📬 {title}", ""]
    for i, r in enumerate(repos, 1):
        desc = r.description or "(설명 없음)"
        lines.append(f"{i}. {r.full_name} — ⭐{r.stars:,}")
        lines.append(f"   {desc[:120]}")
        if detailed:
            lang = f" · {r.language}" if r.language else ""
            lines.append(f"   {r.url}{lang}")
    return "\n".join(lines)


# ── 충실성 검증(날조 차단) — 출력의 레포/스타가 *실제 조회 데이터*를 벗어나지 않나 ──────────
_LINE = re.compile(r"^\s*\d+\.\s+(\S+/\S+)\s+—\s+⭐([\d,]+)", re.MULTILINE)


class DigestFaithfulnessVerifier:
    """렌더된 다이제스트를 *독립적으로 파싱*해 모든 레포·스타가 조회 데이터에 실재하는지 대조.

    No-Pass의 핵심: 모델/렌더가 없는 레포나 부풀린 스타를 만들면 잡는다(날조 차단). 결정적·T1.
    claim.kind=='digest', claim.meta['repos']=조회된 Repo 목록.
    """

    tier: Tier = Tier.T1_EXECUTION
    name: str = "digest_faithfulness"
    locality: Locality = Locality.LOCAL
    reliability: float = 0.9

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "digest"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        repos: list[Repo] = list(claim.meta.get("repos", []))
        stars_by_name = {r.full_name: r.stars for r in repos}
        bad: list[str] = []
        n = 0
        for name, star_str in _LINE.findall(claim.content):
            n += 1
            if name not in stars_by_name:
                bad.append(f"없는 레포: {name}")
            elif int(star_str.replace(",", "")) != stars_by_name[name]:
                bad.append(f"스타 불일치: {name}(렌더 {star_str} ≠ 실제 {stars_by_name[name]:,})")
        if bad:
            return VerifierResult(self.tier, Verdict.FAIL, self.reliability,
                                  "날조 감지: " + "; ".join(bad), bad, self.locality)
        if n == 0:
            return VerifierResult(self.tier, Verdict.INCONCLUSIVE, self.reliability,
                                  "검증할 항목 없음", locality=self.locality)
        return VerifierResult(self.tier, Verdict.PASS, self.reliability,
                              f"{n}개 항목 모두 실제 데이터와 일치", locality=self.locality)


# ── 전달 채널 ────────────────────────────────────────────────────────────────────
@runtime_checkable
class Deliverer(Protocol):
    def send(self, text: str) -> bool: ...


class TelegramDeliverer:
    """telegram.py 재사용. 토큰 미설정이면 send=False(온보딩에서 키 요청)."""

    def __init__(self, client: object | None = None) -> None:
        if client is None:
            from polyrus.notify.telegram import TelegramClient
            client = TelegramClient.from_env()
        self.client = client

    def send(self, text: str) -> bool:
        if self.client is None:
            return False  # 미설정 — 전송 불가(채널 검증이 이미 INCONCLUSIVE로 안내)
        self.client.send_message(text)  # type: ignore[attr-defined]
        return True


@dataclass
class FakeDeliverer:
    sent: list[str] = field(default_factory=list)
    ok: bool = True

    def send(self, text: str) -> bool:
        self.sent.append(text)
        return self.ok


class ConsoleDeliverer:
    def send(self, text: str) -> bool:  # pragma: no cover - 표준출력
        print(text)
        return True


# ── 실행 ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class DigestConfig:
    source: str
    criteria: str = ""
    schedule_cron: str = ""
    channel: str = ""
    length: str = "짧게"

    def github_query(self) -> str:
        return build_github_query(self.source, self.criteria)

    def to_dict(self) -> dict:
        return {"source": self.source, "criteria": self.criteria,
                "schedule_cron": self.schedule_cron, "channel": self.channel, "length": self.length}

    @classmethod
    def from_dict(cls, d: dict) -> DigestConfig:
        return cls(
            source=str(d.get("source", "")), criteria=str(d.get("criteria", "")),
            schedule_cron=str(d.get("schedule_cron", "")), channel=str(d.get("channel", "")),
            length=str(d.get("length", "짧게")),
        )


@dataclass
class DigestResult:
    repos: list[Repo]
    text: str
    delivered: bool
    blocked: str = ""  # 비었으면 정상, 채워졌으면 검증/전송 차단 사유(No-Silent-Stop)


def run_digest(
    config: DigestConfig,
    source: RepoSource,
    deliverer: Deliverer,
    *,
    verify: bool = True,
) -> DigestResult:
    """한 번 실행: 조회 → 유망 선별 → 렌더 → (검증) → 전송. cron이 이걸 매일 부른다."""
    top_n = length_count(config.length)
    repos = source.search(config.github_query(), max_n=max(20, top_n))
    promising = select_promising(repos, top_n=top_n)
    text = render_digest(promising, length=config.length)

    if not promising:
        # 빈 다이제스트를 조용히 보내지 않는다(No-Silent-Stop) — 사유 남기고 전송 보류.
        return DigestResult(promising, text, delivered=False, blocked="조회 결과 없음")
    if verify:
        # No-Pass: 보내기 전 digest 뱅크로 검증(날조 차단=충실성 + 슬롭). 막히면 *안 보낸다*.
        from polyrus.verifiers.registry import default_digest_bank

        claim = Claim("digest", text, kind="digest", meta={"repos": promising})
        verdict = default_digest_bank().run(claim, DoD(spec=config.source, frozen=True))
        if not verdict.passed:
            return DigestResult(promising, text, delivered=False, blocked=f"검증 차단: {verdict.blocker}")

    delivered = deliverer.send(text)
    return DigestResult(promising, text, delivered=delivered,
                        blocked="" if delivered else "전송 실패(채널 미설정?)")


def crontab_line(cron: str, command: str) -> str:
    """cron 표현 + 명령 → crontab 한 줄. (등록은 시스템 쓰기 → 게이트.)"""
    return f"{cron} {command}"
