"""Two-phase OAuth 2.1 + PKCE authentication for the TrustLogix POC agent.

Supports **Entra** or **Okta** as the Phase 1 user-login IdP (selected by
``AUTH_IDP`` in ``.env``).  Phase 2 discovers the gateway's authorization
server via RFC 9728 PRM and picks matching credentials automatically —
the gateway can advertise either Entra or Okta.

Phase 1 — User Login:
    Standard OIDC + PKCE against the configured IdP.  The user lands at
    /auth/login, is redirected to the IdP, comes back to /auth/callback,
    and the agent exchanges the code for id_token + access_token.

Phase 2 — MCP gateway Auth:
    For the TLX MCP gateway URL, perform RFC 9728 PRM discovery, then run
    another OAuth+PKCE flow against the discovered AS.  The per-user
    access token is stored in its own encrypted cookie and sent as the
    Bearer for every tool call routed through the gateway.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from services.agents.auth.config import UIConfig
from services.agents.auth.session import (
    AUTH_CALLBACK_PATH,
    SESSION_AUTH_CHAIN_KEY,
    SESSION_AUTH_PHASE_KEY,
    SESSION_CANONICAL_URL_MAP_KEY,
    SESSION_OAUTH_ISSUER_KEY,
    SESSION_OAUTH_RESOURCE_KEY,
    SESSION_OAUTH_STATE_KEY,
    SESSION_PKCE_VERIFIER_KEY,
    SESSION_USER_IDP_KEY,
    delete_token_cookies,
    set_server_token_cookie,
    set_token_cookies,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PRM / OIDC discovery helpers
# ---------------------------------------------------------------------------

def _make_prm_url(mcp_url: str) -> str:
    """RFC 9728 §3.1: insert ``/.well-known/oauth-protected-resource`` before
    the path component of *mcp_url*.
    """
    p = urlsplit(mcp_url.rstrip("/"))
    base = f"{p.scheme}://{p.netloc}"
    path = p.path or ""
    return f"{base}/.well-known/oauth-protected-resource{path}"


def _discover_prm(mcp_url: str) -> dict[str, Any]:
    """Fetch Protected Resource Metadata for *mcp_url*."""
    prm_url = _make_prm_url(mcp_url)
    with httpx.Client(timeout=10) as client:
        resp = client.get(prm_url)
        resp.raise_for_status()
        return resp.json() or {}


def _discover_as_metadata(issuer: str) -> dict[str, Any]:
    """Fetch the OIDC discovery document from *issuer*.

    S256 PKCE is always used (MCP spec requirement) regardless of whether the
    provider advertises it. Azure AD supports S256 but omits it from its
    discovery document, so we log a warning instead of failing.
    """
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    with httpx.Client(timeout=10) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json() or {}
    supported = data.get("code_challenge_methods_supported") or []
    if "S256" not in supported:
        logger.warning(
            "AS %s does not advertise S256 PKCE support "
            "(code_challenge_methods_supported=%s) — proceeding anyway",
            issuer,
            supported,
        )
    return data


# ---------------------------------------------------------------------------
# Snowflake exclusion — Snowflake is keyed by JWT, never by OAuth chain
# ---------------------------------------------------------------------------

def _is_snowflake_server(server_url: str) -> bool:
    return "snowflakecomputing.com" in (server_url or "").lower()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_redirect_uri(request: Request) -> str:
    """Return the OAuth redirect URI.

    Uses FRONTEND_ORIGIN so the URI matches what's registered in the IdP,
    regardless of internal Docker networking.
    """
    import os
    frontend = os.getenv("FRONTEND_ORIGIN", "").rstrip("/")
    if frontend:
        return f"{frontend}{AUTH_CALLBACK_PATH}"
    try:
        return str(request.url_for("auth_callback"))
    except Exception:
        host = request.url.hostname or "localhost"
        scheme = request.url.scheme or "http"
        port = f":{request.url.port}" if request.url.port else ""
        return f"{scheme}://{host}{port}{AUTH_CALLBACK_PATH}"


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_aud": False})
    except Exception:
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return {}
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
            return json.loads(decoded.decode("utf-8"))
        except Exception:
            return {}


def _get_user_display(claims: dict[str, Any]) -> str:
    if not isinstance(claims, dict):
        return ""
    return (
        claims.get("upn")
        or claims.get("preferred_username")
        or claims.get("email")
        or claims.get("name")
        or ""
    )


def _generate_pkce() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(64)
    challenge_bytes = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def clear_auth_session(session: dict) -> None:
    for key in [
        SESSION_OAUTH_STATE_KEY,
        SESSION_PKCE_VERIFIER_KEY,
        SESSION_OAUTH_ISSUER_KEY,
        SESSION_OAUTH_RESOURCE_KEY,
        SESSION_AUTH_PHASE_KEY,
        SESSION_AUTH_CHAIN_KEY,
        SESSION_USER_IDP_KEY,
        SESSION_CANONICAL_URL_MAP_KEY,
        "user",
    ]:
        try:
            session.pop(key, None)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Auth chain builder — Snowflake explicitly excluded
# ---------------------------------------------------------------------------

def build_auth_chain(config: UIConfig) -> list[str]:
    if not config.enforce_enabled:
        return []
    if not config.tlx_mcp_url:
        logger.warning(
            "enforce_enabled=True but TLX_MCP_URL is empty; skipping Phase 2"
        )
        return []
    return [config.tlx_mcp_url]


# ---------------------------------------------------------------------------
# Phase 1 — Entra authorize / token helpers
# ---------------------------------------------------------------------------

def _build_entra_authorize_url(
    config: UIConfig,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    base = (
        f"https://login.microsoftonline.com/"
        f"{config.aad_tenant_id}/oauth2/v2.0/authorize"
    )
    scope_parts = ["openid", "profile", "email", "offline_access"]
    if config.aad_api_scope:
        scope_parts.append(config.aad_api_scope)
    params = {
        "client_id": config.aad_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(scope_parts),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "response_mode": "query",
    }
    return base + "?" + urlencode(params)


def _entra_token_endpoint(config: UIConfig) -> str:
    return (
        f"https://login.microsoftonline.com/"
        f"{config.aad_tenant_id}/oauth2/v2.0/token"
    )


# ---------------------------------------------------------------------------
# Phase 1 — Okta authorize / token helpers
# ---------------------------------------------------------------------------

def _build_okta_authorize_url(
    config: UIConfig,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    base = f"{config.okta_issuer}/v1/authorize"
    params = {
        "client_id": config.okta_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return base + "?" + urlencode(params)


def _okta_token_endpoint(config: UIConfig) -> str:
    return f"{config.okta_issuer}/v1/token"


# ---------------------------------------------------------------------------
# Phase 1 — User Login (IdP-aware)
# ---------------------------------------------------------------------------

def _phase1_user_login(request: Request, config: UIConfig) -> Response:
    """Redirect the user to the configured IdP's authorize endpoint."""
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    idp_tag = config.auth_idp  # "entra" or "okta"

    try:
        request.session[SESSION_OAUTH_STATE_KEY] = state
        request.session[SESSION_PKCE_VERIFIER_KEY] = code_verifier
        request.session[SESSION_OAUTH_ISSUER_KEY] = idp_tag
        request.session[SESSION_USER_IDP_KEY] = idp_tag
    except Exception:
        logger.exception("Failed to persist Phase 1 auth state in session")
        return RedirectResponse(
            url="/login?error=Failed+to+initiate+login",
            status_code=303,
        )

    try:
        redirect_uri = _get_redirect_uri(request)
        if idp_tag == "okta":
            authorize_url = _build_okta_authorize_url(
                config, redirect_uri, state, code_challenge,
            )
        else:
            authorize_url = _build_entra_authorize_url(
                config, redirect_uri, state, code_challenge,
            )
    except RuntimeError as exc:
        logger.error("Phase 1 login config error: %s", exc)
        return RedirectResponse(
            url=f"/login?error={urlencode({'e': str(exc)})[2:]}",
            status_code=303,
        )

    logger.info("Phase 1: redirecting user to %s authorize endpoint", idp_tag)
    return RedirectResponse(url=authorize_url, status_code=302)


