"""UIConfig — runtime configuration loaded from environment variables.

Supports **Entra** or **Okta** as the Phase 1 user-login IdP, selected by
``AUTH_IDP``. Phase 2 (TrustLogix MCP gateway) discovers its own
authorization server via PRM and picks matching credentials automatically.

**No silent fallbacks.** ``__post_init__`` raises ``RuntimeError`` at startup
if any required setting is missing or inconsistent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class UIConfig:
    # ── IdP selector ──────────────────────────────────────────────────────
    auth_idp: str = field(default_factory=lambda: _env("AUTH_IDP", "entra").lower())

    # ── Entra ID ──────────────────────────────────────────────────────────
    aad_tenant_id: str = field(default_factory=lambda: _env("ENTRAID_TENANT_ID"))
    aad_client_id: str = field(default_factory=lambda: _env("ENTRAID_CLIENT_ID"))
    aad_client_secret: str = field(default_factory=lambda: _env("ENTRAID_CLIENT_SECRET"))
    aad_api_scope: str = field(default_factory=lambda: _env("ENTRAID_API_SCOPE"))

    # ── Okta ──────────────────────────────────────────────────────────────
    okta_domain: str = field(default_factory=lambda: _env("OKTA_DOMAIN"))
    okta_client_id: str = field(default_factory=lambda: _env("OKTA_CLIENT_ID"))
    okta_client_secret: str = field(default_factory=lambda: _env("OKTA_CLIENT_SECRET"))
    okta_auth_server_id: str = field(default_factory=lambda: _env("OKTA_AUTH_SERVER_ID", "default"))

    # ── TrustLogix MCP gateway ────────────────────────────────────────────
    tlx_mcp_url: str = field(default_factory=lambda: _env("TLX_MCP_URL"))

    # ── Cookie/session encryption ─────────────────────────────────────────
    session_secret: str = field(default_factory=lambda: _env("SESSION_SECRET"))

    def __post_init__(self) -> None:
        missing: list[str] = []
        if not self.session_secret:
            missing.append(
                "SESSION_SECRET (generate with: "
                "python -c \"import secrets; print(secrets.token_hex(32))\")"
            )
        if not self.tlx_mcp_url:
            missing.append("TLX_MCP_URL (TrustLogix MCP gateway URL)")

        if self.okta_domain or self.okta_client_id or self.okta_client_secret:
            if not self.okta_domain:
                missing.append("OKTA_DOMAIN")
            if not self.okta_client_id:
                missing.append("OKTA_CLIENT_ID")
            if not self.okta_client_secret:
                missing.append("OKTA_CLIENT_SECRET")
        if self.aad_tenant_id or self.aad_client_id or self.aad_client_secret:
            if not self.aad_tenant_id:
                missing.append("ENTRAID_TENANT_ID")
            if not self.aad_client_id:
                missing.append("ENTRAID_CLIENT_ID")
            if not self.aad_client_secret:
                missing.append("ENTRAID_CLIENT_SECRET")

        if not (self.aad_client_id or self.okta_client_id):
            missing.append("at least one IdP (Entra or Okta) must be configured")

        if missing:
            raise RuntimeError(
                "Required environment variables are missing or empty: "
                + ", ".join(missing)
                + ". Fill these in .env before starting the agent server."
            )

    # ── Helpers ───────────────────────────────────────────────────────────

    @property
    def enforce_enabled(self) -> bool:
        return True

    @property
    def enforcement_mode(self) -> str:
        return "mcp"

    @property
    def okta_issuer(self) -> str:
        domain = self.okta_domain.rstrip("/")
        if not domain.startswith("https://"):
            domain = f"https://{domain}"
        return f"{domain}/oauth2/{self.okta_auth_server_id}"

    def credentials_for_issuer(self, issuer: str) -> tuple[str, str]:
        """Return ``(client_id, client_secret)`` matching *issuer*.

        Phase 2 discovers the AS from PRM. That AS may be Entra or Okta
        regardless of which IdP was used for Phase 1.
        """
        issuer_lower = (issuer or "").lower()
        if self.okta_domain and self.okta_domain.lower().rstrip("/") in issuer_lower:
            return self.okta_client_id, self.okta_client_secret
        if "login.microsoftonline.com" in issuer_lower:
            return self.aad_client_id, self.aad_client_secret
        if self.auth_idp == "okta":
            return self.okta_client_id, self.okta_client_secret
        return self.aad_client_id, self.aad_client_secret


def load_config() -> UIConfig:
    """Build a fresh UIConfig from the current process environment."""
    return UIConfig()
