"""환경 프리플라이트 — 초보자 온보딩(결정적 도구 검사 + 친절 번역 + 설치 게이팅)."""
from __future__ import annotations

from polyrus.planner import ProactivePlanner, ScriptedAnswers
from polyrus.preflight import (
    detect_os,
    install_plan,
    preflight_check,
    translate_toolchain_error,
)
from polyrus.session import Session
from polyrus.skeleton import HOMEPAGE
from polyrus.types import Termination


def _which(present: set[str]):
    return lambda cmd: f"/usr/bin/{cmd}" if cmd in present else None


def test_detect_os() -> None:
    assert detect_os("darwin") == "darwin"
    assert detect_os("linux") == "linux"
    assert detect_os("win32") == "windows"


def test_all_present_ok() -> None:
    rep = preflight_check(["node", "git"], which=_which({"node", "git", "brew"}), platform="darwin")
    assert rep.ok
    assert "준비됐어요" in rep.popup


def test_missing_tool_friendly_popup_no_stacktrace() -> None:
    rep = preflight_check(["node", "git"], which=_which({"git", "brew"}), platform="darwin")
    assert not rep.ok
    assert len(rep.missing) == 1 and rep.missing[0].tool.name == "node"
    # 스택트레이스가 아니라 친절 안내 + 설치 명령.
    assert "node" in rep.popup and "brew install node" in rep.popup
    assert "Error" not in rep.popup and "Traceback" not in rep.popup


def test_gated_vs_manual_by_pkg_manager() -> None:
    from polyrus.preflight import TOOLS

    # 패키지매니저 있으면 gated(한 줄 설치), 없으면 manual(다운로드 링크).
    gated = install_plan(TOOLS["node"], "darwin", has_pkg_mgr=True)
    assert gated.tier == "gated" and "brew" in gated.command
    manual = install_plan(TOOLS["node"], "darwin", has_pkg_mgr=False)
    assert manual.tier == "manual" and manual.manual_url.startswith("https://")


def test_translate_toolchain_error() -> None:
    assert "requests" in (translate_toolchain_error("ModuleNotFoundError: No module named 'requests'") or "")
    assert "node" in (translate_toolchain_error("zsh: command not found: node") or "")
    assert "node" in (translate_toolchain_error("node: command not found") or "")
    # 진짜 결함/변명은 None(환경 탓 아님) → 회피검증/코드검증으로 넘긴다.
    assert translate_toolchain_error("AssertionError: expected 20 got 19") is None


# ── Session 게이트: 빌드 전 환경 미비면 ENV_BLOCKED(크래시 아님) ─────────────────────
class _FakeModel:
    def complete(self, prompt: str, *, system: str = "", temperature: float = 0.0) -> str:
        return "작은 가게입니다. 편하게 오세요."


def _task():
    ans = ScriptedAnswers(answers={
        "goal_action": "문의하기", "references": "stripe.com", "tone_guide": "정돈된",
        "accent_section": "히어로", "palette": "신뢰", "features": "문의폼",
    })
    return ProactivePlanner(HOMEPAGE).run(ans).to_task("b")


def test_session_env_blocked_when_tool_missing() -> None:
    # 존재하지 않는 도구를 요구 → 빌드 안 하고 ENV_BLOCKED로 안내.
    sess = Session.for_homepage_build(_FakeModel(), preflight_tools=["definitely-absent-xyz"])
    result = sess.run(_task())
    assert result.termination is Termination.ENV_BLOCKED
    assert not any(i.closed for i in result.items)
    assert all(i.escalated for i in result.items)  # 조용한 크래시 아님 — 명시적 보류


def test_session_proceeds_when_no_preflight() -> None:
    sess = Session.for_homepage_build(_FakeModel(), preflight_tools=[])
    result = sess.run(_task())
    assert result.termination is Termination.VERIFIED_COMPLETE
