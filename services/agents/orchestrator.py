"""LangGraph orchestration: single DataAgent backed by MCP tools.

Tools are discovered dynamically from the MCP gateway via ``tools/list``.
Vendor-agnostic — works with any MCP server(s) behind the gateway.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable

import os

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models import BaseChatModel

from services.agents.agents.data_agent import create_data_agent
from services.agents.tools import mcp_tools

logger = logging.getLogger(__name__)


@dataclass
class AgentEvent:
    agent_name: str
    event_type: str  # routing, llm_call, tool_call, result, conversation, error
    message: str
    decision: str = ""
    resource_type: str = ""
    data_source: str = ""
    policy_id: str = ""
    layer: str = "agent"
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChatResult:
    run_id: str
    query: str
    user_name: str
    user_role: str
    events: list[dict] = field(default_factory=list)
    final_answer: str = ""
    agents_used: list[str] = field(default_factory=list)
    status: str = "ok"
    error: str = ""
    timestamp: str = field(
        default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )


def _get_llm() -> BaseChatModel:
    provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    model = os.getenv("LLM_MODEL", "qwen2.5:7b")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=model,
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=0.1,
            max_tokens=4096,
        )

    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=model,
        base_url=os.getenv("OLLAMA_BASE_URL", "http://ollama:11434"),
        temperature=0.1,
    )


async def run_chat(
    query: str,
    user_name: str,
    user_role: str,
    conversation_history: list[dict] | None = None,
    emit: Callable[[dict], None] | None = None,
    tlx_gateway_token: str = "",
) -> ChatResult:
    """Main orchestration entry point.

    Discovers tools from the MCP gateway, builds the agent dynamically,
    and routes the user's query through it.
    """
    run_id = str(uuid.uuid4())[:8]
    events: list[dict] = []

    def _emit(event: dict) -> None:
        events.append(event)
        if emit:
            emit(event)

    mcp_tools.set_context(
        agent_name="DataAgent",
        user_name=user_name,
        user_role=user_role,
        emit=_emit,
        tlx_gateway_token=tlx_gateway_token,
    )

    # ── Dynamic tool discovery ────────────────────────────────────────────
    _emit(AgentEvent(
        agent_name="DataAgent",
        event_type="routing",
        message="Discovering tools from MCP gateway...",
    ).to_dict())

    try:
        discovered_tools = await mcp_tools.discover_tools()
    except Exception as exc:
        logger.error("Tool discovery failed: %s", exc)
        discovered_tools = []

    if not discovered_tools:
        _emit(AgentEvent(
            agent_name="DataAgent",
            event_type="error",
            message="No tools discovered from MCP gateway. Check gateway connectivity and auth.",
        ).to_dict())
        return ChatResult(
            run_id=run_id,
            query=query,
            user_name=user_name,
            user_role=user_role,
            events=events,
            final_answer="Unable to discover tools from the MCP gateway. Please sign out and back in to refresh your session.",
            agents_used=["DataAgent"],
            status="error",
        )

    tool_names = [t.name for t in discovered_tools]
    _emit(AgentEvent(
        agent_name="DataAgent",
        event_type="routing",
        message=f"Discovered {len(discovered_tools)} tools: {', '.join(tool_names)}",
    ).to_dict())

    # ── Emit identity so the UI header shows who is calling Snowflake ────
    _gw_service_role = os.getenv("TLX_GW_SERVICE_ROLE", "").strip() or "TLX Gateway"
    _emit({
        "layer": "session_identity",
        "user": user_name,
        "role": f"via TLX Gateway → {_gw_service_role}",
    })

    _emit(AgentEvent(
        agent_name="DataAgent",
        event_type="llm_call",
        message="DataAgent is processing your query...",
    ).to_dict())

    try:
        llm = _get_llm()
        agent = create_data_agent(llm, discovered_tools)

        messages = []
        if conversation_history:
            for msg in conversation_history[:-1]:
                if msg["role"] == "user":
                    messages.append(HumanMessage(content=msg["content"]))
                elif msg["role"] == "assistant":
                    messages.append(AIMessage(content=msg["content"]))
        messages.append(HumanMessage(content=query))

        result = await agent.ainvoke({"messages": messages})

        response_messages = result.get("messages", [])
        final_answer = ""
        for msg in reversed(response_messages):
            if isinstance(msg, AIMessage) and msg.content:
                content = msg.content
                # Gemini/some providers return content as a list of blocks
                if isinstance(content, list):
                    parts = []
                    for block in content:
                        if isinstance(block, str):
                            parts.append(block)
                        elif isinstance(block, dict) and block.get("text"):
                            parts.append(block["text"])
                        elif isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    final_answer = "\n".join(parts)
                else:
                    final_answer = str(content)
                break

        if not final_answer:
            final_answer = "I wasn't able to retrieve the data. Please try rephrasing your question."

        _emit(AgentEvent(
            agent_name="DataAgent",
            event_type="result",
            message=final_answer,
        ).to_dict())

    except Exception as exc:
        error_msg = f"DataAgent encountered an error: {exc}"
        logger.exception(error_msg)
        _emit(AgentEvent(
            agent_name="DataAgent",
            event_type="error",
            message=error_msg,
        ).to_dict())
        final_answer = error_msg

    _emit(AgentEvent(
        agent_name="DataAgent",
        event_type="conversation",
        message=final_answer,
    ).to_dict())

    return ChatResult(
        run_id=run_id,
        query=query,
        user_name=user_name,
        user_role=user_role,
        events=events,
        final_answer=final_answer,
        agents_used=["DataAgent"],
    )
