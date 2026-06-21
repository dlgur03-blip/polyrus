"""polyrus CLI — 임시 settings.json에만 쓴다(실제 ~/.claude 절대 안 건드림)."""
from __future__ import annotations

import json

from polyrus.cli import HOOK_COMMAND, has_hook, main


def _read(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_wrap_registers_hook(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    assert main(["wrap", "claude", "--settings", str(sp)]) == 0
    settings = _read(sp)
    assert has_hook(settings)
    cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd == HOOK_COMMAND


def test_wrap_is_idempotent(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    main(["wrap", "claude", "--settings", str(sp)])
    main(["wrap", "claude", "--settings", str(sp)])  # 두 번째는 중복 등록 안 함
    groups = _read(sp)["hooks"]["Stop"]
    hook_count = sum(
        1 for g in groups for h in g["hooks"] if HOOK_COMMAND in h["command"]
    )
    assert hook_count == 1


def test_wrap_preserves_existing_settings(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({
        "model": "claude-opus-4-8",
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other-tool"}]}]},
    }), encoding="utf-8")
    main(["wrap", "claude", "--settings", str(sp)])
    settings = _read(sp)
    assert settings["model"] == "claude-opus-4-8"          # 기존 설정 보존
    assert any(  # 기존 훅도 보존
        h["command"] == "other-tool"
        for g in settings["hooks"]["Stop"] for h in g["hooks"]
    )
    assert has_hook(settings)                               # + 우리 훅 추가


def test_dry_run_does_not_write(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    assert main(["wrap", "claude", "--settings", str(sp), "--dry-run"]) == 0
    assert not sp.exists()


def test_unwrap_removes_only_our_hook(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    sp.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "other-tool"},
            {"type": "command", "command": HOOK_COMMAND},
        ]}]},
    }), encoding="utf-8")
    main(["unwrap", "claude", "--settings", str(sp)])
    settings = _read(sp)
    assert not has_hook(settings)
    assert any(  # 남의 훅은 남겨둠
        h["command"] == "other-tool"
        for g in settings["hooks"]["Stop"] for h in g["hooks"]
    )


def test_init_creates_skeleton(tmp_path) -> None:
    assert main(["init", "--dir", str(tmp_path)]) == 0
    task = tmp_path / ".polyrus" / "task.json"
    assert task.exists()
    data = json.loads(task.read_text(encoding="utf-8"))
    assert "task" in data and data["task"]["items"]
    # 세션 산출물은 gitignore
    assert (tmp_path / ".polyrus" / ".gitignore").exists()


def test_init_skeleton_is_loadable(tmp_path) -> None:
    # 생성된 골격이 실제 로더로 파싱되는지(필드 계약 일치).
    from polyrus.adapters.claude_code.task_file import load_task_file

    main(["init", "--dir", str(tmp_path)])
    task, _arts = load_task_file(tmp_path / ".polyrus" / "task.json", artifact_base=tmp_path)
    assert task.items and task.items[0].dod.frozen


def test_init_idempotent_without_force(tmp_path, capsys) -> None:
    main(["init", "--dir", str(tmp_path)])
    (tmp_path / ".polyrus" / "task.json").write_text('{"task":{"id":"edited","items":[]}}')
    main(["init", "--dir", str(tmp_path)])  # 덮어쓰지 않음
    assert json.loads((tmp_path / ".polyrus" / "task.json").read_text())["task"]["id"] == "edited"


def test_init_with_wrap_registers_hook(tmp_path) -> None:
    sp = tmp_path / "settings.json"
    assert main(["init", "--dir", str(tmp_path), "--wrap", "--settings", str(sp)]) == 0
    assert has_hook(_read(sp))


def test_init_with_goal_sets_request(tmp_path) -> None:
    main(["init", "내 목표 한 줄", "--dir", str(tmp_path)])
    data = json.loads((tmp_path / ".polyrus" / "task.json").read_text(encoding="utf-8"))
    assert data["task"]["request"] == "내 목표 한 줄"
    assert data["task"]["items"][0]["goal"] == "내 목표 한 줄"


def test_init_llm_synthesizes_tests(tmp_path, monkeypatch) -> None:
    # _default_model을 fake로 갈아끼워 네트워크 0으로 LLM 합성 경로 검증.
    import polyrus.cli as cli
    from tests.test_phase1_arms import FakeModel
    from tests.test_t1_execution import TEST

    monkeypatch.setattr(cli, "_default_model", lambda: FakeModel(f"```python\n{TEST}```"))
    main(["init", "sum_even_squares 구현", "--llm", "--dir", str(tmp_path)])
    data = json.loads((tmp_path / ".polyrus" / "task.json").read_text(encoding="utf-8"))
    tests = data["task"]["items"][0]["acceptance_tests"]
    assert any("def test" in t for t in tests)  # LLM 합성 테스트가 들어감


def test_status_runs(tmp_path, capsys) -> None:
    sp = tmp_path / "settings.json"
    main(["wrap", "claude", "--settings", str(sp)])
    assert main(["status", "--settings", str(sp)]) == 0
    out = capsys.readouterr().out
    assert "등록됨" in out
