"""CLI 백엔드(계정연결) — claude/codex CLI 구동 모델 + `polyrus run`. 서브프로세스 0(주입)."""
from __future__ import annotations

import json

import pytest

from polyrus.models import CliModel, CliModelError, claude_cli, codex_cli


def _runner(rc=0, out="결과", err=""):
    calls: list[tuple] = []

    def run(cmd, stdin):
        calls.append((cmd, stdin))
        return rc, out, err

    return run, calls


# ── CliModel ───────────────────────────────────────────────────────────────────
def test_climodel_builds_command_with_prompt_arg() -> None:
    run, calls = _runner(out="생성된 코드")
    m = CliModel(["claude", "-p"], runner=run)
    assert m.complete("함수 짜줘", system="너는 엔지니어") == "생성된 코드"
    cmd, stdin = calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert "너는 엔지니어" in cmd[-1] and "함수 짜줘" in cmd[-1]  # system+prompt 합쳐 마지막 인자


def test_climodel_stdin_mode() -> None:
    run, calls = _runner()
    CliModel(["codex", "exec"], prompt_via_stdin=True, runner=run).complete("p", system="s")
    cmd, stdin = calls[0]
    assert cmd == ["codex", "exec"] and stdin == "s\n\np"  # 프롬프트는 stdin으로


def test_climodel_model_flag() -> None:
    run, calls = _runner()
    CliModel(["claude", "-p"], model_flag="--model", runner=run).complete("p", model="opus")
    assert "--model" in calls[0][0] and "opus" in calls[0][0]


def test_climodel_raises_on_nonzero() -> None:
    run, _ = _runner(rc=1, err="not logged in")
    with pytest.raises(CliModelError):
        CliModel(["claude", "-p"], runner=run).complete("p")


def test_factories_use_account_cli() -> None:
    assert claude_cli().command == ["claude", "-p"]      # 구독 로그인 print 모드
    assert codex_cli().command == ["codex", "exec"]       # ChatGPT 계정 비대화 모드


# ── polyrus run (백엔드 monkeypatch) ───────────────────────────────────────────
def test_run_drives_backend_to_verified(tmp_path, monkeypatch, capsys) -> None:
    import polyrus.cli as cli
    from tests.test_phase1_arms import FakeModel, GOOD_FENCED
    from tests.test_t1_execution import TEST

    # CLI 백엔드를 fake로 — 실제 claude/codex 호출 없이 owns-loop 검증.
    monkeypatch.setattr(cli, "_make_backend", lambda backend: FakeModel(GOOD_FENCED))
    polyrus = tmp_path / ".polyrus"
    polyrus.mkdir()
    (polyrus / "task.json").write_text(json.dumps({"task": {
        "id": "t", "request": "sum_even_squares 구현",
        "items": [{"id": "i1", "goal": "sum_even_squares 구현", "module": "solution.py",
                   "acceptance_tests": [TEST]}]}}), encoding="utf-8")

    rc = cli.main(["run", "--backend", "codex", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "codex" in out and "verified_complete" in out


def test_run_requires_goal_or_taskfile(tmp_path) -> None:
    import polyrus.cli as cli
    from tests.test_phase1_arms import FakeModel, GOOD_FENCED

    cli_mod = cli
    cli_mod._make_backend = lambda backend: FakeModel(GOOD_FENCED)  # type: ignore[attr-defined]
    assert cli.main(["run", "--backend", "claude", "--dir", str(tmp_path)]) == 2  # 목표/파일 없음
