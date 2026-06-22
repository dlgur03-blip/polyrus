"""polyrus digest run/schedule — 저장된 설정으로 실행·스케줄(네트워크는 monkeypatch로 차단)."""
from __future__ import annotations

import json

from polyrus.cli import main
from polyrus.digest import Repo


def _write_cfg(tmp_path, cron="0 9 * * *") -> None:
    pdir = tmp_path / ".polyrus"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "digest.json").write_text(json.dumps({
        "source": "python LLM", "criteria": "스타 급상승",
        "schedule_cron": cron, "channel": "telegram", "length": "짧게",
    }), encoding="utf-8")


def test_digest_run_needs_config(tmp_path, capsys) -> None:
    rc = main(["digest", "run", "--dir", str(tmp_path)])
    assert rc == 2 and "plan digest" in capsys.readouterr().out


def test_digest_run_executes(tmp_path, monkeypatch, capsys) -> None:
    _write_cfg(tmp_path)
    # GitHub 네트워크 차단 → 페이크 레포 주입.
    import polyrus.digest as dg

    monkeypatch.setattr(dg.GitHubSource, "search",
                        lambda self, q, *, max_n=20: [Repo("acme/llm", "LLM 도구", 4200, "https://x")])
    rc = main(["digest", "run", "--dir", str(tmp_path), "--deliver", "fake"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "GitHub 조회" in out and "language:python" in out
    assert "전송=성공" in out


def test_digest_schedule_prints_crontab(tmp_path, capsys) -> None:
    _write_cfg(tmp_path)
    rc = main(["digest", "schedule", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "0 9 * * *" in out and "polyrus digest run" in out and "crontab -" in out


def test_digest_schedule_blocks_empty_cron(tmp_path, capsys) -> None:
    _write_cfg(tmp_path, cron="")
    rc = main(["digest", "schedule", "--dir", str(tmp_path)])
    assert rc == 1 and "언제" in capsys.readouterr().out


# ── build-check ───────────────────────────────────────────────────────────────
def test_build_check_pass(tmp_path, capsys) -> None:
    rc = main(["build-check", "--dir", str(tmp_path), "--cmd", "true"])
    assert rc == 0 and "✅" in capsys.readouterr().out


def test_build_check_fail(tmp_path, capsys) -> None:
    rc = main(["build-check", "--dir", str(tmp_path), "--cmd", "false"])
    assert rc == 1 and "❌" in capsys.readouterr().out


def test_build_check_missing_tool_is_env(tmp_path, capsys) -> None:
    rc = main(["build-check", "--dir", str(tmp_path), "--cmd", "polyrus-no-tool-xyz build"])
    out = capsys.readouterr().out
    assert rc == 1 and "⚠" in out and "환경" in out  # 도구 없음 = 환경 안내


# ── setup (한 방 설정) ──────────────────────────────────────────────────────────
def test_setup_registers_auto_hook(tmp_path) -> None:
    from polyrus.cli import AUTO_HOOK_COMMAND, has_hook

    sp = tmp_path / "settings.json"
    rc = main(["setup", "--settings", str(sp)])
    assert rc == 0
    s = json.loads(sp.read_text(encoding="utf-8"))
    assert has_hook(s)
    cmd = s["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert cmd == AUTO_HOOK_COMMAND  # 무설정 auto 훅 기본


def test_setup_no_hook_skips(tmp_path, capsys) -> None:
    sp = tmp_path / "settings.json"
    rc = main(["setup", "--settings", str(sp), "--no-hook"])
    assert rc == 0 and not sp.exists()
    assert "건너뜀" in capsys.readouterr().out


# ── connect (클로드/코덱스 CLI 연결) ────────────────────────────────────────────
def test_connect_claude_registers_hook(tmp_path, monkeypatch, capsys) -> None:
    import polyrus.cli as cli
    from polyrus.cli import AUTO_HOOK_COMMAND, has_hook

    monkeypatch.setattr(cli.shutil, "which", lambda c: "/usr/bin/claude" if c == "claude" else None)
    sp = tmp_path / "settings.json"
    rc = main(["connect", "--settings", str(sp)])
    assert rc == 0
    s = json.loads(sp.read_text(encoding="utf-8"))
    assert has_hook(s)
    assert s["hooks"]["Stop"][0]["hooks"][0]["command"] == AUTO_HOOK_COMMAND
    assert "Claude Code 연결" in capsys.readouterr().out


def test_connect_auto_detects_both(tmp_path, monkeypatch, capsys) -> None:
    import polyrus.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda c: f"/usr/bin/{c}")  # 둘 다 있음
    rc = main(["connect", "--settings", str(tmp_path / "s.json")])
    out = capsys.readouterr().out
    assert rc == 0 and "claude" in out and "codex" in out and "owns-loop" in out


def test_connect_none_available_errors(tmp_path, monkeypatch, capsys) -> None:
    import polyrus.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda c: None)  # 아무것도 없음
    rc = main(["connect", "--settings", str(tmp_path / "s.json")])
    assert rc == 1 and "설치" in capsys.readouterr().out


def test_connect_idempotent(tmp_path, monkeypatch, capsys) -> None:
    import polyrus.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda c: "/usr/bin/claude" if c == "claude" else None)
    sp = tmp_path / "settings.json"
    main(["connect", "--settings", str(sp)])
    main(["connect", "--settings", str(sp)])  # 두 번째는 중복 등록 안 함
    s = json.loads(sp.read_text(encoding="utf-8"))
    cnt = sum(1 for g in s["hooks"]["Stop"] for h in g["hooks"] if "polyrus" in h["command"])
    assert cnt == 1
