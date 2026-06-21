"""도메인 뼈대 — 삼분 분류·비례 가지치기·의견형 스택 디폴트."""
from __future__ import annotations

from polyrus.skeleton import HOMEPAGE, Classification, get_skeleton


def test_homepage_has_full_sequence() -> None:
    ids = [s.id for s in HOMEPAGE.steps]
    assert ids == ["purpose", "references", "anti_slop", "accent", "palette", "features"]


def test_classification_partition() -> None:
    asks = HOMEPAGE.by_class(Classification.ASK)
    defaults = HOMEPAGE.by_class(Classification.DEFAULT)
    # 비개발자가 답할 것은 ASK, 전문가 지식은 DEFAULT.
    assert {s.id for s in asks} == {"purpose", "references", "accent", "features"}
    assert {s.id for s in defaults} == {"anti_slop", "palette"}


def test_opinionated_stack_defaults() -> None:
    # §4-2: 묻지 않고 채택하는 의견형 디폴트(글로벌 스택 우선순위와 정렬).
    assert "Next.js" in HOMEPAGE.stack_defaults["framework"]
    assert "Scrapling" in HOMEPAGE.stack_defaults["scraping"]


def test_proportional_pruning_drops_decoration() -> None:
    # 비례 원칙: 가벼운 스코프(랜딩)는 저-weight 장식 질문(3D 포인트, weight=0.4)을 자른다.
    light = HOMEPAGE.scoped(min_weight=0.5)
    assert "accent" not in [s.id for s in light.steps]
    assert "purpose" in [s.id for s in light.steps]  # 필수는 남는다
    assert HOMEPAGE.stack_defaults == light.stack_defaults  # 디폴트는 보존


def test_deterministic_first() -> None:
    # §4-3: 검증 단계 다수가 결정적이어야 한다(LLM은 최후).
    det = [s for s in HOMEPAGE.steps if s.deterministic]
    assert len(det) >= 4


def test_registry_lookup() -> None:
    assert get_skeleton("homepage") is HOMEPAGE
    try:
        get_skeleton("없는도메인")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("미등록 도메인은 KeyError여야 한다")
