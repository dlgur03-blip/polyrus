"""다이제스트 도메인 — 뼈대 시스템이 홈페이지 전용이 아님을 증명.

'매일 9시에 GitHub 유망한 거 요약해서 보내줘' → Polyrus가 '어떻게 보내줄까?'를 묻고,
스케줄·채널을 결정적으로 검증한다.
"""
from __future__ import annotations

from polyrus.memory import SkillStore
from polyrus.planner import ProactivePlanner, ScriptedAnswers
from polyrus.skeleton import DIGEST, get_skeleton
from polyrus.skills_seed import ensure_skills_for
from polyrus.types import Verdict


def _wiki() -> SkillStore:
    w = SkillStore(":memory:")
    ensure_skills_for(w, "digest")
    return w


def _answers() -> ScriptedAnswers:
    return ScriptedAnswers(answers={
        "source": "python LLM 토픽",
        "criteria": "스타 급상승",        # DEFAULT micro_question 취향
        "schedule": "매일 아침 9시",
        "channel": "텔레그램",
        "length": "짧게",                  # DEFAULT micro_question 취향
    })


def test_digest_registered_and_asks_delivery() -> None:
    assert get_skeleton("digest") is DIGEST
    # 사용자가 예측한 그 질문 — '어떻게 보내줄까?'.
    chan = next(s for s in DIGEST.steps if s.id == "channel")
    assert "어떻게 보내줄까" in chan.question


def test_digest_plan_asks_and_fills() -> None:
    res = ProactivePlanner(DIGEST, wiki=_wiki()).run(_answers())
    assert res.asked == 3       # source·schedule·channel
    assert res.defaulted == 2   # criteria·length (위키에서 흡수)
    crit = next(r for r in res.records if r.step.id == "criteria")
    assert crit.source.startswith("wiki:")  # github-promising 흡수본 당겨옴


def test_digest_verifies_schedule_and_channel() -> None:
    res = ProactivePlanner(DIGEST, wiki=_wiki()).run(_answers())
    v = res.verify({})
    # 스케줄은 cron으로 확정(PASS).
    assert any(r.tier.value == "t1_execution" and r.verdict is Verdict.PASS
               and "cron" in r.detail for r in v.results)
    # 채널은 인식되나 설정 미비 → INCONCLUSIVE(키 요청), FAIL 아님.
    assert any(r.verdict is Verdict.INCONCLUSIVE and "토큰" in r.detail for r in v.results)


def test_digest_ambiguous_schedule_surfaces() -> None:
    ans = ScriptedAnswers(answers={
        "source": "python", "criteria": "스타", "schedule": "자주", "channel": "텔레그램", "length": "짧게",
    })
    res = ProactivePlanner(DIGEST, wiki=_wiki()).run(ans)
    v = res.verify({})
    # 시각 모호 → INCONCLUSIVE로 표면화(조용히 넘어가지 않음).
    assert any(r.verdict is Verdict.INCONCLUSIVE and "몇 시" in r.detail for r in v.results)
