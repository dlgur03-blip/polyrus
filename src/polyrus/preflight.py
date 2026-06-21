"""환경 프리플라이트 — '기본 프로그램(Python·Node 등)이 없을 때' 초보자 온보딩.

핵심 구분(회피검증의 사촌):
  - 도구가 *진짜* 없음(which 실패)  → 환경 미비(사실). 초보자 팝업 → 안내 설치. (변명 아님)
  - 도구는 있는데 모델이 '환경 탓'  → 변명(EvasionVerifier가 잡음).
이 결정적 프리플라이트(shutil.which 한 줄)가 둘을 가른다 — 그래서 '환경 미비'를 코드 FAIL이나
변명과 섞지 않고 별도 종료사유(Termination.ENV_BLOCKED)로 라우팅한다.

UX 원칙(질문난이도 가드의 환경판): 스택트레이스 금지. 'ModuleNotFoundError'를
'이 기능엔 X가 필요해요, 설치할까요?'로 번역한다.

3단계 게이팅(browser.py 읽기/쓰기 비대칭 재사용):
  - auto   : 프로젝트-로컬(venv·pip) — 부수효과 작음. 확인 후 우리가 실행.
  - gated  : 시스템 쓰기(brew·apt) — 승인 필수·멱등·감사. 패키지 매니저 있을 때만.
  - manual : 사용자가 직접(설치 관리자 다운로드) — 가장 쉬운 경로(링크/한 줄)를 제시.
"""
from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

OS = str  # 'darwin' | 'linux' | 'windows' | 'unknown'


def detect_os(platform: str | None = None) -> OS:
    p = platform if platform is not None else sys.platform
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    if p.startswith("win"):
        return "windows"
    return "unknown"


@dataclass(frozen=True)
class Tool:
    """필요 도구 1종 + OS별 설치 방법."""

    name: str                       # 'node'
    cmd: str                        # which 대상 ('node')
    why: str                        # '웹사이트를 빌드·실행하려면 필요해요'
    pkg_install: dict[OS, str] = field(default_factory=dict)  # 패키지매니저 한 줄(gated)
    manual_url: str = ""            # 직접 설치 페이지(manual)


# 알려진 기본 도구 레지스트리. (도메인 뼈대가 requires로 이 이름들을 가리킨다.)
TOOLS: dict[str, Tool] = {
    "python3": Tool(
        "python3", "python3", "코드를 실행하고 검증하려면 필요해요",
        pkg_install={"darwin": "brew install python", "linux": "sudo apt install -y python3 python3-venv"},
        manual_url="https://www.python.org/downloads/",
    ),
    "node": Tool(
        "node", "node", "웹사이트를 빌드하고 미리 보려면 필요해요",
        pkg_install={"darwin": "brew install node", "linux": "sudo apt install -y nodejs npm"},
        manual_url="https://nodejs.org/en/download",
    ),
    "git": Tool(
        "git", "git", "코드 버전을 저장·배포하려면 필요해요",
        pkg_install={"darwin": "brew install git", "linux": "sudo apt install -y git"},
        manual_url="https://git-scm.com/downloads",
    ),
}

_PKG_MGRS = ("brew", "apt", "apt-get")


@dataclass(frozen=True)
class InstallPlan:
    tier: str            # 'auto' | 'gated' | 'manual'
    command: str         # 실행/안내할 명령(또는 manual 링크)
    manual_url: str = ""


def install_plan(tool: Tool, os_name: OS, *, has_pkg_mgr: bool) -> InstallPlan:
    """패키지매니저가 있으면 gated(승인 후 한 줄), 없으면 manual(직접 설치 링크)."""
    cmd = tool.pkg_install.get(os_name, "")
    if cmd and has_pkg_mgr:
        return InstallPlan("gated", cmd, tool.manual_url)
    return InstallPlan("manual", tool.manual_url or cmd, tool.manual_url)


@dataclass
class MissingTool:
    tool: Tool
    plan: InstallPlan

    @property
    def message(self) -> str:
        """초보자용 친절 메시지(스택트레이스 금지)."""
        head = f"'{self.tool.name}'가 설치돼 있지 않아요 — {self.tool.why}."
        if self.plan.tier == "gated":
            return f"{head}\n  제가 설치할까요? → {self.plan.command}"
        return f"{head}\n  여기서 설치하세요: {self.plan.manual_url or self.plan.command}"


@dataclass
class PreflightReport:
    present: list[str] = field(default_factory=list)
    missing: list[MissingTool] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def popup(self) -> str:
        """초보자 팝업 텍스트 — 빠진 것 + 설치 안내를 한 화면에."""
        if self.ok:
            return "✅ 필요한 프로그램이 모두 준비됐어요."
        lines = ["🔧 시작 전에 몇 가지가 필요해요:", ""]
        for m in self.missing:
            lines.append(f"• {m.message}")
        return "\n".join(lines)


def _has_pkg_mgr(which: Callable[[str], str | None]) -> bool:
    return any(which(p) for p in _PKG_MGRS)


def preflight_check(
    tool_names: list[str],
    *,
    which: Callable[[str], str | None] | None = None,
    platform: str | None = None,
) -> PreflightReport:
    """필요한 도구가 PATH에 있는지 결정적 확인. which 주입으로 테스트 가능(기본은 호출 시점 shutil.which)."""
    which = which or shutil.which
    os_name = detect_os(platform)
    has_mgr = _has_pkg_mgr(which)
    report = PreflightReport()
    for name in tool_names:
        tool = TOOLS.get(name) or Tool(name, name, "이 작업에 필요해요")
        if which(tool.cmd):
            report.present.append(name)
        else:
            report.missing.append(MissingTool(tool, install_plan(tool, os_name, has_pkg_mgr=has_mgr)))
    return report


# ── 창발적(mid-build) 환경 에러 번역: 샌드박스/실행이 토해낸 메시지를 친절하게 ──────────
_NO_MODULE = re.compile(r"No module named ['\"]?([\w.]+)")
_CMD_COLON = re.compile(r"command not found:\s*(\w+)")        # zsh: 'command not found: node'
_COLON_CMD = re.compile(r"(\w+):\s*command not found")         # bash: 'node: command not found'
_ENOENT = re.compile(r"ENOENT.*?'(\w+)'")


def translate_toolchain_error(text: str) -> str | None:
    """빌드 중 터진 에러가 *환경 미비*면 친절 메시지로 번역, 아니면 None(=진짜 결함/변명).

    None을 주면 환경 탓이 아니다 → 코드 FAIL 또는 변명으로 다뤄라(회피검증).
    """
    m = _NO_MODULE.search(text)
    if m:
        return f"이 기능엔 Python 패키지 '{m.group(1)}'가 필요해요. 설치할까요?"
    m = _CMD_COLON.search(text) or _COLON_CMD.search(text)
    if m:
        return f"'{m.group(1)}' 프로그램이 설치돼 있지 않아요. 설치가 필요해요."
    m = _ENOENT.search(text)
    if m:
        return f"'{m.group(1)}' 프로그램을 찾을 수 없어요. 설치가 필요해요."
    if "ModuleNotFoundError" in text:
        return "필요한 Python 패키지가 빠졌어요. 설치할까요?"
    return None
