"""기획 단계 검증기 — 결정적(무-LLM) 우선(§4-3). LLM-judge는 최후의 보루.

이 검증기들은 선제질문 UX의 각 단계 합격기준을 *룰·파서*로 집행한다. 비개발자도 모델도
못 믿으므로, 가능한 한 결정적 오라클로 잠근다(글로벌 시스템의 진짜 품질은 프롬프트가 아니라
훅=결정적에서 나온다는 관찰의 코드화).
"""
from __future__ import annotations

from polyrus.verifiers.plan.deterministic import (
    AccentCountVerifier,
    AISlopVerifier,
    ContrastVerifier,
    FrameAlignmentVerifier,
    SlopReport,
    accent_count_ok,
    check_slop,
    contrast_ratio,
    frame_alignment,
)
from polyrus.verifiers.plan.automation import (
    ChannelVerifier,
    ScheduleVerifier,
    channel_config_status,
    detect_channel,
    parse_schedule,
)
from polyrus.verifiers.plan.responsiveness import (
    EaseReport,
    EvasionReport,
    EvasionVerifier,
    QuestionEaseVerifier,
    answer_responsive,
    check_evasion,
    completion_excuse,
    question_ease,
)

__all__ = [
    "AISlopVerifier",
    "AccentCountVerifier",
    "ContrastVerifier",
    "FrameAlignmentVerifier",
    "SlopReport",
    "check_slop",
    "accent_count_ok",
    "contrast_ratio",
    "frame_alignment",
    "EvasionVerifier",
    "QuestionEaseVerifier",
    "EvasionReport",
    "EaseReport",
    "check_evasion",
    "answer_responsive",
    "completion_excuse",
    "question_ease",
    "ScheduleVerifier",
    "ChannelVerifier",
    "parse_schedule",
    "detect_channel",
    "channel_config_status",
]