def _phase1_callback(
    request: Request,
    config: UIConfig,
    render_login: Callable,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
) -> Response:
    stored_state = request.session.get(SESSION_OAUTH_STATE_KEY)
    code_verifier = request.session.pop(SESSION_PKCE_VERIFIER_KEY, None)
    stored_idp = request.session.get(SESSION_USER_IDP_KEY, "entra")
    request.session.pop(SESSION_OAUTH_ISSUER_KEY, None)
    request.session.pop(SESSION_OAUTH_STATE_KEY, None)

    if error:
        logger.error("Phase 1 OAuth error: %s (%s)", error, error_description)
        clear_auth_session(request.session)
        return render_login(request, error=error_description or error, status_code=400)

    if not code or not state or state != stored_state or not code_verifier:
        logger.error(
            "Invalid Phase 1 callback state (code=%s, state_match=%s, verifier=%s)",
            bool(code), state == stored_state, bool(code_verifier),
        )
        clear_auth_session(request.session)
        return render_login(
            request, error="Invalid authorization state. Please try again.",
            status_code=400,
        )

    # Select token endpoint and credentials based on Phase 1 IdP.
    if stored_idp == "okta":
        token_endpoint = _okta_token_endpoint(config)
        client_id = config.okta_client_id
        client_secret = config.okta_client_secret
    else:
        token_endpoint = _entra_token_endpoint(config)
        client_id = config.aad_client_id
        client_secret = config.aad_client_secret

    redirect_uri = _get_redirect_uri(request)
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token exchange returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        token_body = resp.json() or {}
    except Exception as exc:
        logger.exception("Phase 1 token exchange failed: %s", exc)
        clear_auth_session(request.session)
        return render_login(
            request, error="Failed to exchange authorization code.", status_code=400,
        )

    access_token = token_body.get("access_token")
    id_token = token_body.get("id_token")
    if not access_token or not id_token:
        logger.error("Phase 1 token response missing access_token or id_token")
        clear_auth_session(request.session)
        return render_login(
            request, error="Login response missing required tokens.", status_code=400,
        )

    id_claims = _decode_jwt_claims(id_token)
    access_claims = _decode_jwt_claims(access_token)
    display_name = (
        _get_user_display(id_claims) or _get_user_display(access_claims) or "user"
    )

    try:
        request.session["user"] = display_name
        request.session[SESSION_USER_IDP_KEY] = stored_idp
        logger.info(
            "Phase 1 complete: user %s authenticated via %s (id_token=%d chars, "
            "access_token=%d chars)",
            display_name, stored_idp, len(id_token), len(access_token),
        )
    except Exception:
        logger.exception("Failed to store user in session.")
        clear_auth_session(request.session)
        return render_login(
            request, error="Unable to persist login session.", status_code=400,
        )

    auth_chain = build_auth_chain(config)
    request.session[SESSION_AUTH_PHASE_KEY] = "mcp_server" if auth_chain else "user_login"
    request.session[SESSION_AUTH_CHAIN_KEY] = auth_chain

    response = RedirectResponse(
        url="/auth/login" if auth_chain else "/",
        status_code=303,
    )
    delete_token_cookies(response)
    set_token_cookies(response, id_token, access_token, config.session_secret)
    return response


