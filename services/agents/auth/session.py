"""Session keys, cookie helpers, and Fernet token encryption.

The two-phase auth flow stores three kinds of state:

1. **Server-side session** (Starlette ``SessionMiddleware``, signed cookie) —
   ephemeral OAuth state: PKCE verifier, oauth state nonce, current phase,
   and the remaining auth_chain. Cleared on logout.

2. **Encrypted token cookies** (Fernet, derived from ``SESSION_SECRET``) —
   the user's Phase 1 ``id_token`` / ``access_token`` (one cookie each), and
   one per-server access token cookie for each Phase 2 server. These persist
   across requests so chat/tool calls can pull the user's bearer token.

3. **In-process state** — none. Everything needed to authorize a chat or
   tool call is reconstructable from the cookies on each request.

There is intentionally NO fallback-server mechanism. If a Phase 2 server
fails PRM discovery, the auth flow raises a hard error rather than reusing
the Phase 1 token — silent fallback would let a misconfigured gateway look
"live" while bypassing per-server enforcement.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Mapping
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Response

logger = logging.getLogger(__name__)


def _bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("true", "1", "yes", "on")


# ── Routes ──────────────────────────────────────────────────────────────────

AUTH_CALLBACK_PATH = "/auth/callback"


# ── Session keys (Starlette session, signed but not encrypted) ─────────────

SESSION_AUTH_PHASE_KEY = "auth_phase"               # "user_login" or "mcp_server"
SESSION_AUTH_CHAIN_KEY = "auth_chain"               # list[str] of MCP URLs left to authenticate
SESSION_USER_IDP_KEY = "user_idp"                   # IdP type: "aad", "okta", etc.
SESSION_OAUTH_STATE_KEY = "oauth_state"             # CSRF nonce
SESSION_PKCE_VERIFIER_KEY = "pkce_verifier"
SESSION_OAUTH_ISSUER_KEY = "oauth_issuer"           # current AS issuer URL (Phase 2) or "aad" (Phase 1)
SESSION_OAUTH_RESOURCE_KEY = "oauth_resource"       # canonical resource URL (Phase 2)
SESSION_CANONICAL_URL_MAP_KEY = "canonical_url_map" # raw MCP URL → canonical resource URL


# ── Cookie names ────────────────────────────────────────────────────────────

TOKEN_COOKIE_ID = "tlx_id_token"
TOKEN_COOKIE_ACCESS = "tlx_access_token"
SERVER_TOKEN_COOKIE_PREFIX = "tlx_srv_"

# Set SECURE_COOKIES=true in .env when running behind TLS. Defaults to False
# so the localhost HTTP demo works without further configuration.
_SECURE_COOKIES = _bool_env("SECURE_COOKIES", default=False)

COOKIE_COMMON: dict = {
    "httponly": True,
    "secure": _SECURE_COOKIES,
    "samesite": "lax",
    "path": "/",
}

if _SECURE_COOKIES:
    logger.info("SECURE_COOKIES=true — token cookies will require HTTPS")


# ── Fernet helpers ─────────────────────────────────────────────────────────

_cipher_cache: dict[str, Fernet] = {}


def _get_cipher(session_secret: str) -> Fernet:
    """Derive a deterministic Fernet key from ``session_secret``.

    Cached per-secret so repeated calls in the same process don't re-derive.
    """
    cached = _cipher_cache.get(session_secret)
    if cached is not None:
        return cached
    key_material = hashlib.sha256(session_secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(key_material)
    cipher = Fernet(key)
    _cipher_cache[session_secret] = cipher
    return cipher


def _encrypt(value: str, session_secret: str) -> str:
    return _get_cipher(session_secret).encrypt(value.encode("utf-8")).decode("utf-8")


def _decrypt(value: str, session_secret: str) -> str:
    try:
        return _get_cipher(session_secret).decrypt(value.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("Encrypted cookie failed to decrypt — ignoring")
        return ""


# ── Per-server cookie naming ───────────────────────────────────────────────

def _server_cookie_name(server_url: str) -> str:
    """Stable, short cookie name derived from a hash of the server URL.

    Multiple Phase 2 tokens can coexist (e.g. TLX MCP + a hypothetical
    second downstream MCP) without colliding.
    """
    p = urlsplit(server_url.rstrip("/"))
    canonical = f"{p.scheme}://{p.netloc}{p.path}".lower()
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    short = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:16]
    return f"{SERVER_TOKEN_COOKIE_PREFIX}{short}"


# ── Public API ──────────────────────────────────────────────────────────────

def set_token_cookies(
    response: Response,
    id_token: str,
    access_token: str,
    session_secret: str,
) -> None:
    """Store the Phase 1 user tokens as encrypted cookies on *response*."""
    response.set_cookie(
        TOKEN_COOKIE_ID,
        _encrypt(id_token, session_secret),
        **COOKIE_COMMON,
    )
    response.set_cookie(
        TOKEN_COOKIE_ACCESS,
        _encrypt(access_token, session_secret),
        **COOKIE_COMMON,
    )


def set_server_token_cookie(
    response: Response,
    server_url: str,
    access_token: str,
    session_secret: str,
) -> None:
    """Store a Phase 2 per-server access token as its own encrypted cookie."""
    name = _server_cookie_name(server_url)
    response.set_cookie(name, _encrypt(access_token, session_secret), **COOKIE_COMMON)


def delete_token_cookies(response: Response) -> None:
    """Drop the Phase 1 user-token cookies. Per-server cookies are left in
    place — the caller is responsible for clearing them on logout if needed.
    """
    response.delete_cookie(TOKEN_COOKIE_ID, path=COOKIE_COMMON["path"])
    response.delete_cookie(TOKEN_COOKIE_ACCESS, path=COOKIE_COMMON["path"])


def delete_server_token_cookie(response: Response, server_url: str) -> None:
    response.delete_cookie(_server_cookie_name(server_url), path=COOKIE_COMMON["path"])


def load_token_from_cookies(
    cookies: Mapping[str, str],
    name: str,
    session_secret: str,
) -> str:
    """Decrypt and return a named Phase 1 token cookie ('' if missing)."""
    raw = cookies.get(name) or ""
    if not raw:
        return ""
    return _decrypt(raw, session_secret)


def load_server_token(
    cookies: Mapping[str, str],
    server_url: str,
    session_secret: str,
) -> str:
    """Decrypt and return the per-server Phase 2 access token for ``server_url``.

    Returns '' if no cookie is present (caller should treat as "user has not
    completed Phase 2 for this server yet").
    """
    raw = cookies.get(_server_cookie_name(server_url)) or ""
    if not raw:
        return ""
    return _decrypt(raw, session_secret)
