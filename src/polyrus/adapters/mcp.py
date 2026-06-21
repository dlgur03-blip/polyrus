"""MCP 어댑터 (v2 기둥 A — 표준 도구 흡수).

MCP(Model Context Protocol)는 도구·리소스를 에이전트에 노출하는 시장 표준(7.1). Polyrus는
재발명하지 않고 올라탄다 — 아무 MCP 도구를 *액션 스키마*로 변환해 레지스트리에 넣으면,
그 위에 v1 사상(환각 차단·멱등·되돌릴수없음 격리·감사)이 자동으로 얹힌다. '싸우지 말고 감싸라'.

전송(JSON-RPC stdio/SSE)은 공식 `mcp` SDK가 처리 — 여기선 MCPClient 프로토콜만 두고,
*Polyrus 고유 가치*(스키마 변환 + 게이팅)에 집중한다. 테스트는 FakeMCPClient로 네트워크 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from polyrus.action_registry import ActionRegistry, ActionSchema, Param

# JSON Schema 타입 → 레지스트리 Param 타입.
_JSON_TO_PARAM = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "array": "list",
    "object": "dict",
}

# 이름에 이 동사가 있으면 부수효과/되돌릴 수 없음으로 *보수적* 가정(게이트 대상).
_WRITE_HINTS = (
    "send", "create", "delete", "update", "post", "write", "pay", "transfer",
    "remove", "insert", "put", "execute", "publish", "charge", "drop",
)


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)  # JSON Schema(object)


class MCPClient(Protocol):
    """공식 mcp SDK 세션을 감싸거나(SdkMCPClient) 테스트 페이크가 구현."""

    def list_tools(self) -> list[MCPTool]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def json_schema_to_params(schema: dict[str, Any]) -> list[Param]:
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", []))
    params: list[Param] = []
    for name, spec in props.items():
        jtype = spec.get("type", "string") if isinstance(spec, dict) else "string"
        params.append(Param(name, _JSON_TO_PARAM.get(jtype, "str"), required=name in required))
    return params


def infer_reversible(tool_name: str) -> bool:
    """쓰기 동사가 이름에 있으면 되돌릴 수 없음(False)으로 가정 → 게이트. 보수적 안전 기본값."""
    n = tool_name.lower()
    return not any(h in n for h in _WRITE_HINTS)


def mcp_tool_to_schema(tool: MCPTool, client: MCPClient, *, kind: str = "mcp") -> ActionSchema:
    """MCP 도구 → ActionSchema(핸들러는 client.call_tool로 위임)."""
    return ActionSchema(
        name=tool.name,
        kind=kind,
        reversible=infer_reversible(tool.name),
        params=json_schema_to_params(tool.input_schema),
        description=tool.description,
        handler=lambda args, _name=tool.name: client.call_tool(_name, args),
    )


def register_mcp_tools(registry: ActionRegistry, client: MCPClient, *, kind: str = "mcp") -> int:
    """MCP 서버의 모든 도구를 레지스트리에 흡수. 반환=등록 개수.

    이후 ActionGateway.submit(tool_name, args, key=...)이 환각·파라미터를 검증하고,
    쓰기 도구는 게이트(승인), 멱등 보장, 감사 기록 — 아무 MCP 도구에 v1 신뢰성이 얹힌다.
    """
    tools = client.list_tools()
    for t in tools:
        registry.register(mcp_tool_to_schema(t, client, kind=kind))
    return len(tools)


class SdkMCPClient:
    """공식 `mcp` SDK의 동기 세션을 MCPClient로 감싸는 얇은 어댑터.

    세션은 list_tools()/call_tool(name, arguments)를 제공한다고 가정(duck-typed).
    실제 전송·핸드셰이크는 SDK가 처리 — 라이브 서버 필요(`pip install polyrus-agent` + mcp).
    """

    def __init__(self, session: object) -> None:
        self._s = session

    def list_tools(self) -> list[MCPTool]:  # pragma: no cover - 라이브 SDK 필요
        result = self._s.list_tools()  # type: ignore[attr-defined]
        tools = getattr(result, "tools", result)
        return [
            MCPTool(
                name=t.name,
                description=getattr(t, "description", "") or "",
                input_schema=getattr(t, "inputSchema", {}) or {},
            )
            for t in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:  # pragma: no cover
        return self._s.call_tool(name, arguments)  # type: ignore[attr-defined]
