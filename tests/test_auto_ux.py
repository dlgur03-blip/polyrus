"""무설정 UX — 사람말 번역 + 일반대화 침묵 + 요청에서 기준 자동생성."""
from __future__ import annotations

import json

from polyrus.adapters.claude_code.auto import (
    AutoStopDecider,
    has_code_intent,
    last_user_request,
    read_transcript,
)
from polyrus.humanize import humanize
from polyrus.types import Termination
from tests.test_phase1_arms import FakeModel
from tests.test_t1_execution import BAD, GOOD, TEST


# ── humanize: pytest 덤프 → 사람 말 ─────────────────────────────────────────────
def test_humanize_assertion() -> None:
    out = humanize("수용 테스트 실패: assert 35 == 0\nFAILED test_x")
    assert "기대값은 0" in out and "35" in out and "assert" not in out


def test_humanize_exception() -> None:
    assert "오류" in humanize("ZeroDivisionError: division by zero")


def test_humanize_passthrough_human_detail() -> None:
    # 이미 사람말인 재무/검색 detail은 그대로(태그만 제거).
    assert humanize("[finance.t1.recompute] 재계산 3000 ≠ 주장 3500").startswith("재계산")


# ── transcript 읽기 + 의도 감지 ─────────────────────────────────────────────────
def test_read_transcript_and_last_request(tmp_path) -> None:
    tp = tmp_path / "t.jsonl"
    tp.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "하이"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"role": "assistant",
                      "content": [{"type": "text", "text": "안녕"}]}}) + "\n"
        + json.dumps({"role": "user", "content": "sum_even_squares 구현해줘"}) + "\n",
        encoding="utf-8",
    )
    assert read_transcript(tp)[0] == ("user", "하이")
    assert last_user_request({"transcript_path": str(tp)}) == "sum_even_squares 구현해줘"


def test_code_intent() -> None:
    assert has_code_intent("sum_even_squares 구현해줘")
    assert has_code_intent("이 버그 고쳐줘")
    assert not has_code_intent("하이")
    assert not has_code_intent("오늘 날씨 어때?")


# ── 무설정 결정 ─────────────────────────────────────────────────────────────────
def _decider(code_text: str | None) -> AutoStopDecider:
    # 모델: 요청→수용테스트 합성(고정). 코드: 주입.
    return AutoStopDecider(
        FakeModel(f"```python\n{TEST}```"),
        get_request=lambda p: p.get("_req", ""),
        get_code=lambda cwd: ("solution.py", code_text) if code_text else None,
    )


def test_casual_chat_is_silent() -> None:
    # "하이" → 코드 의도 없음 → 침묵 허용(끼어들지 않음).
    r = _decider(GOOD).decide({"_req": "하이"})
    assert not r.block and r.as_hook_output() == {}


def test_code_task_correct_is_silent() -> None:
    # 코드 요청 + 올바른 코드 → 통과 → 침묵.
    r = _decider(GOOD).decide({"_req": "sum_even_squares 구현해줘"})
    assert not r.block and r.termination is Termination.VERIFIED_COMPLETE


def test_code_task_wrong_blocks_in_plain_language() -> None:
    # 코드 요청 + 틀린 코드 → 차단 + 사람 말(pytest 덤프 아님).
    r = _decider(BAD).decide({"_req": "sum_even_squares 구현해줘"})
    assert r.block
    out = r.as_hook_output()
    assert out["decision"] == "block"
    assert "기대값" in out["reason"] and "assert" not in out["reason"]
    assert "자동 생성한 수용 테스트" in out["reason"]  # 무엇을 검증했는지 투명


def test_no_code_artifact_is_silent() -> None:
    r = _decider(None).decide({"_req": "sum_even_squares 구현해줘"})
    assert not r.block


def test_derivation_failure_does_not_nag() -> None:
    # 모델이 빈 응답(기준 생성 실패) → 막지 않음(잔소리 금지).
    d = AutoStopDecider(FakeModel("테스트 못 만들겠어요"),
                        get_request=lambda p: "구현해줘", get_code=lambda c: ("solution.py", GOOD))
    assert not d.decide({}).block
