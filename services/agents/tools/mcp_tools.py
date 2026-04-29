"""Dynamic MCP tool discovery and execution via the TrustLogix MCP gateway.

Tools are discovered at runtime by calling ``tools/list`` against the gateway.
Each tool's input schema is parsed into a Pydantic model and exposed to the
LLM as a LangChain ``StructuredTool``. No tool names or schemas are hardcoded.
"""

from __future__ import annotations

import logging
from typing import Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from services.agents.tools.mcp_gateway import call_tool as _backend_call_tool
from services.agents.tools.mcp_gateway import list_tools as _backend_list_tools

logger = logging.getLogger(__name__)


# ── Shared context ────────────────────────────────────────────────────────

_user_context: dict[str, str] = {}
_emit_callback: Callable[[dict], None] | None = None


def set_context(
    agent_name: str,
    user_name: str,
    user_role: str,
    emit: Callable[[dict], None] | None = None,
    tlx_gateway_token: str = "",
) -> None:
    _user_context.update({
        "agent_name": agent_name,
        "user_name": user_name,
        "user_role": user_role,
        "tlx_gateway_token": tlx_gateway_token,
    })
    global _emit_callback
    _emit_callback = emit


# ── Generic tool call dispatcher ──────────────────────────────────────────

async def call_tool(tool_name: str, arguments: dict) -> str:
    """Call any MCP tool by name through the TrustLogix gateway."""
    return await _backend_call_tool(
        tool_name=tool_name,
        arguments=arguments,
        agent_name=_user_context.get("agent_name", ""),
        user_name=_user_context.get("user_name", ""),
        user_role=_user_context.get("user_role", ""),
        user_token=_user_context.get("tlx_gateway_token", ""),
        emit=_emit_callback,
    )


# ── Dynamic tool discovery ────────────────────────────────────────────────

_TYPE_MAP = {"string": str, "integer": int, "number": float, "boolean": bool}


def _build_args_model(tool_name: str, input_schema: dict) -> type[BaseModel]:
    """Dynamically create a Pydantic model from an MCP inputSchema."""
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    fields = {}
    for k, v in props.items():
        py_type = _TYPE_MAP.get(v.get("type", "string"), str)
        desc = v.get("description", k)
        if k in required:
            fields[k] = (py_type, Field(description=desc))
        else:
            fields[k] = (py_type, Field(default="", description=desc))

    if not fields:
        fields["input"] = (str, Field(description="Input to the tool"))

    model_name = f"{tool_name}_args"
    return create_model(model_name, **fields)


def _build_tool_func(tool_name: str):
    async def _invoke(**kwargs) -> str:
        return await call_tool(tool_name, kwargs)
    _invoke.__name__ = tool_name
    return _invoke


async def discover_tools() -> list[StructuredTool]:
    """Call ``tools/list`` on the gateway and return LangChain tools."""
    token = _user_context.get("tlx_gateway_token", "")
    try:
        raw_tools = await _backend_list_tools(user_token=token)
    except Exception as exc:
        logger.error("Tool discovery failed: %s", exc)
        return []

    if not raw_tools:
        logger.warning("No tools discovered from gateway")
        return []

    tools = []
    for t in raw_tools:
        name = t.get("name", "")
        description = t.get("description", name)
        input_schema = t.get("inputSchema", {})

        if not name:
            continue

        args_model = _build_args_model(name, input_schema)

        tool = StructuredTool.from_function(
            coroutine=_build_tool_func(name),
            name=name,
            description=description,
            args_schema=args_model,
        )
        tools.append(tool)
        logger.info("Discovered tool: %s (args: %s)", name, list(args_model.model_fields.keys()))

    return tools
