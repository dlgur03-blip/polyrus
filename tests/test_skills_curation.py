"""스킬 위키 큐레이션 루프(§4-1) + 전문가 스킬 흡수(자가발전 첫 실증)."""
from __future__ import annotations

from polyrus.memory import SkillStore
from polyrus.skills_seed import seed_homepage_skills


def test_seed_absorbs_expert_skills_as_verified() -> None:
    w = SkillStore(":memory:")
    ids = seed_homepage_skills(w)
    assert len(ids) == 3
    assert w.verified_count() == 3
    # 흡수본은 출처가 표시된다(자가발전 추적).
    slop = w.recall_default("anti-ai-slop")
    assert slop is not None and slop.source.startswith("absorbed:")
    assert "블랙리스트" in slop.solution  # design-review 실제 추출


def test_recall_default_only_serves_verified() -> None:
    w = SkillStore(":memory:")
    [sid, *_] = seed_homepage_skills(w)
    w.demote(sid)  # COLD 강등
    assert w.recall_default("anti-ai-slop") is None  # 강등분은 서빙 안 함
    w.promote(sid)  # 다시 HOT
    assert w.recall_default("anti-ai-slop") is not None


def test_curate_demotes_unused() -> None:
    w = SkillStore(":memory:")
    seed_homepage_skills(w)
    # 하나만 사용(recall→uses 누적), 나머지는 미사용.
    w.recall_default("oklch-palette")
    demoted = w.curate(min_uses=1)
    assert demoted == 2  # 안 쓰인 anti-ai-slop·moscow 강등
    assert w.verified_count() == 1
    assert w.recall_default("oklch-palette") is not None  # 쓰인 건 HOT 유지


def test_backward_compat_record() -> None:
    # 기존 시그니처(verified/source 없이)도 동작 — session.py 호환.
    w = SkillStore(":memory:")
    w.record(kind="code", goal="x", solution="y", confidence=0.9)
    assert w.verified_count() == 1
