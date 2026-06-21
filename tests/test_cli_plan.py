"""polyrus plan — 대화형 선제질문 기획 CLI(임시 디렉토리에만 쓴다)."""
from __future__ import annotations

import builtins

from polyrus.cli import main


def _scripted_input(answers: list[str]):
    it = iter(answers)

    def fake_input(_prompt: str = "") -> str:
        return next(it, "")

    return fake_input


def test_plan_full_flow_writes_brief(tmp_path, monkeypatch, capsys) -> None:
    # 6단계(목적·레퍼런스·취향[anti_slop]·포인트·취향[palette]·기능) 답을 순서대로 공급.
    monkeypatch.setattr(
        builtins, "input",
        _scripted_input(["문의하기", "stripe.com", "정돈된", "히어로", "신뢰", "문의폼, 가격표"]),
    )
    rc = main(["plan", "homepage", "--no-research", "--dir", str(tmp_path)])
    assert rc == 0
    plan = (tmp_path / ".polyrus" / "plan.md").read_text(encoding="utf-8")
    assert "문의하기" in plan and "의견형 스택 디폴트" in plan
    # 위키 영속 + 흡수 메시지.
    assert (tmp_path / ".polyrus" / "skills.db").exists()
    assert "흡수" in capsys.readouterr().out


def test_plan_evasive_answer_blocks(tmp_path, monkeypatch, capsys) -> None:
    # 목적에 회피('아무거나') → 회복질문에도 회피 → 미해결로 비0 종료(조용히 통과 X).
    monkeypatch.setattr(
        builtins, "input",
        _scripted_input(["아무거나", "알아서요", "stripe.com", "정돈된", "히어로", "신뢰", "문의폼"]),
    )
    rc = main(["plan", "homepage", "--no-research", "--dir", str(tmp_path)])
    assert rc == 1
    assert "미해결" in capsys.readouterr().out


def test_doctor_reports_missing_friendly(monkeypatch, capsys) -> None:
    import polyrus.preflight as pf

    monkeypatch.setattr(pf.shutil, "which", lambda _cmd: None)  # 아무것도 없음
    rc = main(["doctor", "homepage"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "node" in out and "필요" in out
    assert "Traceback" not in out  # 초보자에게 스택트레이스 금지


def test_doctor_ok_when_present(monkeypatch, capsys) -> None:
    import polyrus.preflight as pf

    monkeypatch.setattr(pf.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")  # 다 있음
    rc = main(["doctor", "homepage"])
    assert rc == 0
    assert "준비됐어요" in capsys.readouterr().out


def test_plan_scope_prunes_decoration(tmp_path, monkeypatch) -> None:
    # 랜딩 스코프: 3D 포인트(accent) 질문 생략 → 5개 답이면 충분.
    monkeypatch.setattr(
        builtins, "input",
        _scripted_input(["문의하기", "stripe.com", "정돈된", "신뢰", "문의폼"]),
    )
    rc = main(["plan", "homepage", "--no-research", "--dir", str(tmp_path), "--scope-min-weight", "0.5"])
    assert rc == 0
    plan = (tmp_path / ".polyrus" / "plan.md").read_text(encoding="utf-8")
    assert "3D 아이콘" not in plan
