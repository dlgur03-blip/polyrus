"""멀티API 라우팅 UX(§6) — '이 작업엔 X가 나아요, 쓸까요?'를 이유·대안과 함께.

전제(사용자 셋업): 메인은 Claude Max + Codex 계정 CLI(키 불요). 추가 provider(openai 등)는
*그 작업에 진짜 나을 때만* 추천하고, 미설정이면 키/설치를 요청한다 — preflight·채널 온보딩과 같은
사상(부재를 크래시 말고 친절 요청으로). 추천엔 항상 *이유 + 대안(없으면 이걸로, 품질 약간↓)*.

가용성은 결정적: 계정 CLI는 PATH(which), 키형 provider는 env. 테스트는 which/env 주입.
"""
from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from polyrus.models import ModelRecommendation, recommend_provider


def provider_available(
    provider: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> bool:
    """provider를 실제로 쓸 수 있나. claude/codex=계정 CLI(PATH) 또는 키, openai=키."""
    env = env if env is not None else os.environ
    which = which or shutil.which
    if provider == "claude":
        return bool(which("claude") or env.get("ANTHROPIC_API_KEY"))
    if provider == "codex":
        return bool(which("codex"))
    if provider == "openai":
        return bool(env.get("OPENAI_API_KEY"))
    return False


@dataclass
class RoutingDecision:
    task_kind: str
    provider: str
    reason: str
    available: bool
    fallback: str
    request: str = ""   # 미가용 시 친절 요청(이유+방법+대안), 가용이면 ''

    @property
    def use(self) -> str:
        """실제로 쓸 provider — 추천이 가용하면 그것, 아니면 대안(품질 약간↓)."""
        return self.provider if self.available else self.fallback


def route(
    task_kind: str,
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> RoutingDecision:
    """작업 → 추천 provider + 가용성. 미가용이면 키/설치 요청 메시지를 만든다."""
    rec: ModelRecommendation = recommend_provider(task_kind)
    avail = provider_available(rec.provider, env=env, which=which)
    request = ""
    if not avail:
        how = (f"{rec.needs_key} 키를 설정하면 돼요." if rec.needs_key
               else f"'{rec.provider}' CLI 로그인이 필요해요.")
        request = (f"이 작업엔 {rec.provider}가 나아요 ({rec.reason}). {how} "
                   f"없으면 {rec.alternative}로 진행할게요(품질 약간↓).")
    return RoutingDecision(task_kind, rec.provider, rec.reason, avail, rec.alternative, request)
