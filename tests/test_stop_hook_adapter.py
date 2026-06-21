"""Claude Code Stop-hook 어댑터 — wrap-first 웨지. 생성=호스트, 검증·게이팅=Polyrus.

block→호스트 재시도, continue 예산 소진→강제 stop+에스컬레이션(No-Silent-Stop)을 실측.
"""
from __future__ import annotations

import json

from polyrus.adapters.claude_code.stop_hook import (
    ClaudeCodeStopAdapter,
    run_hook,
)
from polyrus.adapters.claude_code.task_file import load_task_file
from polyrus.types import AgentAdapter, Claim, DoD, LedgerItem, Task, Termination
from tests.test_t1_execution import BAD, GOOD, TEST


def _task_with(code: str | None) -> tuple[Task, dict[str, Claim]]:
    dod = DoD(spec="짝수 제곱합", acceptance_tests=[TEST], frozen=True)
    task = Task(id="tk", request="구현", items=[LedgerItem(id="i1", goal="sum_even_squares", dod=dod)])
    arts: dict[str, Claim] = {}
    if code is not None:
        arts["i1"] = Claim(id="a", content=code, meta={"module": "solution.py"})
    return task, arts


def _adapter(code: str | None, max_continues: int = 3) -> ClaudeCodeStopAdapter:
    return ClaudeCodeStopAdapter(loader=lambda _p: _task_with(code), max_continues=max_continues)


# ── 결정 로직 ─────────────────────────────────────────────────────────────────
def test_verified_allows_stop() -> None:
    r = _adapter(GOOD).decide({})
    assert not r.block
    assert r.termination is Termination.VERIFIED_COMPLETE
    assert r.as_hook_output() == {}  # 빈 출력 = 정상 종료 허용
    assert r.corpus_records  # 코퍼스 emit


def test_unverified_blocks_and_reinjects() -> None:
    r = _adapter(BAD).decide({}, continues=0)
    assert r.block
    out = r.as_hook_output()
    assert out["decision"] == "block"
    assert "수용 테스트 실패" in out["reason"]  # 빠진 항목이 재주입 reason에


def test_missing_artifact_blocks() -> None:
    r = _adapter(None).decide({}, continues=0)
    assert r.block and "산출물 없음" in r.as_hook_output()["reason"]


def test_continue_budget_exhausted_escalates() -> None:
    # continue 예산 소진 → 강제 stop 허용 + 에스컬레이션(조용한 패스 아님).
    r = _adapter(BAD, max_continues=2).decide({}, continues=2)
    assert not r.block
    assert r.termination is Termination.BUDGET_ESCALATED
    assert "사람 확인 필요" in r.as_hook_output()["systemMessage"]


def test_adapter_conforms_to_protocol() -> None:
    assert isinstance(_adapter(GOOD), AgentAdapter)


def test_run_hook_notifies_on_budget_escalation(tmp_path) -> None:
    # continue 예산(0) 즉시 소진 → 강제 stop + notifier 핑(폰 알림 6.4).
    pings: list[str] = []
    out = run_hook(
        {"session_id": "s"}, _adapter(BAD, max_continues=0),
        state_dir=tmp_path, notifier=pings.append,
    )
    assert "systemMessage" in out
    assert pings and "미해결" in pings[0]


# ── run_hook: 세션 카운터로 continue 예산 가로질러 추적 ──────────────────────────
def test_run_hook_counter_increments_and_resets(tmp_path) -> None:
    adapter = _adapter(BAD, max_continues=2)
    payload = {"session_id": "s1"}
    out1 = run_hook(payload, adapter, state_dir=tmp_path)
    assert out1["decision"] == "block"  # 1회차: continue 0 → block
    out2 = run_hook(payload, adapter, state_dir=tmp_path)
    assert out2["decision"] == "block"  # 2회차: continue 1 → block
    out3 = run_hook(payload, adapter, state_dir=tmp_path)
    assert "systemMessage" in out3       # 3회차: continue 2 == max → 허용+에스컬레이션
    # 통과 케이스는 카운터 리셋
    ok = run_hook({"session_id": "s1"}, _adapter(GOOD), state_dir=tmp_path)
    assert ok == {}


# ── task_file 로더(실파일) + 어댑터 엔드투엔드 ─────────────────────────────────
def _write_task_file(tmp_path, code: str) -> None:
    polyrus = tmp_path / ".polyrus"
    polyrus.mkdir()
    (tmp_path / "solution.py").write_text(code, encoding="utf-8")
    (polyrus / "task.json").write_text(
        json.dumps({
            "task": {
                "id": "feat", "request": "sum_even_squares 구현",
                "items": [{
                    "id": "i1", "goal": "sum_even_squares 구현",
                    "module": "solution.py", "artifact": "../solution.py",
                    "acceptance_tests": [TEST],
                }],
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )


def test_task_file_loader_roundtrip(tmp_path) -> None:
    _write_task_file(tmp_path, GOOD)
    task, artifacts = load_task_file(tmp_path / ".polyrus" / "task.json")
    assert task.id == "feat" and task.items[0].dod.frozen
    assert "i1" in artifacts and "sum_even_squares" in artifacts["i1"].content


def test_end_to_end_good_verifies(tmp_path) -> None:
    _write_task_file(tmp_path, GOOD)
    adapter = ClaudeCodeStopAdapter(
        loader=lambda _p: load_task_file(tmp_path / ".polyrus" / "task.json")
    )
    r = adapter.decide({})
    assert not r.block and r.termination is Termination.VERIFIED_COMPLETE


def test_end_to_end_bad_blocks(tmp_path) -> None:
    _write_task_file(tmp_path, BAD)
    adapter = ClaudeCodeStopAdapter(
        loader=lambda _p: load_task_file(tmp_path / ".polyrus" / "task.json")
    )
    r = adapter.decide({})
    assert r.block and r.as_hook_output()["decision"] == "block"
