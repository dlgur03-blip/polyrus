"""전문가 스킬 흡수 → 검증 스킬 위키 (자가발전 메커니즘의 첫 실증).

글로벌 시스템이 스킬 99개를 *손으로* 쌓은 걸, Polyrus는 *검증된 것만* 위키에 흡수해
DEFAULT 단계가 당겨 쓰게 한다. 비개발자가 답할 수 없는 전문가 지식(AI 티 제거·팔레트·MoSCoW)을
'묻지 않고 채우는' 재료다.

출처는 실제 글로벌 스킬에서 추출(추측 아님):
- anti-ai-slop : ~/.claude/skills/design-review (AI Slop Detection 10종 블랙리스트)
                 + ~/.claude/commands/글쓰기.md (AI 균일성 깨기 규약)
- oklch-palette: 글로벌 CLAUDE.md 기술스택(디자인 시스템 OKLCH) 우선순위
- moscow       : mrd-writer MoSCoW 우선순위 패턴

흡수본은 source='absorbed:<원본>'으로 표시되고 verified=1(HOT)로 서빙된다. 큐레이션 루프가
미사용분을 나중에 강등한다(SkillStore.curate).
"""
from __future__ import annotations

from polyrus.memory import SkillStore

# design-review §9 'AI Slop Detection' 블랙리스트 + /글쓰기 불균일성 핵심 — 실제 추출.
_ANTI_SLOP = """\
[목표] 'AI가 만든 티'를 결정적으로 제거한다. (출처: design-review §9 + /글쓰기)

# 시각 슬롭 블랙리스트 (보이면 거절)
1. 보라/바이올렛/인디고 그라디언트 배경, blue→purple 색조합
2. 3열 기능 그리드(원형아이콘+볼드제목+2줄설명 ×3 대칭) — 가장 알아보기 쉬운 AI 레이아웃
3. 색 원 안의 아이콘을 섹션 장식으로(SaaS 스타터 템플릿 룩)
4. 전부 가운데정렬(text-align:center 남발)
5. 모든 요소에 같은 큰 border-radius(균일 버블 라운드)
6. 장식용 blob·떠다니는 원·물결 SVG 디바이더(빈 섹션은 콘텐츠로 채워라, 장식 말고)
7. 디자인 요소로서의 이모지(제목 속 로켓, 불릿 대신 이모지)
8. 카드 좌측 컬러 보더(border-left:3px solid accent)
9. 제네릭 히어로 카피("Welcome to X", "Unlock the power of...", "올인원 솔루션")
10. 쿠키커터 섹션 리듬(hero→3기능→후기→가격→CTA, 모든 섹션 같은 높이)
11. system-ui/-apple-system을 주 디스플레이 폰트로 — '타이포 포기' 신호. 진짜 서체를 골라라.

# 카피 불균일성 (출처 /글쓰기)
- AI는 균일하고 사람은 불균일하다: 문장 길이를 들쭉날쭉(단문 둘셋 뒤 장문 하나).
- 같은 결론을 표현만 바꿔 반복 금지. 핵심은 한 번 세게.
- 클리셰 비유(인생은 마라톤·동전의 양면) 금지. 일상에서 끌어온 비유.
- "결국~/요약하자면~/정리하면~" 마무리 신호 금지. 여운·새 질문으로 끊기.
- 날조 금지: 없는 연구·수치·일화 만들지 마라.

# 집행
- 결정적 체커(ai_slop)로 클리셰·이모지스팸·균일성을 1차 거른다(LLM-judge는 최후).
"""

_OKLCH = """\
[목표] 브랜드 느낌 한 단어 → 일관된 OKLCH 토큰 팔레트. (출처: 글로벌 디자인 시스템 우선순위)

- OKLCH로 명도(L)를 고정한 채 색상(H)만 돌려 *지각적으로 균일한* 스케일을 만든다.
- 토큰: --bg / --surface / --text / --accent / --accent-fg (의미 기반, 값 직접 X).
- 합격기준: 본문 대비 WCAG AA 4.5:1, 큰 텍스트 3:1. (contrast 검증기가 결정적으로 집행.)
- 다크모드: surface는 명도반전이 아니라 elevation, text는 off-white(#E0E0E0), accent 채도 10~20%↓.
- 팔레트 일관성: 비회색 색상 ≤12종, 중립은 따뜻/차가움 일관.
"""

