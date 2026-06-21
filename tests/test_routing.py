"""멀티API 라우팅 UX(§6) — 작업별 추천 + 가용성 + 미설정 시 친절 요청. Gemini 없음."""
from __future__ import annotations

from polyrus.cli import main
from polyrus.routing import provider_available, route


def _which(present: set[str]):
    return lambda c: f"/usr/bin/{c}" if c in present else None


def test_claude_available_via_cli() -> None:
    assert provider_available("claude", env={}, which=_which({"claude"}))
    assert not provider_available("claude", env={}, which=_which(set()))
    # 키로도 가용.
    assert provider_available("claude", env={"ANTHROPIC_API_KEY": "x"}, which=_which(set()))


def test_openai_needs_key() -> None:
    assert provider_available("openai", env={"OPENAI_API_KEY": "x"}, which=_which(set()))
    assert not provider_available("openai", env={}, which=_which(set()))


def test_route_available_no_request() -> None:
    d = route("code", env={}, which=_which({"claude"}))
    assert d.provider == "claude" and d.available and d.request == ""
    assert d.use == "claude"


def test_route_unavailable_requests_with_reason_and_alternative() -> None:
    # codex 추천인데 미설치 → 친절 요청 + 대안으로 진행.
    d = route("cheap_divergence", env={}, which=_which({"claude"}))
    assert d.provider == "codex" and not d.available
    assert "나아요" in d.request and "없으면" in d.request  # 이유 + 대안
    assert d.use == "claude"  # 대안으로 진행(품질 약간↓)


def test_route_never_recommends_gemini() -> None:
    for t in ("code", "long_reasoning", "cheap_divergence", "default", "vision"):
        assert route(t, env={}, which=_which({"claude", "codex"})).provider != "gemini"


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli_route_outputs_recommendation(capsys) -> None:
    rc = main(["route", "code"])
    out = capsys.readouterr().out
    assert rc == 0 and "추천: claude" in out
