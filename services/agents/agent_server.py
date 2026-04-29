"""FastAPI agent server — chat endpoint with SSE streaming, two-phase auth,
and TrustLogix MCP gateway integration.

Auth model
==========

Two-phase OAuth 2.1 + PKCE (see ``services/agents/auth/auth_flow.py``):

  Phase 1 — User login via the configured IdP (Entra or Okta). Establishes
            the user's identity. Tokens are stored as Fernet-encrypted
            cookies on the response.
  Phase 2 — Per-user OAuth against the TrustLogix MCP gateway (PRM discovery
            + PKCE). The user gets a per-gateway access token, stored in
            its own encrypted cookie, which the agent then sends as Bearer
            when calling tools through the gateway.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any
from urllib.parse import urlencode

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.middleware.sessions import SessionMiddleware

from services.agents.auth.auth_flow import (
    auth_login as flow_auth_login,
    auth_callback as flow_auth_callback,
    clear_auth_session,
)
from services.agents.auth.config import load_config
from services.agents.auth.session import (
    SESSION_CANONICAL_URL_MAP_KEY,
    SESSION_USER_IDP_KEY,
    TOKEN_COOKIE_ACCESS,
    TOKEN_COOKIE_ID,
    delete_server_token_cookie,
    delete_token_cookies,
    load_server_token,
    load_token_from_cookies,
)
from services.agents.orchestrator import run_chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

CONFIG = load_config()

app = FastAPI(title="TrustLogix POC Agent", version="1.0.0")

# ── Middleware ────────────────────────────────────────────────────────────────
# SessionMiddleware: short-lived signed cookie holding ephemeral OAuth state
# (PKCE verifier, state nonce, current phase, auth_chain). Tokens themselves
# are stored in separate Fernet-encrypted cookies set by auth_flow.
_SECURE_COOKIES = os.getenv("SECURE_COOKIES", "").strip().lower() in ("true", "1", "yes", "on")
app.add_middleware(
    SessionMiddleware,
    secret_key=CONFIG.session_secret,
    same_site="lax",
    https_only=_SECURE_COOKIES,
    max_age=3600,
)

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory conversation history per session
_conversations: dict[str, list[dict]] = {}
_MAX_HISTORY = 20


# ── Authenticated user object (built fresh per request) ──────────────────────

class AuthedUser:
    """In-memory representation of an authenticated user.

    Pulled from the signed session cookie + Fernet-encrypted token cookies on
    every request — there's no server-side session store. The TLX gateway
    token (if any) is loaded lazily by the orchestrator.

    There is intentionally no ``role`` field. The user's effective role is
    whatever Snowflake's ``CURRENT_ROLE()`` returns at the moment of each
    chat request, and that is surfaced live via the ``session_identity``
    event emitted by the orchestrator. We do not stage a role here.
    """

    def __init__(
        self,
        name: str,
        email: str,
        id_token: str = "",
        access_token: str = "",
    ) -> None:
        self.name = name
        self.email = email
        self.id_token = id_token
        self.access_token = access_token
        self.session_id = f"session-{email or name}"


def _get_user(request: Request) -> AuthedUser | None:
    """Reconstruct the authed user from session + encrypted token cookies."""
    user = request.session.get("user")
    if user:
        id_token = load_token_from_cookies(
            request.cookies, TOKEN_COOKIE_ID, CONFIG.session_secret,
        )
        access_token = load_token_from_cookies(
            request.cookies, TOKEN_COOKIE_ACCESS, CONFIG.session_secret,
        )
        if id_token and access_token:
            return AuthedUser(
                name=str(user),
                email=str(user),
                id_token=id_token,
                access_token=access_token,
            )
        logger.debug("Session has user but missing token cookies")

    return None


# ── IdP logout URL builder ──────────────────────────────────────────────────

def _build_idp_logout_url(request: Request, id_token_hint: str) -> str:
    """Return the IdP end-session URL based on which IdP the user logged in with."""
    idp = request.session.get(SESSION_USER_IDP_KEY, CONFIG.auth_idp)
    if idp == "okta" and CONFIG.okta_domain:
        params = {
            "post_logout_redirect_uri": FRONTEND_ORIGIN,
            "id_token_hint": id_token_hint,
        }
        return f"{CONFIG.okta_issuer}/v1/logout?{urlencode(params)}"
    if CONFIG.aad_tenant_id and id_token_hint:
        params = {
            "post_logout_redirect_uri": FRONTEND_ORIGIN,
            "id_token_hint": id_token_hint,
        }
        return (
            f"https://login.microsoftonline.com/{CONFIG.aad_tenant_id}"
            f"/oauth2/v2.0/logout?{urlencode(params)}"
        )
    return "/"


# ── Login redirect helper ────────────────────────────────────────────────────

def _render_login_redirect(request: Request, error: str | None = None, status_code: int = 303):
    """auth_flow expects a render_login callable that returns an error response.

    The frontend SPA owns the actual login screen, so on auth failure we just
    bounce the user back to / with an ?error= query param. The SPA can read it
    and surface a banner.
    """
    target = "/"
    if error:
        target = f"/?{urlencode({'error': error})}"
    return RedirectResponse(url=target, status_code=303)


# ── Auth routes (server-side redirects, browser-driven) ──────────────────────

@app.get("/auth/login")
async def auth_login_route(request: Request, idp: str | None = None):
    """Phase-aware login dispatcher. Browser navigates here directly.

    The ``?idp=`` query param signals a fresh login from the login screen.
    Clears any stale session state and starts Phase 1 with the chosen IdP.
    Without ``?idp=``, this is a Phase 2 continuation (redirect from Phase 1
    callback) and the session drives which phase runs.
    """
    if idp and idp in ("entra", "okta"):
        clear_auth_session(request.session)
        CONFIG.auth_idp = idp
    return flow_auth_login(request, CONFIG)


@app.get("/auth/callback", name="auth_callback")
async def auth_callback_route(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Phase-aware callback dispatcher. Entra/TLX AS redirect lands here."""
    return flow_auth_callback(
        request, CONFIG, _render_login_redirect,
        code, state, error, error_description,
    )


