"""TrustLogix MCP Gateway client — Streamable HTTP transport.

Implements the MCP Streamable HTTP transport protocol:
1. ``initialize`` — handshake to get a session ID
2. ``tools/list`` — discover available tools
3. ``tools/call`` — execute a tool

Every request after ``initialize`` includes the ``Mcp-Session-Id`` header.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from threading import Lock
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

TRUSTLOGIX_MCP_GATEWAY_URL = os.getenv("TLX_MCP_URL", "").strip()
_GATEWAY_TIMEOUT = int(os.getenv("TRUSTLOGIX_MCP_TIMEOUT", "30"))

# Per-user session IDs from the gateway
_sessions: dict[str, str] = {}
_session_lock = Lock()

# Audit log
_gateway_audit: list[dict] = []
_audit_lock = Lock()


@dataclass
class GatewayEvent:
    agent_name: str
    tool_name: str
    mcp_server: str
    decision: str  # ALLOW, DENY, ERROR
    reason: str = ""
    policy_rule: str = ""
    content_inspected: bool = False
    content_flags: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    layer: str = "mcp_gateway"

    def to_dict(self) -> dict:
        return asdict(self)


def _base_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _build_headers(
    token: str,
    agent_name: str,
    user_name: str,
    user_role: str,
    session_id: str = "",
) -> dict[str, str]:
    h = _base_headers(token)
    if session_id:
        h["Mcp-Session-Id"] = session_id
    h["X-TrustLogix-User-Id"] = user_name
    h["X-TrustLogix-User-Role"] = user_role
    h["X-TrustLogix-Agent-Name"] = agent_name
    return h


def _build_jsonrpc(method: str, params: dict, req_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": req_id,
    }


def _parse_jsonrpc_result(data: dict) -> tuple[str | None, dict | None]:
    if "error" in data:
        return None, data["error"]

    result = data.get("result", {})
    if isinstance(result, str):
        return result, None

    content = result.get("content", [])
    if isinstance(content, list) and content:
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts), None

    return json.dumps(result), None


def _parse_response(resp: httpx.Response) -> dict:
    """Parse a gateway response — handles both JSON and SSE formats.

    The MCP Streamable HTTP transport may return:
    - ``application/json``: plain JSON-RPC response
    - ``text/event-stream``: SSE with ``event: message`` / ``data: {...}``
    """
    content_type = (resp.headers.get("content-type") or "").lower()

    if "text/event-stream" in content_type:
        # Parse SSE: look for data lines containing JSON-RPC
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    continue
        logger.warning("SSE response had no parseable JSON-RPC data line")
        return {}

    # Plain JSON
    text = resp.text.strip()
    if not text:
        logger.warning("Empty response body (content-type=%s)", content_type)
        return {}
    return resp.json()


def _log_audit(agent_name: str, tool_name: str, decision: str, reason: str) -> None:
    with _audit_lock:
        _gateway_audit.append({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "agent_name": agent_name,
            "tool_name": tool_name,
            "decision": decision,
            "reason": reason,
            "gateway_url": TRUSTLOGIX_MCP_GATEWAY_URL,
        })


# ---------------------------------------------------------------------------
# MCP Session management (Streamable HTTP transport)
# ---------------------------------------------------------------------------

async def _initialize_session(token: str) -> str:
    """Send ``initialize`` to the gateway and return the session ID.

    The gateway responds with an ``Mcp-Session-Id`` header that must be
    included in all subsequent requests.
    """
    headers = _base_headers(token)
    payload = _build_jsonrpc("initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "tlx-agent", "version": "1.0.0"},
    })

    async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as client:
        resp = await client.post(
            TRUSTLOGIX_MCP_GATEWAY_URL,
            headers=headers,
            json=payload,
        )

    if resp.status_code != 200:
        raise RuntimeError(
            f"MCP initialize returned HTTP {resp.status_code}: {resp.text[:300]}"
        )

    session_id = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id") or ""
    if not session_id:
        data = _parse_response(resp)
        result = data.get("result", {})
        session_id = result.get("sessionId", "")

    init_data = _parse_response(resp)
    logger.info("MCP initialize full response: %s", json.dumps(init_data)[:2000] if init_data else resp.text[:2000])

    if session_id:
        logger.info("MCP session initialized: %s", session_id[:20] + "...")
    else:
        logger.warning("MCP initialize succeeded but no session ID returned")

    return session_id


async def _get_session(token: str) -> str:
    """Get or create an MCP session for this user token."""
    # Use a hash of the token as the cache key
    import hashlib
    key = hashlib.sha256(token.encode()).hexdigest()[:16]

    with _session_lock:
        existing = _sessions.get(key)
    if existing:
        return existing

    try:
        session_id = await _initialize_session(token)
    except Exception as exc:
        logger.error("MCP session init failed: %s", exc)
        return ""

    if session_id:
        with _session_lock:
            _sessions[key] = session_id
    return session_id


def clear_session(token: str = "") -> None:
    """Clear cached session for a user token, or all sessions if no token."""
    if token:
        import hashlib
        key = hashlib.sha256(token.encode()).hexdigest()[:16]
        with _session_lock:
            _sessions.pop(key, None)
    else:
        with _session_lock:
            _sessions.clear()
    logger.info("MCP sessions cleared")


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------

async def list_tools(user_token: str = "") -> list[dict]:
    """Discover tools from the gateway via ``tools/list``."""
    if not TRUSTLOGIX_MCP_GATEWAY_URL:
        logger.error("list_tools: TLX_MCP_URL is not set")
        return []
    if not user_token:
        logger.error("list_tools: no user token — Phase 2 incomplete")
        return []

    session_id = await _get_session(user_token)

    headers = _base_headers(user_token)
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    payload = _build_jsonrpc("tools/list", {})

    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as client:
            resp = await client.post(
                TRUSTLOGIX_MCP_GATEWAY_URL,
                headers=headers,
                json=payload,
            )
    except Exception as exc:
        logger.error("list_tools failed: %s", exc)
        return []

    if resp.status_code != 200:
        logger.error("list_tools returned HTTP %d: %s", resp.status_code, resp.text[:300])
        return []

    # Log full exchange for debugging
    logger.info("list_tools DEBUG: request headers=%s", {k: v[:20] + "..." if k == "Authorization" else v for k, v in headers.items()})
    logger.info("list_tools DEBUG: response body=%r", resp.text[:3000])

    data = _parse_response(resp)

    if not data:
        logger.error("list_tools: empty/unparseable response (content-type=%s, body=%s)",
                      resp.headers.get("content-type", ""), resp.text[:500])
        return []
    if "error" in data:
        logger.error("list_tools JSON-RPC error: %s", data["error"])
        return []

    result = data.get("result", {})
    tools = result.get("tools", [])
    logger.info("list_tools: discovered %d tools from gateway", len(tools))
    return tools


# ---------------------------------------------------------------------------
# tools/call
# ---------------------------------------------------------------------------

async def call_tool(
    tool_name: str,
    arguments: dict,
    agent_name: str,
    user_name: str = "",
    user_role: str = "",
    user_token: str = "",
    emit: Callable[[dict], None] | None = None,
) -> str:
    """Call an MCP tool through the TrustLogix Gateway."""
    if not TRUSTLOGIX_MCP_GATEWAY_URL:
        msg = "TLX_MCP_URL is not set; cannot route through gateway"
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="unknown", decision="ERROR", reason=msg,
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", msg)
        return f"[ERROR] {msg}"

    if not user_token:
        msg = (
            "No per-user TLX gateway token available — the user has not "
            "completed Phase 2 OAuth against the gateway. Sign out and "
            "back in to retry."
        )
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="ERROR", reason="missing user token",
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", "Missing user token")
        return f"[ERROR] {msg}"

    session_id = await _get_session(user_token)
    headers = _build_headers(user_token, agent_name, user_name, user_role, session_id)
    payload = _build_jsonrpc("tools/call", {"name": tool_name, "arguments": arguments})

    try:
        async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as client:
            resp = await client.post(
                TRUSTLOGIX_MCP_GATEWAY_URL,
                headers=headers,
                json=payload,
            )
    except httpx.TimeoutException:
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="ERROR",
            reason="Gateway request timed out",
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", "Timeout")
        return f"[ERROR] TrustLogix MCP Gateway request timed out for tool '{tool_name}'."
    except httpx.HTTPError as exc:
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="ERROR", reason=str(exc),
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", str(exc))
        return f"[ERROR] Gateway HTTP error for tool '{tool_name}': {exc}"

    if resp.status_code in (401, 403):
        reason = f"HTTP {resp.status_code}: {resp.text[:200]}"
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="DENY", reason=reason,
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "DENY", reason)
        return f"[DENIED] Access denied for tool '{tool_name}': {reason}"

    if resp.status_code != 200:
        reason = f"HTTP {resp.status_code}: {resp.text[:200]}"
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="ERROR", reason=reason,
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", reason)
        return f"[ERROR] Gateway returned HTTP {resp.status_code} for tool '{tool_name}'."

    data = _parse_response(resp)
    logger.info("call_tool %s: parsed response keys=%s", tool_name, list(data.keys()) if data else "EMPTY")
    if not data:
        logger.error("call_tool %s: raw response content-type=%s body=%s",
                      tool_name, resp.headers.get("content-type", ""), resp.text[:500])
        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision="ERROR",
            reason="Empty/unparseable response from gateway",
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, "ERROR", "Empty response")
        return f"[ERROR] Empty response from gateway for tool '{tool_name}'."
    result_text, error = _parse_jsonrpc_result(data)
    logger.info("call_tool %s: result_text=%s error=%s",
                tool_name, (result_text or "")[:200], error)

    if error:
        code = error.get("code", 0)
        message = error.get("message", "Unknown error")
        if code in (-32001, -32003) or "denied" in message.lower() or "not authorized" in message.lower():
            decision = "DENY"
        else:
            decision = "ERROR"

        event = GatewayEvent(
            agent_name=agent_name, tool_name=tool_name,
            mcp_server="trustlogix", decision=decision, reason=message,
            policy_rule=error.get("data", {}).get("policy", "") if isinstance(error.get("data"), dict) else "",
        )
        if emit:
            emit(event.to_dict())
        _log_audit(agent_name, tool_name, decision, message)
        return f"[{decision}] {message}"

    event = GatewayEvent(
        agent_name=agent_name, tool_name=tool_name,
        mcp_server="trustlogix", decision="ALLOW",
        reason="Policy evaluation passed",
    )
    if emit:
        emit(event.to_dict())
    _log_audit(agent_name, tool_name, "ALLOW", "OK")
    return result_text or ""


def get_gateway_audit() -> list[dict]:
    with _audit_lock:
        return list(_gateway_audit)


def clear_gateway_audit() -> None:
    with _audit_lock:
        _gateway_audit.clear()