# ---------------------------------------------------------------------------
# Phase 2 — MCP Server Auth (PRM discovery + PKCE)
# ---------------------------------------------------------------------------

def _phase2_mcp_login(request: Request, config: UIConfig) -> Response:
    """Initiate OAuth for the next MCP server in the auth chain.

    Credentials are selected dynamically based on the discovered issuer,
    so the gateway can advertise either Entra or Okta as its AS.
    """
    auth_chain = list(request.session.get(SESSION_AUTH_CHAIN_KEY, []))
    if not auth_chain:
        request.session[SESSION_AUTH_PHASE_KEY] = "user_login"
        return RedirectResponse(url="/", status_code=303)

    current_server_url = auth_chain[0]

    if _is_snowflake_server(current_server_url):
        logger.error(
            "Phase 2: Snowflake URL %s appeared in auth_chain — refusing.",
            current_server_url,
        )
        clear_auth_session(request.session)
        return RedirectResponse(
            url="/?" + urlencode({
                "error": "Snowflake URL must not be in the OAuth auth chain. "
                         "Check TLX_MCP_URL configuration.",
            }),
            status_code=303,
        )

    # PRM + AS discovery — must succeed, no fallback.
    try:
        prm = _discover_prm(current_server_url)
    except Exception as exc:
        logger.error(
            "Phase 2: PRM discovery failed for %s — %s.",
            current_server_url, exc,
        )
        clear_auth_session(request.session)
        return RedirectResponse(
            url="/?" + urlencode({
                "error": (
                    f"PRM discovery failed for {current_server_url}: {exc}. "
                    f"Verify the gateway is reachable and exposes "
                    f"/.well-known/oauth-protected-resource."
                ),
            }),
            status_code=303,
        )

    authorization_servers = prm.get("authorization_servers") or []
    if not authorization_servers:
        logger.error(
            "Phase 2: PRM for %s did not list any authorization_servers.",
            current_server_url,
        )
        clear_auth_session(request.session)
        return RedirectResponse(
            url="/?" + urlencode({
                "error": (
                    f"Gateway PRM for {current_server_url} advertises no "
                    f"authorization_servers. Cannot complete Phase 2."
                ),
            }),
            status_code=303,
        )

    issuer = authorization_servers[0]
    try:
        as_metadata = _discover_as_metadata(issuer)
    except Exception as exc:
        logger.error(
            "Phase 2: AS discovery failed for issuer %s — %s",
            issuer, exc,
        )
        clear_auth_session(request.session)
        return RedirectResponse(
            url="/?" + urlencode({
                "error": f"AS discovery failed for issuer {issuer}: {exc}",
            }),
            status_code=303,
        )

    canonical_resource = prm.get("resource") or current_server_url

    # Pick credentials that match the discovered AS.
    client_id, _client_secret = config.credentials_for_issuer(issuer)

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)

    try:
        request.session[SESSION_OAUTH_STATE_KEY] = state
        request.session[SESSION_PKCE_VERIFIER_KEY] = code_verifier
        request.session[SESSION_OAUTH_ISSUER_KEY] = issuer
        request.session[SESSION_OAUTH_RESOURCE_KEY] = canonical_resource
        canonical_map: dict[str, str] = dict(
            request.session.get(SESSION_CANONICAL_URL_MAP_KEY) or {}
        )
        canonical_map[current_server_url] = canonical_resource
        request.session[SESSION_CANONICAL_URL_MAP_KEY] = canonical_map
    except Exception:
        logger.exception("Failed to persist Phase 2 auth state in session")
        return RedirectResponse(
            url="/login?error=Failed+to+initiate+login",
            status_code=303,
        )

    redirect_uri = _get_redirect_uri(request)

    prm_scopes = prm.get("scopes_supported") or []
    if prm_scopes:
        scope = " ".join(prm_scopes)
        logger.info("Phase 2: using PRM-advertised scope %r for %s", scope, current_server_url)
    else:
        scope = "openid profile email"
        logger.info("Phase 2: PRM had no scopes — falling back to %r", scope)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_endpoint = as_metadata["authorization_endpoint"]
    return RedirectResponse(
        url=auth_endpoint + "?" + urlencode(params), status_code=302,
    )


