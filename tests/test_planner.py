"""선제질문 엔진 — 뼈대를 걸어 동결 기획 산출. ASK/DEFAULT/VERIFY 전체 경로."""
from __future__ import annotations

from polyrus.memory import SkillStore
from polyrus.planner import ProactivePlanner, ScriptedAnswers, audit_questions
from polyrus.skeleton import HOMEPAGE
from polyrus.skills_seed import ensure_homepage_skills, seed_homepage_skills
from polyrus.types import Verdict


def _wiki() -> SkillStore:
    w = SkillStore(":memory:")
    seed_homepage_skills(w)
    return w


def _full_answers() -> ScriptedAnswers:
    return ScriptedAnswers(
        answers={
            "goal_action": "문의하기",
            "references": "stripe.com, linear.app",
            "tone_guide": "정돈된",      # DEFAULT(anti_slop)의 micro_question 취향
            "accent_section": "히어로",
            "palette": "신뢰",
            "features": "문의폼, 사례소개, 가격표",
        }
    )


def test_planner_asks_and_fills() -> None:
    p = ProactivePlanner(HOMEPAGE, wiki=_wiki())
    res = p.run(_full_answers())
    # ASK 4개는 사용자에게 묻고, DEFAULT 2개는 위키에서 채운다.
    assert res.asked == 4
    assert res.defaulted == 2
    # DEFAULT가 흡수된 전문가 스킬을 당겨왔다(자가발전 연결).
    slop_rec = next(r for r in res.records if r.step.id == "anti_slop")
    assert slop_rec.source.startswith("wiki:")
    assert "취향:정돈된" in slop_rec.value  # micro_question 반영


def test_plan_freezes_dod_with_acceptance() -> None:
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(_full_answers())
    assert res.dod.frozen is True
    # 각 단계 합격기준이 검증기 이름과 함께 properties로 동결됐다(굿하트 차단).
    joined = " ".join(res.dod.properties)
    assert "ai_slop" in joined and "contrast" in joined and "frame_alignment" in joined


def test_plan_to_task_for_nopass_loop() -> None:
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(_full_answers())
    task = res.to_task("home-1")
    assert task.id == "home-1" and len(task.items) == 1
    assert task.items[0].dod.frozen is True


def test_deterministic_verify_catches_slop_and_low_contrast() -> None:
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(_full_answers())
    bad = res.verify({
        "copy": "혁신적인 올인원 솔루션 🚀✨ unlock the power!",
        "palette": "#aaaaaa on #ffffff",
        "accent_count": 5,
        "sections": ["갤러리", "블로그"],
    })
    assert bad.passed is False  # 결정적 검증기가 막는다
    good = res.verify({
        "copy": "두 줄짜리 메모로 시작했다. 그게 전부였다.",
        "palette": "#111111 on #ffffff",
        "accent_count": 1,
        "sections": ["문의하기 폼", "문의하기 안내"],
    })
    assert good.passed is True
    assert all(r.verdict is Verdict.PASS for r in good.results)


def test_missing_required_answer_blocks_not_silently() -> None:
    # 필수(목적) 미응답 → 회복 질문 1회 → 그래도 없으면 blocker(No-Silent-Stop).
    ans = ScriptedAnswers(answers={"goal_action": "", "features": "문의폼"})
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(ans)
    assert res.blockers  # 조용히 통과하지 않는다
    assert "goal_action*" in ans.asked_log  # 회복 질문(*)이 실제로 나갔다


def test_recovery_question_resolves() -> None:
    ans = ScriptedAnswers(
        answers={"goal_action": "", "features": "문의폼"},
        clarifications={"goal_action": "예약하기"},
    )
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(ans)
    assert res.answers["goal_action"] == "예약하기"
    assert not res.blockers


def test_proportional_scope_asks_fewer() -> None:
    # 랜딩 스코프(min_weight=0.5): 3D 포인트(0.4) 질문이 사라진다.
    ans = _full_answers()
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki(), scope_min_weight=0.5).run(ans)
    assert "accent_section" not in res.answers


def test_works_without_wiki_via_fallback() -> None:
    # 위키 없어도 멈추지 않는다 — 의견형 스택 디폴트/스킬명으로 폴백.
    res = ProactivePlanner(HOMEPAGE, wiki=None).run(_full_answers())
    slop_rec = next(r for r in res.records if r.step.id == "anti_slop")
    assert slop_rec.source in ("fallback", "stack-default")


# ── 우회/변명 검증: '답을 했다 치고 넘어가는' 사고 방지 ─────────────────────────────
def test_evasive_answer_does_not_pass_silently() -> None:
    # 목적에 '아무거나' 회피 답 → 회복질문 → 그래도 회피면 blocker(조용히 통과 X).
    ans = ScriptedAnswers(
        answers={"goal_action": "아무거나 알아서", "features": "문의폼"},
        clarifications={"goal_action": "그냥 알아서요"},  # 회복해도 또 회피
    )
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(ans)
    assert res.blockers
    assert any("회피" in b for b in res.blockers)
    assert "goal_action*" in ans.asked_log  # 회복질문이 실제로 나갔다


def test_evasive_then_real_answer_resolves() -> None:
    ans = ScriptedAnswers(
        answers={"goal_action": "몰라요", "features": "문의폼"},
        clarifications={"goal_action": "예약하기"},  # 회복 시 진짜 결정
    )
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(ans)
    assert not res.blockers
    assert res.answers["goal_action"] == "예약하기"


# ── 질문 난이도 가드: 제품이 자기 기준을 통과해야 한다 ─────────────────────────────
def test_homepage_questions_pass_own_ease_bar() -> None:
    assert audit_questions(HOMEPAGE) == []  # 우리 질문은 우리 난이도 기준을 통과한다


def test_plan_carries_question_audit() -> None:
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(_full_answers())
    assert res.question_issues == []


def test_completion_evasion_caught_in_verify() -> None:
    res = ProactivePlanner(HOMEPAGE, wiki=_wiki()).run(_full_answers())
    bad = res.verify({"completion": "일단 됐습니다. 실패는 제 변경 때문이 아닙니다."})
    assert bad.passed is False
    ok = res.verify({"completion": "문의폼 구현 완료, 테스트 green."})
    assert ok.passed is True


def test_ensure_homepage_skills_idempotent() -> None:
    w = SkillStore(":memory:")
    assert ensure_homepage_skills(w) == 3
    assert ensure_homepage_skills(w) == 0  # 재호출 시 중복 없음
    assert w.count() == 3
