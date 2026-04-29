"""Probe whether the TrustLogix MCP gateway needs X-TrustLogix-* headers
on initialize / tools/list to retrieve the security context.

Usage:
    python scripts/diag_headers.py <encrypted-cookie-value> [user_id] [user_role]

Pass the encrypted cookie value (from browser DevTools) as the first arg.
Optionally pass the user id and role you want the gateway to evaluate against.
"""
import json, hashlib, base64, sys
from pathlib import Path
import httpx
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
env = {}
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()

if len(sys.argv) < 2:
    print("usage: python scripts/diag_headers.py <encrypted-cookie-value> [user_id] [user_role]")
    sys.exit(2)

cookie = sys.argv[1]
test_user = sys.argv[2] if len(sys.argv) > 2 else "user@example.com"
test_role = sys.argv[3] if len(sys.argv) > 3 else env.get("TLX_GW_SERVICE_ROLE", "POC_TIER1_ROLE")

key = base64.urlsafe_b64encode(hashlib.sha256(env["SESSION_SECRET"].encode()).digest())
token = Fernet(key).decrypt(cookie.encode()).decode()
url = env["TLX_MCP_URL"]


def probe(label, extra_headers=None, client_info=None):
    print(f"\n========== {label} ==========")
    H = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if extra_headers:
        H.update(extra_headers)
    ci = client_info or {"name": "tlx-agent", "version": "1.0.0"}
    with httpx.Client(timeout=30) as c:
        r = c.post(url, headers=H, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": ci},
        })
        sid = r.headers.get("mcp-session-id", "")
        print(f"init -> {r.status_code} sid={sid[:16]}... clientInfo.name={ci.get('name')}")
        H2 = dict(H)
        H2["Mcp-Session-Id"] = sid
        c.post(url, headers=H2, json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })
        r = c.post(url, headers=H2, json={
            "jsonrpc": "2.0", "id": 5, "method": "prompts/get",
            "params": {"name": "retrieve_security_context", "arguments": {}},
        })
        for line in r.text.splitlines():
            if line.startswith("data:"):
                d = json.loads(line[5:])
                msgs = d.get("result", {}).get("messages", [])
                if msgs:
                    txt = msgs[0].get("content", {}).get("text", "")
                    print(f"  security_context: {txt[:300]}")

        r = c.post(url, headers=H2, json={
            "jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {},
        })
        for line in r.text.splitlines():
            if line.startswith("data:"):
                d = json.loads(line[5:])
                tools = d.get("result", {}).get("tools", [])
                err = d.get("error")
                if err:
                    print(f"  tools/list ERROR: {err}")
                else:
                    print(f"  tools/list: count={len(tools)} names={[t.get('name') for t in tools[:5]]}")


# Baseline (what the agent currently sends)
probe("BASELINE (no TrustLogix headers, clientInfo=tlx-agent)")

# With TrustLogix headers
probe("WITH X-TrustLogix-* headers", extra_headers={
    "X-TrustLogix-User-Id": test_user,
    "X-TrustLogix-User-Role": test_role,
    "X-TrustLogix-Agent-Name": "DataAgent",
})

# With different clientInfo names
for name in ("DataAgent", "tlx-poc-agent", "mcp-client", "agent"):
    probe(f"clientInfo.name={name}", client_info={"name": name, "version": "1.0.0"})

# With both
probe("WITH headers AND clientInfo.name=DataAgent", extra_headers={
    "X-TrustLogix-User-Id": test_user,
    "X-TrustLogix-User-Role": test_role,
    "X-TrustLogix-Agent-Name": "DataAgent",
}, client_info={"name": "DataAgent", "version": "1.0.0"})
