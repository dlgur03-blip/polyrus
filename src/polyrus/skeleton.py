"""도메인 뼈대 (선제질문 기획 UX의 골격) — 비개발자의 머릿속을 한 질문씩 꺼낸다.

전제(빌더 튜닝과의 비교에서 도출): 전문가는 뼈대를 머릿속에 갖고 있어서 "묻지 말고 실행"이
맞다. 비개발자는 그 뼈대가 없으므로 질문이 *유일한 가치*다. 그래서 Polyrus는 전문가가 암묵적으로
들고 있는 뼈대를 **외부화**해 순차로 빌려준다.

삼분 프레임(글로벌 규칙 → Polyrus 번역):
- ASK     : 비개발자만 답할 수 있는 것(목적·취향·제약). 반드시 묻는다.
- DEFAULT : 전문가 지식이라 비개발자가 답 못 하는 것. 묻지 말고 *검증된 스킬 위키*에서 채운다.
- VERIFY  : 사람도 모델도 못 믿는 것. 결정적 검증기로 집행한다.

비례 원칙(글로벌 가드레일 차용): "나중에 다시 안 볼 답이면 묻지 마라." 질문 수는 프로젝트
무게에 비례한다 — `Skeleton.scoped()`가 가벼운 스코프에서 저-weight 단계를 가지치기한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Classification(Enum):
    """선제질문 단계의 삼분 — 묻거나 / 채워주거나 / 검증하거나."""

    ASK = "ask"          # 비개발자만 답할 수 있음 → 묻는다
    DEFAULT = "default"  # 전문가 지식 → 검증 스킬 위키에서 채운다
    VERIFY = "verify"    # 못 믿음 → 결정적 검증기로 집행한다


@dataclass(frozen=True)
class SkeletonStep:
    """뼈대의 한 마디. 분류에 따라 질문/디폴트/검증 중 하나로 처리된다."""

    id: str
    title: str
    classification: Classification
    output_key: str                 # 이 단계가 plan에 채우는 키
    acceptance: str                 # 합격기준(이게 곧 검증 항목)
    verifier: str = ""              # VERIFY 연결: 검증기 이름(deterministic 우선)
    deterministic: bool = True      # §4-3: 결정적(룰·파서)인가 — LLM-judge는 최후
    weight: float = 1.0             # 비례 가지치기용(1=필수, 낮을수록 먼저 잘림)
    question: str = ""              # ASK: 사용자에게 던지는 문구
    micro_question: str = ""        # DEFAULT인데 취향 1개만 곁들일 때
    default_skill: str = ""         # DEFAULT: 위키에서 당겨올 스킬 이름
    research: bool = False          # ASK인데 답을 받아 *읽어서 조사*하는 단계(레퍼런스 등)

    @property
    def required(self) -> bool:
        return self.weight >= 1.0


@dataclass(frozen=True)
class Skeleton:
    """한 도메인의 질문 시퀀스 + 의견형 스택 디폴트(§4-2)."""

    domain: str
    steps: tuple[SkeletonStep, ...]
    stack_defaults: dict[str, str] = field(default_factory=dict)
    requires: tuple[str, ...] = ()  # 이 도메인 빌드/실행에 필요한 기본 도구(프리플라이트 대상)

    def scoped(self, *, min_weight: float = 0.0) -> Skeleton:
        """비례 원칙: 가벼운 스코프에선 저-weight(출력을 거의 안 바꾸는) 단계를 잘라낸다.

        예: 랜딩 1장(min_weight=0.5)은 '3D 포인트' 같은 장식 질문을 묻지 않는다.
        """
        kept = tuple(s for s in self.steps if s.weight >= min_weight)
        return Skeleton(self.domain, kept, dict(self.stack_defaults), self.requires)

    def by_class(self, c: Classification) -> tuple[SkeletonStep, ...]:
        return tuple(s for s in self.steps if s.classification is c)


# ─────────────────────────────────────────────────────────────────────────────
# 홈페이지 도메인 (첫 파일럿). 0~5단계 — 비교 기획안 §3 시퀀스의 코드화.
# ASK=목적·레퍼런스·포인트·컬러·기능,  DEFAULT=AI티제거·팔레트·MoSCoW,  VERIFY=각 단계 합격기준.
# ─────────────────────────────────────────────────────────────────────────────
HOMEPAGE = Skeleton(
    domain="homepage",
    requires=("node", "git"),  # Next.js 빌드·실행 + 버전관리 (Python은 polyrus 자체가 보장)
    stack_defaults={
        # 의견형 디폴트(글로벌 CLAUDE.md 기술스택 우선순위와 정렬) — 묻지 않고 기본 채택.
        "framework": "Next.js (App Router)",
        "styling": "Tailwind + OKLCH 디자인 토큰",
        "deploy": "Vercel",
        "scraping": "Scrapling (레퍼런스 수집)",
    },
    steps=(
        SkeletonStep(
            id="purpose",
            title="목적",
            classification=Classification.ASK,
            output_key="goal_action",
            question="이 홈페이지로 방문자가 하길 바라는 *단 하나의* 행동은? (예: 문의하기·구매·예약)",
            acceptance="모든 후속 산출물이 이 단일 행동에 정렬되어야 한다",
            verifier="frame_alignment",  # frame.py 계열 — 진짜 의도 vs 영합
            deterministic=True,
            weight=1.0,
        ),
        SkeletonStep(
            id="references",
            title="레퍼런스",
            classification=Classification.ASK,
            output_key="references",
            question="좋아 보이는 사이트 2~3개를 알려주세요. 없으면 업종만 알려주시면 찾아오겠습니다.",
            acceptance="레퍼런스가 목적과 정렬되고 출처가 실재해야 한다",
            verifier="provenance",  # T3 — 출처/존재 대조(Scrapling)
            deterministic=False,
            weight=0.8,
            research=True,  # 답을 받아 읽어서 조사(읽기우선)
        ),
        SkeletonStep(
            id="anti_slop",
            title="AI 티 제거",
            classification=Classification.DEFAULT,  # 비개발자가 답 못 함 → 위키에서 채움
            output_key="tone_guide",
            default_skill="anti-ai-slop",  # design-review + /글쓰기 흡수본
            micro_question="톤은 '정돈된' 쪽인가요, '개성 강한' 쪽인가요?",
            acceptance="AI-slop 체커 통과(클리셰·이모지스팸·균일문장 없음)",
            verifier="ai_slop",  # 결정적 룰 체커
            deterministic=True,
            weight=0.9,
        ),
        SkeletonStep(
            id="accent",
            title="3D 아이콘 포인트",
            classification=Classification.ASK,
            output_key="accent_section",
            question="3D 아이콘으로 포인트 줄 섹션 하나만 고르세요 (히어로 / 기능소개 / CTA).",
            acceptance="포인트 요소는 1~2개로 제한(과용 금지)",
            verifier="accent_count",  # 결정적 카운트 체크
            deterministic=True,
            weight=0.4,  # 장식 — 가벼운 스코프에선 가지치기 대상
        ),
        SkeletonStep(
            id="palette",
            title="컬러",
            classification=Classification.DEFAULT,
            output_key="palette",
            default_skill="oklch-palette",
            micro_question="브랜드 느낌을 한 단어로 (예: 신뢰·활기·고급).",
            acceptance="WCAG AA 대비 충족 + 팔레트 일관성",
            verifier="contrast",  # 결정적 대비 계산
            deterministic=True,
            weight=0.7,
        ),
        SkeletonStep(
            id="features",
            title="기능 리스트",
            classification=Classification.ASK,
            output_key="features",
            question="꼭 필요한 기능을 말해주세요 (쉼표로 구분).",
            acceptance="각 기능마다 production-grade 합격기준(검증기) 동반 — No-Pass로 집행",
            verifier="nopass",  # 실행 단계에서 기존 No-Pass 루프가 집행
            deterministic=False,
            weight=1.0,
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 다이제스트/자동화 도메인 (2번째 — 뼈대 시스템이 홈페이지 전용이 아님을 증명).
# 예: "매일 9시에 GitHub 유망한 거 요약해서 보내줘". 비개발자가 못 채우는 건 DEFAULT,
# 결정할 건 ASK(특히 '어떻게 보내줄까?'=채널), 실행가능성은 VERIFY(cron·채널설정).
# ─────────────────────────────────────────────────────────────────────────────
DIGEST = Skeleton(
    domain="digest",
    requires=(),  # 예약 자동화는 서버측(GitHub API + 채널 API) — 로컬 도구 불요
    stack_defaults={
        "source_api": "GitHub REST (search/trending)",
        "scheduler": "cron (서버측)",
        "runtime": "Polyrus 예약 잡",
    },
    steps=(
        SkeletonStep(
            id="source",
            title="소스",
            classification=Classification.ASK,
            output_key="source",
            question="GitHub에서 뭘 받아볼까요? (예: 트렌딩 전체 / 특정 언어(python) / 토픽·키워드(LLM))",
            acceptance="수집 대상이 명확한 GitHub 질의로 확정되어야 한다",
            verifier="provenance",  # 소스가 실제 조회 가능한지(T3, 나중에 GitHub API)
            deterministic=False,
            weight=1.0,
        ),
        SkeletonStep(
            id="criteria",
            title="유망 기준",
            classification=Classification.DEFAULT,  # '유망함'을 비개발자가 정의 못 함 → 채워줌
            output_key="criteria",
            default_skill="github-promising",
            micro_question="어떤 게 '유망'인가요? (스타 급상승 / 새 릴리스 / 활발한 활동 중 끌리는 것)",
            acceptance="유망 판단 기준이 측정 가능해야 한다(스타증가율·최근활동 등)",
            verifier="",
            deterministic=True,
            weight=0.7,
        ),
        SkeletonStep(
            id="schedule",
            title="언제",
            classification=Classification.ASK,
            output_key="schedule",
            question="언제 보낼까요? (예: 매일 9시 / 평일 아침 9시 / 매주 월요일 9시)",
            acceptance="실행 가능한 cron으로 확정되어야 한다",
            verifier="schedule",  # 결정적 자연어→cron 파싱·검증
            deterministic=True,
            weight=1.0,
        ),
        SkeletonStep(
            id="channel",
            title="전달 방법",
            classification=Classification.ASK,
            output_key="channel",
            question="어떻게 보내줄까요? (텔레그램 / 이메일 / 슬랙)",  # ← 사용자가 예측한 그 질문
            acceptance="지원 채널이고 실제 전송 설정(키)까지 갖춰져야 한다",
            verifier="channel",  # 채널 인식 + 설정 검사(미비면 키 요청)
            deterministic=True,
            weight=1.0,
        ),
        SkeletonStep(
            id="length",
            title="분량",
            classification=Classification.DEFAULT,
            output_key="length",
            default_skill="digest-format",
            micro_question="요약 분량은? (짧게 3줄 / 자세히)",
            acceptance="분량·형식이 일관되게 정의되어야 한다",
            verifier="",
            deterministic=True,
            weight=0.4,
        ),
    ),
)


REGISTRY: dict[str, Skeleton] = {HOMEPAGE.domain: HOMEPAGE, DIGEST.domain: DIGEST}


def get_skeleton(domain: str) -> Skeleton:
    if domain not in REGISTRY:
        raise KeyError(f"미등록 도메인 뼈대: {domain!r} (있는 것: {sorted(REGISTRY)})")
    return REGISTRY[domain]
