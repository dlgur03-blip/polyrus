"""액션 스키마 레지스트리 + 게이트웨이 (v2 기둥 A — 검증된 실행 권위).

Swytchcode가 증명한 통찰: 에이전트가 raw API/도구를 부르면 *엔드포인트를 환각*하고 파라미터를
틀린다. 해법은 실행 *전에* 스키마 레지스트리에 대조하는 것 — 이건 v1의 T3(코드 API 환각 차단)를
*액션*으로 일반화한 것이다.

파이프라인(execution authority): 의도 → 스키마 검증(환각·파라미터 차단) → 멱등 게이트 실행(actions.py)
→ 감사. 스키마/핸들러는 백엔드(인프로세스 / MCP / Swytchcode 어댑터)가 채운다.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Callable

from polyrus.actions import Action, ActionExecutor, ActionResult

_TYPES: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
    "list": list,
    "dict": dict,
}


@dataclass
class Param:
    name: str
    type: str  # _TYPES 키
    required: bool = True


@dataclass
class ActionSchema:
    """한 액션의 계약. handler는 백엔드가 제공하는 실제 실행(없으면 검증만)."""

    name: str
    kind: str                       # email | payment | http_write | ...
    reversible: bool
    params: list[Param] = field(default_factory=list)
    description: str = ""
    handler: Callable[[dict], Any] | None = None


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)


class ActionRegistry:
    """등록된 액션 스키마. validate()가 환각·파라미터 오류를 실행 전에 잡는다(T3-액션판)."""

    def __init__(self) -> None:
        self._schemas: dict[str, ActionSchema] = {}

    def register(self, schema: ActionSchema) -> None:
        self._schemas[schema.name] = schema

    def get(self, name: str) -> ActionSchema | None:
        return self._schemas.get(name)

    def names(self) -> list[str]:
        return sorted(self._schemas)

    def validate(self, name: str, params: dict[str, Any]) -> ValidationResult:
        schema = self._schemas.get(name)
        if schema is None:
            near = difflib.get_close_matches(name, self._schemas, n=3)
            hint = f" 비슷한 것: {near}" if near else ""
            return ValidationResult(False, [f"액션 '{name}'은(는) 존재하지 않는다(환각).{hint}"])

        errors: list[str] = []
        known = {p.name for p in schema.params}
        for p in schema.params:
            if p.name not in params:
                if p.required:
                    errors.append(f"필수 파라미터 '{p.name}'({p.type}) 누락")
            elif not isinstance(params[p.name], _TYPES[p.type]):
                got = type(params[p.name]).__name__
                errors.append(f"'{p.name}' 타입 불일치(기대 {p.type}, 실제 {got})")
        for key in params:
            if key not in known:
                errors.append(f"알 수 없는 파라미터 '{key}'")
        return ValidationResult(not errors, errors)


class ActionGateway:
    """의도 → 스키마 검증 → 멱등 게이트 실행 → 감사. v1 No-Pass 사상의 *액션* 버전.

    검증 실패면 실행하지 않는다(환각/오류 액션은 '안전한 미완료'). 통과하면 actions.ActionExecutor가
    멱등·되돌릴수없음 격리·감사를 처리한다.
    """

    def __init__(self, registry: ActionRegistry, executor: ActionExecutor | None = None) -> None:
        self.registry = registry
        self.executor = executor or ActionExecutor()

    def submit(self, name: str, params: dict[str, Any], *, key: str) -> ActionResult:
        v = self.registry.validate(name, params)
        if not v.ok:
            # 검증 실패 = 실행 안 함. 환각/오류는 거부(감사엔 안 남김 — 실행 시도가 없으므로).
            return ActionResult(key, "rejected_invalid", value=v.errors)

        schema = self.registry.get(name)
        assert schema is not None
        handler = schema.handler or (lambda _p: None)
        action = Action(
            key=key,
            kind=schema.kind,
            reversible=schema.reversible,
            run=lambda: handler(params),
            description=schema.description or name,
        )
        return self.executor.execute(action)
