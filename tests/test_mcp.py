"""MCP 어댑터 — 표준 도구를 Polyrus 검증·게이트·멱등·감사로 흡수(네트워크 0)."""
from __future__ import annotations

from typing import Any

from polyrus.action_registry import ActionGateway, ActionRegistry
from polyrus.actions import ActionExecutor
from polyrus.adapters.mcp import (
    MCPTool,
    infer_reversible,
    json_schema_to_params,
    mcp_tool_to_schema,
    register_mcp_tools,
)


class FakeMCPClient:
    """MCP 서버 페이크 — 도구 목록 + 호출 기록."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self) -> list[MCPTool]:
        return [
            MCPTool("get_weather", "날씨 조회", {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            }),
            MCPTool("send_message", "메시지 전송", {
                "type": "object",
                "properties": {"to": {"type": "string"}, "text": {"type": "string"}},
                "required": ["to", "text"],
            }),
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        return {"ok": True, "tool": name}


# ── 스키마 변환 ────────────────────────────────────────────────────────────────
def test_json_schema_to_params() -> None:
    params = json_schema_to_params({
        "type": "object",
        "properties": {"city": {"type": "string"}, "days": {"type": "integer"}},
        "required": ["city"],
    })
    by = {p.name: p for p in params}
    assert by["city"].type == "str" and by["city"].required
    assert by["days"].type == "int" and not by["days"].required


def test_infer_reversible() -> None:
    assert infer_reversible("get_weather") is True       # 읽기 → 되돌릴 수 있음
    assert infer_reversible("send_message") is False     # 쓰기 → 게이트 대상
    assert infer_reversible("delete_user") is False
    assert infer_reversible("list_files") is True


def test_mcp_tool_to_schema() -> None:
    client = FakeMCPClient()
    schema = mcp_tool_to_schema(client.list_tools()[1], client)  # send_message
    assert schema.name == "send_message" and schema.reversible is False
    assert {p.name for p in schema.params} == {"to", "text"}


# ── 레지스트리 흡수 + 게이트웨이 ────────────────────────────────────────────────
def test_register_mcp_tools_populates() -> None:
    reg = ActionRegistry()
    n = register_mcp_tools(reg, FakeMCPClient())
    assert n == 2 and set(reg.names()) == {"get_weather", "send_message"}


def test_gateway_validates_mcp_call() -> None:
    client = FakeMCPClient()
    reg = ActionRegistry()
    register_mcp_tools(reg, client)
    gw = ActionGateway(reg, ActionExecutor(approve=lambda a: True))

    # 환각 도구 → 거부, 호출 안 됨.
    assert gw.submit("send_emssage", {"to": "a", "text": "b"}, key="k0").status == "rejected_invalid"
    # 파라미터 누락 → 거부.
    assert gw.submit("send_message", {"to": "a"}, key="k1").status == "rejected_invalid"
    # 유효 쓰기 도구 → 승인 게이트 통과 후 실행.
    assert gw.submit("send_message", {"to": "a", "text": "hi"}, key="k2").status == "executed"
    assert client.calls == [("send_message", {"to": "a", "text": "hi"})]


def test_gateway_reversible_mcp_auto_executes() -> None:
    client = FakeMCPClient()
    reg = ActionRegistry()
    register_mcp_tools(reg, client)
    gw = ActionGateway(reg)  # 승인자 없음
    # 읽기 도구(get_weather)는 되돌릴 수 있어 자동 실행.
    assert gw.submit("get_weather", {"city": "서울"}, key="r1").status == "executed"
    # 쓰기 도구는 승인자 없으면 트레이로 보류(자동 실행 안 됨).
    assert gw.submit("send_message", {"to": "a", "text": "b"}, key="r2").status == "deferred"


def test_gateway_mcp_idempotent() -> None:
    client = FakeMCPClient()
    reg = ActionRegistry()
    register_mcp_tools(reg, client)
    gw = ActionGateway(reg, ActionExecutor(approve=lambda a: True))
    args = {"to": "a", "text": "once"}
    gw.submit("send_message", args, key="same")
    assert gw.submit("send_message", args, key="same").status == "skipped_idempotent"
    assert len(client.calls) == 1  # 재시도해도 MCP 도구는 한 번만