@app.get("/auth/logout")
async def auth_logout_route(request: Request):
    """Server-side logout — clears session + cookies, then bounces to the
    IdP's end-session endpoint.
    """
    id_token_hint = load_token_from_cookies(
        request.cookies, TOKEN_COOKIE_ID, CONFIG.session_secret,
    )
    redirect_url = _build_idp_logout_url(request, id_token_hint)
    clear_auth_session(request.session)
    try:
        request.session.clear()
    except Exception:
        pass

    response = RedirectResponse(url=redirect_url, status_code=303)
    delete_token_cookies(response)
    if CONFIG.tlx_mcp_url:
        delete_server_token_cookie(response, CONFIG.tlx_mcp_url)
    return response


# ── JSON auth introspection (for the SPA) ────────────────────────────────────

@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Return the current user, or 401 if not signed in.

    No role field — that comes live from Snowflake via the
    ``session_identity`` event on each chat turn.
    """
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {
        "user": {
            "name": user.name,
            "email": user.email,
        }
    }


@app.post("/api/auth/logout")
async def auth_logout_api(request: Request):
    """API-style logout — returns the IdP logout URL so the SPA can navigate."""
    id_token_hint = load_token_from_cookies(
        request.cookies, TOKEN_COOKIE_ID, CONFIG.session_secret,
    )
    logout_url = _build_idp_logout_url(request, id_token_hint)
    clear_auth_session(request.session)
    try:
        request.session.clear()
    except Exception:
        pass

    response = JSONResponse({"logout_url": logout_url})
    delete_token_cookies(response)
    if CONFIG.tlx_mcp_url:
        delete_server_token_cookie(response, CONFIG.tlx_mcp_url)
    return response


# ── Chat endpoint ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


@app.post("/api/chat")
async def chat(request: Request):
    """Main chat endpoint — returns SSE stream of agent events + final answer."""
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")

    body = await request.json()
    message = body.get("message", "").strip()
    conversation_id = body.get("conversation_id", user.session_id)

    if not message:
        raise HTTPException(400, "Empty message")

    # Pull the per-user TLX gateway token from cookies (if Phase 2 ran).
    # The cookie name is derived from the server URL hash. PRM discovery
    # may return a canonical `resource` that differs from the raw
    # TLX_MCP_URL, so check the session's canonical_url_map first.
    tlx_token = ""
    if CONFIG.enforce_enabled and CONFIG.tlx_mcp_url:
        canonical_map = request.session.get(SESSION_CANONICAL_URL_MAP_KEY) or {}
        lookup_url = canonical_map.get(CONFIG.tlx_mcp_url, CONFIG.tlx_mcp_url)
        tlx_token = load_server_token(
            request.cookies, lookup_url, CONFIG.session_secret,
        )
        if not tlx_token:
            logger.warning(
                "Gateway mode is on but user %s has no TLX token cookie — "
                "Phase 2 likely incomplete; tool calls will fail",
                user.email,
            )

    history = _conversations.setdefault(conversation_id, [])
    history.append({"role": "user", "content": message})
    if len(history) > _MAX_HISTORY * 2:
        history[:] = history[-_MAX_HISTORY * 2:]

    event_queue: asyncio.Queue = asyncio.Queue()

    def emit_event(event: dict) -> None:
        event_queue.put_nowait(event)

    async def run_and_signal():
        try:
            result = await run_chat(
                query=message,
                user_name=user.email or user.name,
                user_role="",  # surfaced live via session_identity event, not staged here
                conversation_history=history,
                emit=emit_event,
                tlx_gateway_token=tlx_token,
            )
            final_text = result.final_answer
            history.append({"role": "assistant", "content": final_text})

            event_queue.put_nowait({
                "_type": "final",
                "text": final_text,
                "agents_used": result.agents_used,
                "run_id": result.run_id,
            })
        except Exception as exc:
            logger.exception("Chat orchestration error")
            event_queue.put_nowait({
                "_type": "error",
                "message": str(exc),
            })
        finally:
            event_queue.put_nowait(None)  # Sentinel

    async def sse_generator():
        task = asyncio.create_task(run_and_signal())
        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break

                if event.get("_type") == "final":
                    yield {
                        "event": "answer",
                        "data": json.dumps({
                            "text": event["text"],
                            "agents_used": event.get("agents_used", []),
                            "run_id": event.get("run_id", ""),
                        }),
                    }
                elif event.get("_type") == "error":
                    yield {
                        "event": "error",
                        "data": json.dumps({"message": event["message"]}),
                    }
                elif event.get("layer") == "guardrails":
                    yield {
                        "event": "guardrail",
                        "data": json.dumps(event),
                    }
                elif event.get("layer") == "mcp_gateway":
                    yield {
                        "event": "gateway",
                        "data": json.dumps(event),
                    }
                elif event.get("layer") == "session_identity":
                    yield {
                        "event": "session_identity",
                        "data": json.dumps(event),
                    }
                else:
                    yield {
                        "event": "agent",
                        "data": json.dumps(event),
                    }

            yield {"event": "done", "data": "{}"}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(sse_generator())


# ── Utility endpoints ────────────────────────────────────────────────────────

@app.get("/api/tools")
async def list_gateway_tools(request: Request):
    """List tools advertised by the TrustLogix MCP gateway."""
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    tlx_token = ""
    if CONFIG.enforce_enabled and CONFIG.tlx_mcp_url:
        canonical_map = request.session.get(SESSION_CANONICAL_URL_MAP_KEY) or {}
        lookup_url = canonical_map.get(CONFIG.tlx_mcp_url, CONFIG.tlx_mcp_url)
        tlx_token = load_server_token(
            request.cookies, lookup_url, CONFIG.session_secret,
        )
    if not tlx_token:
        return {"tools": [], "error": "No gateway token — sign out and back in"}
    from services.agents.tools import mcp_tools
    mcp_tools.set_context(
        agent_name="system",
        user_name=user.email or user.name,
        user_role="",
        tlx_gateway_token=tlx_token,
    )
    tools = await mcp_tools.discover_tools()
    return {
        "gateway": CONFIG.tlx_mcp_url,
        "tool_count": len(tools),
        "tools": [
            {"name": t.name, "description": t.description, "args": list(t.args_schema.model_fields.keys()) if t.args_schema else []}
            for t in tools
        ],
    }


@app.post("/api/reset")
async def reset_connection(request: Request):
    """Clear cached MCP sessions so the gateway picks up fresh policies.

    Call this after changing policies in TrustLogix — the next query will
    establish a new MCP session and re-discover tools.
    """
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    from services.agents.tools.mcp_gateway import clear_session, clear_gateway_audit
    clear_session()
    clear_gateway_audit()
    # Clear conversation history for this user
    for key in list(_conversations.keys()):
        if user.email in key or user.name in key:
            _conversations.pop(key, None)
    logger.info("Connection reset by %s — MCP sessions and audit cleared", user.email)
    return {"status": "reset", "message": "MCP sessions cleared. Next query will re-establish connection."}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "agent-server",
        "timestamp": time.time(),
        "gateway_mode": CONFIG.enforce_enabled,
    }


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return {"messages": _conversations.get(conversation_id, [])}


@app.delete("/api/conversations/{conversation_id}")
async def clear_conversation(conversation_id: str, request: Request):
    user = _get_user(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    _conversations.pop(conversation_id, None)
    return {"status": "cleared"}


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    llm_model = os.getenv("LLM_MODEL", "qwen2.5:7b")
    logger.info(
        "Agent server starting on port 5100 (gateway_mode=%s, tlx_url=%s, llm=%s)",
        CONFIG.enforce_enabled,
        CONFIG.tlx_mcp_url or "<unset>",
        llm_model,
    )
    llm_provider = os.getenv("LLM_PROVIDER", "ollama").lower()
    if llm_provider == "ollama":
        try:
            import httpx
            ollama_url = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{ollama_url}/api/tags")
                models = [m["name"] for m in resp.json().get("models", [])]
                if llm_model not in models:
                    logger.info("Pulling %s model...", llm_model)
                    await client.post(
                        f"{ollama_url}/api/pull",
                        json={"name": llm_model},
                        timeout=600,
                    )
                    logger.info("Model pull complete")
        except Exception as exc:
            logger.warning("Could not check/pull Ollama model: %s", exc)
    else:
        logger.info("Using %s provider — skipping Ollama model pull", llm_provider)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5100)
