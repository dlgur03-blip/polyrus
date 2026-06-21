"""다이제스트 실행 엔진 — 조회→유망선별→렌더→검증→전송 (네트워크 0, 페이크로)."""
from __future__ import annotations

from polyrus.digest import (
    DigestConfig,
    DigestFaithfulnessVerifier,
    FakeDeliverer,
    FakeSource,
    Repo,
    build_github_query,
    length_count,
    render_digest,
    run_digest,
    select_promising,
)
from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.registry import default_digest_bank
from polyrus.planner import ProactivePlanner, ScriptedAnswers
from polyrus.skeleton import DIGEST


def _repos() -> list[Repo]:
    return [
        Repo("acme/llm-tools", "LLM 유틸 모음", 5200, "https://github.com/acme/llm-tools", language="Python"),
        Repo("foo/tiny", "작은 실험", 30, "https://github.com/foo/tiny"),
        Repo("bar/agent", "에이전트 프레임워크", 980, "https://github.com/bar/agent", language="Python"),
    ]


def test_build_github_query() -> None:
    q = build_github_query("python LLM 토픽", "스타 급상승")
    assert "LLM" in q and "language:python" in q and "fork:false" in q


def test_select_promising_by_stars() -> None:
    top = select_promising(_repos(), top_n=2)
    assert [r.full_name for r in top] == ["acme/llm-tools", "bar/agent"]


def test_length_count() -> None:
    assert length_count("짧게") == 5 and length_count("자세히") == 10


def test_render_no_fabrication() -> None:
    text = render_digest(select_promising(_repos(), top_n=2), length="짧게")
    assert "acme/llm-tools" in text and "⭐5,200" in text
    assert "https://" not in text  # 짧게=링크 없음
    detailed = render_digest(select_promising(_repos(), top_n=2), length="자세히")
    assert "https://github.com/acme/llm-tools" in detailed  # 자세히=링크 있음


def test_run_digest_delivers() -> None:
    cfg = DigestConfig(source="python LLM", length="짧게")
    deliverer = FakeDeliverer()
    result = run_digest(cfg, FakeSource(_repos()), deliverer)
    assert result.delivered and not result.blocked
    assert len(result.repos) == 3 and len(deliverer.sent) == 1
    assert "llm-tools" in deliverer.sent[0]


def test_empty_result_not_sent_silently() -> None:
    deliverer = FakeDeliverer()
    result = run_digest(DigestConfig(source="x"), FakeSource([]), deliverer)
    assert not result.delivered and result.blocked == "조회 결과 없음"
    assert deliverer.sent == []  # 빈 다이제스트를 조용히 보내지 않는다


def test_delivery_failure_reported() -> None:
    result = run_digest(DigestConfig(source="x"), FakeSource(_repos()), FakeDeliverer(ok=False))
    assert not result.delivered and "전송 실패" in result.blocked


# ── No-Pass 충실성: 출력이 조회 데이터를 벗어나면(날조) 차단 ───────────────────────
_DOD = DoD(spec="x", frozen=True)


def test_faithfulness_passes_real_render() -> None:
    repos = select_promising(_repos(), top_n=2)
    text = render_digest(repos, length="짧게")
    v = DigestFaithfulnessVerifier().verify(Claim("d", text, kind="digest", meta={"repos": repos}), _DOD)
    assert v.verdict is Verdict.PASS


def test_faithfulness_catches_fabricated_repo() -> None:
    repos = select_promising(_repos(), top_n=2)
    fake = "📬 오늘의 GitHub 유망 레포\n\n1. evil/made-up — ⭐999,999\n   가짜"
    v = DigestFaithfulnessVerifier().verify(Claim("d", fake, kind="digest", meta={"repos": repos}), _DOD)
    assert v.verdict is Verdict.FAIL and "없는 레포" in v.detail


def test_faithfulness_catches_inflated_stars() -> None:
    repos = select_promising(_repos(), top_n=1)  # acme/llm-tools ⭐5,200
    inflated = "1. acme/llm-tools — ⭐9,999,999\n   뻥튀기"
    v = DigestFaithfulnessVerifier().verify(Claim("d", inflated, kind="digest", meta={"repos": repos}), _DOD)
    assert v.verdict is Verdict.FAIL and "스타 불일치" in v.detail


def test_digest_bank_runs_faithfulness_and_slop() -> None:
    # 한 digest claim에 충실성 + 슬롭 둘 다 적용된다.
    repos = select_promising(_repos(), top_n=2)
    text = render_digest(repos, length="짧게")
    verdict = default_digest_bank().run(Claim("d", text, kind="digest", meta={"repos": repos}), _DOD)
    details = " ".join(r.detail for r in verdict.results)
    assert "일치" in details and "slop" in details.lower()  # 충실성 + 슬롭 둘 다 돌았다
    assert verdict.passed and len(verdict.results) == 2


def test_run_digest_blocks_on_fabrication(monkeypatch) -> None:
    # 렌더가 날조하면 전송 안 됨(No-Pass 차단).
    import polyrus.digest as dg

    monkeypatch.setattr(dg, "render_digest", lambda repos, **k: "1. evil/fake — ⭐999,999\n   x")
    deliverer = FakeDeliverer()
    result = run_digest(DigestConfig(source="x"), FakeSource(_repos()), deliverer)
    assert not result.delivered and "검증 차단" in result.blocked
    assert deliverer.sent == []  # 날조는 발송 안 함


# ── plan → config: digest 기획이 실행 설정으로 이어진다 ───────────────────────────
def test_plan_to_digest_config() -> None:
    ans = ScriptedAnswers(answers={
        "source": "python LLM", "criteria": "스타 급상승", "schedule": "매일 아침 9시",
        "channel": "텔레그램", "length": "자세히",
    })
    res = ProactivePlanner(DIGEST).run(ans)
    cfg = res.digest_config()
    assert cfg.source == "python LLM"
    assert cfg.schedule_cron == "0 9 * * *"
    assert cfg.channel == "telegram"
    assert cfg.length == "자세히"
    # 라운드트립.
    assert DigestConfig.from_dict(cfg.to_dict()).schedule_cron == "0 9 * * *"