_MOSCOW = """\
[목표] 기능 나열 → MoSCoW 우선순위 + 기능마다 합격기준. (출처: mrd-writer)

- Must / Should / Could / Won't(이번엔 안 함)로 분류. Must만 MVP 범위.
- 각 Must 기능은 *production-grade 합격기준*(검증기)을 동반한다 — 이게 마누스 회피 핵심.
  (예: '문의폼' → 전송 성공/실패 처리·스팸 차단·서버 검증까지 No-Pass로 집행.)
- Won't을 명시해 범위 폭발을 막는다(비례 원칙).
"""

_GH_PROMISING = """\
[목표] GitHub '유망함'을 *측정 가능*하게 정의. (비개발자 대신 채우는 전문가 기준)

- 스타 급상승: 최근 7일 ★ 증가율(절대 ★ 아님 — 신생도 잡힌다).
- 활발한 활동: 최근 커밋·이슈·PR 빈도(죽은 레포 제외).
- 새 릴리스: 최근 태그/릴리스 있는 것.
- 토픽/언어 필터로 노이즈 제거. 포크·아카이브·미러 제외.
- 기본 조합 = (스타 급상승 OR 새 릴리스) AND 최근 활동.
"""

_DIGEST_FORMAT = """\
[목표] 다이제스트 분량·형식 기본값. (묻지 않고 채택, 취향 1개만 받음)

- 짧게(기본): 항목당 3줄 — 이름·한 줄 요약·왜 유망한지(숫자). 상위 5개.
- 자세히: 항목당 5~7줄 + 링크 + 최근 변화. 상위 10개.
- 채널 맞춤: 텔레그램=마크다운 간결, 이메일=제목+본문, 슬랙=블록.
- 날조 금지: 없는 스타 수·요약 지어내지 마라(출처=실제 API 응답).
"""

# (위키 키, kind, goal=스킬이름, solution=가이드, source)
SEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("copy", "anti-ai-slop", _ANTI_SLOP, "absorbed:design-review+글쓰기"),
    ("palette", "oklch-palette", _OKLCH, "absorbed:design-system"),
    ("plan", "moscow", _MOSCOW, "absorbed:mrd-writer"),
)

DIGEST_SEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("criteria", "github-promising", _GH_PROMISING, "absorbed:github-trends"),
    ("format", "digest-format", _DIGEST_FORMAT, "absorbed:글쓰기"),
)

# 도메인 → 시드 묶음. 도메인별로 필요한 전문가 스킬만 흡수.
SEEDS_BY_DOMAIN: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "homepage": SEEDS,
    "digest": DIGEST_SEEDS,
}


def seed_homepage_skills(wiki: SkillStore, *, confidence: float = 0.9) -> list[int]:
    """홈페이지 도메인 DEFAULT 단계가 쓰는 전문가 스킬을 위키에 흡수(검증=HOT).

    멱등하지 않다(중복 호출 시 중복 삽입) — 호출자가 '한 번만' 보장하거나 빈 위키에 쓴다.
    영속 DB엔 `ensure_homepage_skills`를 써라(중복 방지). 반환: 삽입된 스킬 id 목록.
    """
    ids: list[int] = []
    for kind, name, solution, source in SEEDS:
        ids.append(
            wiki.record(
                kind=kind,
                goal=name,
                solution=solution,
                confidence=confidence,
                verified=True,
                source=source,
            )
        )
    return ids


def ensure_homepage_skills(wiki: SkillStore, *, confidence: float = 0.9) -> int:
    """홈페이지 도메인 멱등 시딩(하위호환 별칭)."""
    return ensure_skills_for(wiki, "homepage", confidence=confidence)


def ensure_skills_for(wiki: SkillStore, domain: str, *, confidence: float = 0.9) -> int:
    """영속 위키용 도메인별 멱등 시딩 — 이미 있는(이름 일치) 스킬은 건너뛴다. 반환: 새로 넣은 수."""
    added = 0
    for kind, name, solution, source in SEEDS_BY_DOMAIN.get(domain, ()):
        present = wiki._conn.execute(  # noqa: SLF001 - 같은 패키지 내부 접근
            "SELECT 1 FROM skills WHERE goal=? LIMIT 1", (name,)
        ).fetchone()
        if present:
            continue
        wiki.record(kind=kind, goal=name, solution=solution, confidence=confidence,
                    verified=True, source=source)
        added += 1
    return added
