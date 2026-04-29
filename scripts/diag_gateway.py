"""One-off diagnostic for the TrustLogix MCP gateway.

Probes initialize / tools/list / prompts/list / resources/list and dumps
every raw SSE frame so we can see exactly what TLX 3.0.0b1 is sending.

Does NOT modify any production code path. Run standalone.

Usage (PowerShell or bash):

    # Easiest: paste the encrypted cookie value (from browser DevTools).
    # The script decrypts it using SESSION_SECRET from .env.
    python scripts/diag_gateway.py --cookie "<value of tlx_srv_l-BzVrAsRIghEpa0 cookie>"

    # Or, if you already have the decrypted bearer token:
    python scripts/diag_gateway.py --token "<raw access token>"

    # Override the protocol version (default tries both 2025-03-26 and 2025-11-25):
    python scripts/diag_gateway.py --cookie "..." --protocol 2025-06-18

    # Override the gateway URL (defaults to TLX_MCP_URL from .env):
    python scripts/diag_gateway.py --cookie "..." --url https://...
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    print("ERROR: cryptography package is required (pip install cryptography)", file=sys.stderr)
    sys.exit(2)


# ── Locate and parse .env ──────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def load_env_var(name: str) -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return os.getenv(name, "")


def fernet_for(secret: str) -> Fernet:
    key_material = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key_material))


# ── Protocol probe ────────────────────────────────────────────────────────

def dump_sse(label: str, body: str) -> list[dict]:
    """Print every SSE event/data line and return parsed JSON-RPC payloads."""
    print(f"\n--- {label}: raw response body ({len(body)} chars) ---")
    if not body:
        print("(empty body)")
        return []
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        print(f"  [{i:>3}] {ln}")
    parsed: list[dict] = []
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("data:"):
            payload = ln[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed.append(json.loads(payload))
            except json.JSONDecodeError as e:
                print(f"  (couldn't parse data line as JSON: {e})")
    return parsed


def post(url: str, headers: dict, payload: dict, timeout: int = 30) -> tuple[httpx.Response, str]:
    started = time.time()
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=headers, json=payload)
    elapsed = time.time() - started
    print(f"\n  POST {payload.get('method')} -> HTTP {resp.status_code} in {elapsed:.2f}s")
    print(f"  Content-Type: {resp.headers.get('content-type', '')}")
    print(f"  Mcp-Session-Id (response): {resp.headers.get('mcp-session-id', '')}")
    return resp, resp.text


def run_probe(url: str, token: str, protocol_version: str) -> None:
    print("=" * 78)
    print(f"PROBE  url={url}  protocol={protocol_version}")
    print("=" * 78)

    base_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    # 1) initialize
    init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "tlx-diag", "version": "0.1"},
        },
    }
    resp, body = post(url, base_headers, init_payload)
    parsed = dump_sse("initialize", body)
    session_id = resp.headers.get("mcp-session-id", "") or resp.headers.get("Mcp-Session-Id", "")
    if not session_id and parsed:
        session_id = parsed[0].get("result", {}).get("sessionId", "")
    print(f"\n  Negotiated session: {session_id or '(none)'}")
    if parsed:
        negotiated = parsed[0].get("result", {}).get("protocolVersion", "?")
        server_info = parsed[0].get("result", {}).get("serverInfo", {})
        capabilities = parsed[0].get("result", {}).get("capabilities", {})
        print(f"  Negotiated protocolVersion: {negotiated}")
        print(f"  Server: {server_info}")
        print(f"  Capabilities: {capabilities}")

    if not session_id:
        print("\n  No session id — aborting follow-up calls.")
        return

    # The MCP-Protocol-Version header was made mandatory by the 2025-06-18
    # spec for follow-up requests. Send it both ways and see if behaviour
    # differs.
    auth_headers = dict(base_headers)
    auth_headers["Mcp-Session-Id"] = session_id

    auth_headers_with_proto = dict(auth_headers)
    auth_headers_with_proto["MCP-Protocol-Version"] = protocol_version

    # 2) Per-spec "notifications/initialized" — some servers gate tool
    #    visibility on receiving this. Some don't. Send it (no response expected).
    init_done = {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
        "params": {},
    }
    try:
        with httpx.Client(timeout=10) as client:
            resp_n = client.post(url, headers=auth_headers, json=init_done)
        print(f"\n  notifications/initialized -> HTTP {resp_n.status_code} (body {len(resp_n.text)} chars)")
        if resp_n.text:
            for ln in resp_n.text.splitlines()[:10]:
                print(f"    {ln}")
    except Exception as exc:
        print(f"  notifications/initialized failed: {exc}")

    # 3) tools/list — once without protocol header, once with
    for header_label, h in (
        ("WITHOUT MCP-Protocol-Version header", auth_headers),
        ("WITH MCP-Protocol-Version header", auth_headers_with_proto),
    ):
        print(f"\n>>> tools/list {header_label}")
        list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        try:
            resp_l, body_l = post(url, h, list_payload, timeout=45)
        except httpx.TimeoutException:
            print("  TIMED OUT after 45s — gateway may be holding the SSE connection open")
            continue
        parsed_l = dump_sse("tools/list", body_l)
        for p in parsed_l:
            r = p.get("result", {})
            print(f"\n  ── parsed result ──")
            print(f"  result keys:    {list(r.keys()) if isinstance(r, dict) else type(r).__name__}")
            if isinstance(r, dict):
                print(f"  result.tools:   type={type(r.get('tools')).__name__} count={len(r.get('tools') or [])}")
                if r.get("tools"):
                    for t in r["tools"][:5]:
                        print(f"    - {t.get('name')}: {t.get('description', '')[:80]}")
            if "error" in p:
                print(f"  ERROR: {p['error']}")

    # 4) For comparison, prompts/list and resources/list
    for method in ("prompts/list", "resources/list"):
        print(f"\n>>> {method}")
        payload = {"jsonrpc": "2.0", "id": 3, "method": method, "params": {}}
        try:
            resp_x, body_x = post(url, auth_headers, payload, timeout=20)
            dump_sse(method, body_x)
        except httpx.TimeoutException:
            print(f"  {method} timed out")
        except Exception as exc:
            print(f"  {method} failed: {exc}")


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", help="Encrypted tlx_srv_* cookie value (will be decrypted)")
    parser.add_argument("--token", help="Already-decrypted bearer token")
    parser.add_argument("--url", help="Gateway URL (default: TLX_MCP_URL from .env)")
    parser.add_argument(
        "--protocol",
        action="append",
        help="Protocol version(s) to try (repeatable). Default: 2025-03-26 and 2025-11-25.",
    )
    args = parser.parse_args()

    url = args.url or load_env_var("TLX_MCP_URL")
    if not url:
        print("ERROR: gateway URL missing — pass --url or set TLX_MCP_URL in .env", file=sys.stderr)
        return 2

    token = args.token or ""
    if not token and args.cookie:
        secret = load_env_var("SESSION_SECRET")
        if not secret:
            print("ERROR: SESSION_SECRET missing from .env", file=sys.stderr)
            return 2
        try:
            token = fernet_for(secret).decrypt(args.cookie.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            print("ERROR: cookie failed to decrypt — wrong SESSION_SECRET or wrong cookie value", file=sys.stderr)
            return 2

    if not token:
        print("ERROR: provide --cookie or --token", file=sys.stderr)
        return 2

    print(f"Token: {len(token)} chars, prefix={token[:24]}...")

    protocols = args.protocol or ["2025-03-26", "2025-11-25"]
    for p in protocols:
        run_probe(url, token, p)

    return 0


if __name__ == "__main__":
    sys.exit(main())
