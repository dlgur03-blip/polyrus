"""자동화/다이제스트 도메인 결정적 검증기 — 스케줄·전달채널.

'매일 9시에 GitHub 유망한 거 요약해서 보내줘' 같은 *예약 자동화* 요청을 위해:
- 스케줄: 자연어("매일 9시"·"평일 아침 9시")를 cron으로 파싱·검증(결정적). 못 파싱하면 다시 묻는다.
- 전달채널: '어떻게 보내줄까?'의 답(텔레그램·이메일·슬랙)을 인식하고, 그 채널을 *실제로 보낼 설정*이
  됐는지 검사. 안 됐으면 키 요청(이유+방법 동반 — §6 키요청 UX, preflight 온보딩과 같은 사상).

전부 무-LLM 룰. 휴리스틱이라 reliability는 정직하게 1 미만.
"""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

from polyrus.types import Claim, DoD, Locality, Tier, Verdict, VerifierResult

# 요일 → cron(일=0). 월1 화2 수3 목4 금5 토6 일0.
_WEEKDAY = {"월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6, "일": 0}
_HOUR = re.compile(r"(\d{1,2})\s*시")


def parse_schedule(text: str) -> str | None:
    """자연어 스케줄 → cron('분 시 일 월 요일'). 시각이 없으면 None(예약 불가 → 다시 묻기)."""
    t = text.strip()
    m = _HOUR.search(t)
    if not m:
        return None
    hour = int(m.group(1))
    # 오전/오후/아침/저녁/밤 보정.
    if ("오후" in t or "저녁" in t or "밤" in t) and hour < 12:
        hour += 12
    if "오전" in t and hour == 12:
        hour = 0
    if not (0 <= hour <= 23):
        return None

    # 요일 도메인.
    if "평일" in t:
        dow = "1-5"
    elif "주말" in t:
        dow = "0,6"
    else:
        named = [str(_WEEKDAY[c]) for c in _WEEKDAY if f"{c}요일" in t]
        dow = ",".join(named) if named else "*"
    return f"0 {hour} * * {dow}"


# ── 전달 채널 ────────────────────────────────────────────────────────────────────
_CHANNELS = {
    "telegram": ("텔레그램", "telegram", "tele"),
    "email": ("이메일", "메일", "email", "e-mail"),
    "slack": ("슬랙", "slack"),
    "discord": ("디스코드", "discord"),
}
# 채널별 '실제로 보낼 수 있으려면' 필요한 설정(env 키)과 안내.
_CHANNEL_CONFIG = {
    "telegram": (("POLYRUS_TELEGRAM_TOKEN", "POLYRUS_TELEGRAM_CHAT_ID"),
                 "텔레그램으로 보내려면 봇 토큰이 필요해요. @BotFather로 봇을 만들고 "
                 "POLYRUS_TELEGRAM_TOKEN·POLYRUS_TELEGRAM_CHAT_ID를 설정하면 돼요."),
    "email": (("POLYRUS_SMTP_URL",), "이메일은 보내는 계정(SMTP) 설정이 필요해요."),
    "slack": (("POLYRUS_SLACK_WEBHOOK",), "슬랙은 Incoming Webhook URL이 필요해요."),
    "discord": (("POLYRUS_DISCORD_WEBHOOK",), "디스코드는 채널 Webhook URL이 필요해요."),
}


def detect_channel(text: str) -> str | None:
    """'어떻게 보내줄까?' 답에서 채널을 정규화. 못 알아보면 None."""
    low = text.lower()
    for norm, aliases in _CHANNELS.items():
        if any(a in low for a in aliases):
            return norm
    return None


def channel_config_status(channel: str, env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """그 채널로 *실제로 보낼* 설정이 됐는지 + 안 됐으면 친절 안내(키 요청)."""
    env = env if env is not None else os.environ
    keys, guide = _CHANNEL_CONFIG.get(channel, ((), ""))
    missing = [k for k in keys if not env.get(k)]
    if not missing:
        return True, f"{channel} 전송 준비됨"
    return False, guide


class _Auto:
    tier: Tier = Tier.T1_EXECUTION
    locality: Locality = Locality.LOCAL
    reliability: float = 0.8

    def _r(self, v: Verdict, detail: str, ev: list[str] | None = None) -> VerifierResult:
        return VerifierResult(self.tier, v, self.reliability, detail, ev or [], self.locality)


class ScheduleVerifier(_Auto):
    """스케줄이 실행 가능한 cron으로 확정되나(시각 모호 차단)."""

    name = "schedule"
    kind = "schedule"

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "schedule"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        cron = parse_schedule(claim.content)
        if cron is None:
            return self._r(Verdict.INCONCLUSIVE, "시각이 불명확해요 — 몇 시에 보낼지 알려주세요(예: 매일 9시)")
        return self._r(Verdict.PASS, f"cron '{cron}'", [cron])


class ChannelVerifier(_Auto):
    """전달 채널이 지원되고 *실제로 보낼 설정*까지 됐나. 안 되면 키 요청(이유+방법)."""

    name = "channel"
    kind = "channel"

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env = env

    def applies_to(self, claim: Claim) -> bool:
        return claim.kind == "channel"

    def verify(self, claim: Claim, dod: DoD) -> VerifierResult:
        ch = detect_channel(claim.content)
        if ch is None:
            return self._r(Verdict.FAIL, "지원하지 않는 채널 — 텔레그램/이메일/슬랙/디스코드 중에서 골라주세요")
        ok, guide = channel_config_status(ch, self.env)
        if ok:
            return self._r(Verdict.PASS, f"{ch} 전송 준비됨")
        # 설정 미비 → 코드 결함이 아니라 *온보딩*(키 요청). 솔직하게 INCONCLUSIVE.
        return self._r(Verdict.INCONCLUSIVE, guide, [ch])