def _phase2_callback(
    request: Request,
    config: UIConfig,
    render_login: Callable,
    code: str | None,
    state: str | None,
    error: str | None,
    error_description: str | None,
) -> Response:
    stored_state = request.session.get(SESSION_OAUTH_STATE_KEY)
    code_verifier = request.session.pop(SESSION_PKCE_VERIFIER_KEY, None)
    issuer = request.session.pop(SESSION_OAUTH_ISSUER_KEY, None)
    resource_url = request.session.pop(SESSION_OAUTH_RESOURCE_KEY, None)
    request.session.pop(SESSION_OAUTH_STATE_KEY, None)

    if error:
        logger.error("Phase 2 OAuth error: %s (%s)", error, error_description)
        clear_auth_session(request.session)
        return render_login(request, error=error_description or error, status_code=400)

    if (
        not code
        or not state
        or state != stored_state
        or not code_verifier
        or not issuer
        or not resource_url
    ):
        logger.error(
            "Invalid Phase 2 callback state (code=%s, state_match=%s, "
            "verifier=%s, issuer=%s, resource=%s)",
            bool(code), state == stored_state, bool(code_verifier),
            bool(issuer), bool(resource_url),
        )
        clear_auth_session(request.session)
        return render_login(
            request, error="Invalid authorization state. Please try again.",
            status_code=400,
        )

    try:
        as_metadata = _discover_as_metadata(issuer)
        token_endpoint = as_metadata.get("token_endpoint")
        if not token_endpoint:
            raise RuntimeError("AS metadata missing token_endpoint")
    except Exception as exc:
        logger.exception("Failed to fetch AS metadata during Phase 2 callback: %s", exc)
        clear_auth_session(request.session)
        return render_login(
            request, error="Failed to complete login. Please retry.", status_code=400,
        )

    # Dynamic credential selection based on the discovered issuer.
    client_id, client_secret = config.credentials_for_issuer(issuer)

    redirect_uri = _get_redirect_uri(request)
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token exchange returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        token_body = resp.json() or {}
    except Exception as exc:
        logger.exception("Phase 2 token exchange failed: %s", exc)
        clear_auth_session(request.session)
        return render_login(
            request, error="Failed to exchange authorization code.", status_code=400,
        )

    access_token = token_body.get("access_token")
    if not access_token:
        logger.error("Phase 2 token response missing access_token for %s", resource_url)
        clear_auth_session(request.session)
        return render_login(
            request, error="Login response missing access token.", status_code=400,
        )

    auth_chain = list(request.session.get(SESSION_AUTH_CHAIN_KEY, []))
    if auth_chain:
        auth_chain = auth_chain[1:]
    request.session[SESSION_AUTH_CHAIN_KEY] = auth_chain

    if not auth_chain:
        request.session[SESSION_AUTH_PHASE_KEY] = "user_login"
        redirect_target = "/"
    else:
        redirect_target = "/auth/login"

    response = RedirectResponse(url=redirect_target, status_code=303)
    set_server_token_cookie(response, resource_url, access_token, config.session_secret)
    logger.info(
        "Phase 2: per-server token stored for %s (access_token=%d chars); "
        "auth_chain remaining=%d",
        resource_url, len(access_token), len(auth_chain),
    )
    return response


# ---------------------------------------------------------------------------
# Public dispatchers — phase-aware
# ---------------------------------------------------------------------------

def auth_login(request: Request, config: UIConfig) -> Response:
    phase = request.session.get(SESSION_AUTH_PHASE_KEY, "user_login")
    if phase == "user_login":
        return _phase1_user_login(request, config)
    return _phase2_mcp_login(request, config)


def auth_callback(
    request: Request,
    config: UIConfig,
    render_login: Callable,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    phase = request.session.get(SESSION_AUTH_PHASE_KEY, "user_login")
    if phase == "mcp_server":
        return _phase2_callback(
            request, config, render_login, code, state, error, error_description,
        )
    return _phase1_callback(
        request, config, render_login, code, state, error, error_description,
    )
