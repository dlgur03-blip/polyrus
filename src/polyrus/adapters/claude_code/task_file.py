"""`.polyrus/task.json` 로더 — Stop 훅이 세션의 완료 원장을 파일에서 해석한다.

스키마(예):
{
  "task": {
    "id": "feat-login",
    "request": "로그인 기능 구현",
    "items": [
      {
        "id": "i1",
        "goal": "sum_even_squares 구현",
        "module": "solution.py",
        "artifact": "solution.py",                 // 호스트가 쓴 현재 산출물 경로
        "acceptance_tests": ["tests/test_x.py"]     // 경로 또는 인라인 테스트 소스
      }
    ]
  }
}

요구사항(원장)을 *모델 밖 파일*에 두는 5.3 원칙의 실체. acceptance_tests는 생성 전 동결.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polyrus.dod import DoDGenerator
from polyrus.types import Claim, LedgerItem, RiskLevel, Task

_RISK = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}


def load_task_file(path: str | Path, artifact_base: str | Path | None = None) -> tuple[Task, dict[str, Claim]]:
    """task.json 경로 → (Task, {item_id: 현재 산출물 Claim}). 산출물 파일이 없으면 그 항목은 Claim 누락.

    artifact_base: 산출물(artifact) 상대경로의 기준. 호스트가 코드를 쓰는 프로젝트 루트.
    None이면 task.json이 있는 디렉토리 기준(테스트 편의).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = data["task"]
    base = Path(artifact_base).resolve() if artifact_base else Path(path).resolve().parent
    gen = DoDGenerator()

    items: list[LedgerItem] = []
    artifacts: dict[str, Claim] = {}
    for raw in spec.get("items", []):
        dod = gen.derive_dod(
            raw.get("goal", spec.get("request", "")),
            acceptance_tests=list(raw.get("acceptance_tests", [])),
            properties=list(raw.get("properties", [])),
        )
        item = LedgerItem(
            id=raw["id"],
            goal=raw.get("goal", raw["id"]),
            dod=dod,
            risk=_RISK.get(raw.get("risk", "medium"), RiskLevel.MEDIUM),
        )
        items.append(item)

        module = raw.get("module", "solution.py")
        artifact_path = raw.get("artifact")
        if artifact_path:
            f = (base / artifact_path).resolve()
            if f.exists():
                artifacts[item.id] = Claim(
                    id=f"{item.id}-artifact",
                    content=f.read_text(encoding="utf-8"),
                    meta={"module": module},
                )

    return Task(id=spec["id"], request=spec.get("request", ""), items=items), artifacts


def file_loader(payload: dict[str, Any]) -> tuple[Task, dict[str, Claim]]:
    """Stop payload → task.json 해석. 경로는 payload.cwd 우선, 없으면 현재 디렉토리.
    산출물(artifact)은 프로젝트 루트(cwd) 기준으로 해석 — 호스트가 코드를 쓰는 곳."""
    cwd = payload.get("cwd", ".")
    return load_task_file(Path(cwd) / ".polyrus" / "task.json", artifact_base=cwd)
