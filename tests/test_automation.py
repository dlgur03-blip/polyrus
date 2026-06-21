"""자동화 도메인 검증기 — 스케줄(자연어→cron)·전달채널(인식+설정)."""
from __future__ import annotations

from polyrus.types import Claim, DoD, Verdict
from polyrus.verifiers.plan import (
    ChannelVerifier,
    ScheduleVerifier,
    channel_config_status,
    detect_channel,
    parse_schedule,
)

_DOD = DoD(spec="x", frozen=True)


def test_parse_schedule_daily() -> None:
    assert parse_schedule("매일 9시") == "0 9 * * *"
    assert parse_schedule("매일 아침 9시에 보내줘") == "0 9 * * *"
    assert parse_schedule("오후 6시") == "0 18 * * *"


def test_parse_schedule_weekday_weekend() -> None:
    assert parse_schedule("평일 9시") == "0 9 * * 1-5"
    assert parse_schedule("주말 10시") == "0 10 * * 0,6"
    assert parse_schedule("매주 월요일 9시") == "0 9 * * 1"


def test_parse_schedule_no_time_is_none() -> None:
    assert parse_schedule("매일매일 자주") is None  # 시각 없음 → 다시 물어야


def test_schedule_verifier() -> None:
    v = ScheduleVerifier()
    assert v.verify(Claim("s", "평일 아침 9시", kind="schedule"), _DOD).verdict is Verdict.PASS
    # 시각 모호 → INCONCLUSIVE(통과도 실패도 아님, 다시 묻기).
    assert v.verify(Claim("s", "자주 보내줘", kind="schedule"), _DOD).verdict is Verdict.INCONCLUSIVE


def test_detect_channel() -> None:
    assert detect_channel("텔레그램으로 보내줘") == "telegram"
    assert detect_channel("이메일이 편해요") == "email"
    assert detect_channel("슬랙 채널에") == "slack"
    assert detect_channel("팩스로") is None


def test_channel_config_status() -> None:
    ok, _ = channel_config_status("telegram", env={"POLYRUS_TELEGRAM_TOKEN": "t", "POLYRUS_TELEGRAM_CHAT_ID": "c"})
    assert ok
    missing, guide = channel_config_status("telegram", env={})
    assert not missing and "토큰" in guide  # 키 요청(이유+방법)


def test_channel_verifier_configured_vs_not() -> None:
    configured = ChannelVerifier(env={"POLYRUS_TELEGRAM_TOKEN": "t", "POLYRUS_TELEGRAM_CHAT_ID": "c"})
    assert configured.verify(Claim("c", "텔레그램", kind="channel"), _DOD).verdict is Verdict.PASS
    # 설정 미비 = 온보딩(키 요청) → INCONCLUSIVE(코드 결함 아님).
    bare = ChannelVerifier(env={})
    assert bare.verify(Claim("c", "텔레그램", kind="channel"), _DOD).verdict is Verdict.INCONCLUSIVE
    # 미지원 채널 → FAIL.
    assert bare.verify(Claim("c", "비둘기로", kind="channel"), _DOD).verdict is Verdict.FAIL
