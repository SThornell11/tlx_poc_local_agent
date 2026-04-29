"""Databricks MCP tools — all route through TrustLogix Gateway."""

from __future__ import annotations

from typing import Callable

from langchain_core.tools import tool

from services.agents.tools.mcp_gateway import call_tool as gateway_call

_user_context: dict[str, str] = {}
_emit_callback: Callable[[dict], None] | None = None


def set_context(
    agent_name: str,
    user_name: str,
    user_role: str,
    emit: Callable[[dict], None] | None = None,
) -> None:
    _user_context.update({
        "agent_name": agent_name,
        "user_name": user_name,
        "user_role": user_role,
    })
    global _emit_callback
    _emit_callback = emit


@tool
async def databricks_execute_sql(query: str, catalog: str = "", schema: str = "") -> str:
    """Execute SQL on Databricks Unity Catalog via the TrustLogix MCP Gateway.

    Args:
        query: SQL query to execute.
        catalog: Unity Catalog name (optional, uses default if empty).
        schema: Schema name (optional, uses default if empty).
    """
    args: dict = {"query": query}
    if catalog:
        args["catalog"] = catalog
    if schema:
        args["schema"] = schema
    return await gateway_call(
        tool_name="databricks_execute_sql",
        arguments=args,
        agent_name=_user_context.get("agent_name", ""),
        user_name=_user_context.get("user_name", ""),
        user_role=_user_context.get("user_role", ""),
        emit=_emit_callback,
    )


@tool
async def databricks_vector_search(query: str, num_results: int = 5) -> str:
    """Search Databricks vector index for similar documents.

    Uses the configured vector search index for unstructured retrieval
    (e.g., support ticket descriptions, KB articles).

    Args:
        query: Natural language search query.
        num_results: Number of results to return.
    """
    return await gateway_call(
        tool_name="databricks_vector_search",
        arguments={"query": query, "num_results": num_results},
        agent_name=_user_context.get("agent_name", ""),
        user_name=_user_context.get("user_name", ""),
        user_role=_user_context.get("user_role", ""),
        emit=_emit_callback,
    )


@tool
async def databricks_genie_query(question: str) -> str:
    """Query Databricks Genie for natural language data analysis.

    Args:
        question: Natural language question about the data.
    """
    return await gateway_call(
        tool_name="databricks_genie_query",
        arguments={"question": question},
        agent_name=_user_context.get("agent_name", ""),
        user_name=_user_context.get("user_name", ""),
        user_role=_user_context.get("user_role", ""),
        emit=_emit_callback,
    )


@tool
async def databricks_uc_function(function_name: str, parameters: dict | None = None) -> str:
    """Invoke a custom function registered in Databricks Unity Catalog.

    Args:
        function_name: Fully qualified function name (catalog.schema.function).
        parameters: Function parameters as a dictionary.
    """
    return await gateway_call(
        tool_name="databricks_uc_function",
        arguments={"function_name": function_name, "parameters": parameters or {}},
        agent_name=_user_context.get("agent_name", ""),
        user_name=_user_context.get("user_name", ""),
        user_role=_user_context.get("user_role", ""),
        emit=_emit_callback,
    )
