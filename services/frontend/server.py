"""Lightweight static file server + auth/API proxy for the frontend.

Two proxy modes:

* ``/api/*``  — JSON request/response. Uses ``urllib`` (auto-follows
  redirects, which is fine for JSON endpoints).
* ``/auth/*`` — OAuth flow. Uses ``http.client`` directly so we can opt OUT
  of redirect following — the browser must see the 302/303 from Entra (or
  TLX) to drive the next step. Forwards ``Location`` and ``Set-Cookie``
  headers verbatim so cookies land on the frontend origin.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

PORT = 5000
STATIC_DIR = Path(__file__).parent / "static"
AGENT_SERVER = os.getenv("AGENT_SERVER_URL", "http://agent-server:5100")


class FrontendHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
            return

        if self.path == "/config.js":
            self._serve_config_js()
            return

        # Auth flow — must NOT follow redirects.
        if self.path.startswith("/auth/"):
            self._proxy_raw("GET")
            return

        # JSON API — auto-follow redirects is fine.
        if self.path.startswith("/api/"):
            self._proxy_get()
            return

        # SPA fallback: serve index.html for non-file paths
        file_path = STATIC_DIR / self.path.lstrip("/").split("?", 1)[0]
        if not file_path.exists() or file_path.is_dir():
            self.path = "/index.html"

        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/auth/"):
            self._proxy_raw("POST")
            return
        if self.path.startswith("/api/"):
            self._proxy_post()
            return
        self._json_response(404, {"error": "Not found"})

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy_delete()
            return
        self._json_response(404, {"error": "Not found"})

    # ── Static config endpoint ────────────────────────────────────────────

    def _serve_config_js(self):
        agent_server = os.getenv("AGENT_SERVER_EXTERNAL", "http://localhost:5100")
        customer_label = os.getenv("CUSTOMER_LABEL", "")
        auth_idp = os.getenv("AUTH_IDP", "entra").lower()
        js = f"""window.__CONFIG__ = {{
    API_BASE: "",
    AGENT_API: "{agent_server}",
    CUSTOMER_LABEL: "{customer_label}",
    AUTH_IDP: "{auth_idp}",
}};"""
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript")
        self.end_headers()
        self.wfile.write(js.encode())

    # ── /auth/* — raw proxy with NO redirect following ────────────────────

    def _proxy_raw(self, method: str):
        """Proxy ``self.path`` to the agent server *without* following
        redirects. ``Location`` and ``Set-Cookie`` headers are forwarded so
        the browser drives the OAuth flow and cookies land on the frontend
        origin.
        """
        parsed = urllib.parse.urlsplit(AGENT_SERVER)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        scheme = parsed.scheme

        # Read request body if present.
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else None

        # Build forwarded headers.
        fwd_headers = {}
        for header in ("Cookie", "X-Demo-User", "Authorization", "Content-Type"):
            val = self.headers.get(header)
            if val:
                fwd_headers[header] = val
        fwd_headers["Host"] = f"{host}:{port}" if port not in (80, 443) else host

        # Open the connection.
        conn_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
        try:
            conn = conn_cls(host, port, timeout=30)
            conn.request(method, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
        except Exception as exc:
            self._json_response(502, {"error": f"Upstream connection failed: {exc}"})
            return

        try:
            self.send_response(resp.status)

            # Forward all relevant headers — Location for redirects,
            # Set-Cookie for the encrypted token cookies, Content-Type for
            # the body. There can be multiple Set-Cookie headers; iterate
            # using getheaders() not get_header() to capture all of them.
            seen = set()
            for raw_header, value in resp.getheaders():
                lower = raw_header.lower()
                if lower in ("transfer-encoding", "connection", "content-length"):
                    continue
                self.send_header(raw_header, value)
                seen.add(lower)

            # If the upstream sent a body, we need Content-Length for HTTP/1.0.
            body_data = resp.read() or b""
            if "content-length" not in seen and body_data:
                self.send_header("Content-Length", str(len(body_data)))
            self.end_headers()
            if body_data:
                self.wfile.write(body_data)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # ── /api/* — JSON proxy (auto-follow redirects is fine) ───────────────

    def _proxy_get(self):
        url = f"{AGENT_SERVER}{self.path}"
        try:
            req = urllib.request.Request(url)
            self._copy_headers(req)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                self.send_response(resp.status)
                for h in ("Content-Type", "Set-Cookie"):
                    val = resp.headers.get(h)
                    if val:
                        self.send_header(h, val)
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self._json_response(e.code, {"error": e.reason})
        except Exception as e:
            self._json_response(502, {"error": str(e)})

    def _proxy_post(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""
        url = f"{AGENT_SERVER}{self.path}"

        # Special handling for SSE chat endpoint
        if self.path == "/api/chat":
            self._proxy_sse(url, body)
            return

        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            self._copy_headers(req)
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                for h in ("Content-Type", "Set-Cookie"):
                    val = resp.headers.get(h)
                    if val:
                        self.send_header(h, val)
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as e:
            self._json_response(e.code, {"error": e.reason})
        except Exception as e:
            self._json_response(502, {"error": str(e)})

    def _proxy_sse(self, url: str, body: bytes):
        """Stream SSE from agent-server to client."""
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            self._copy_headers(req)
            with urllib.request.urlopen(req, timeout=120) as resp:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                for line in resp:
                    self.wfile.write(line)
                    self.wfile.flush()
        except urllib.error.HTTPError as e:
            self._json_response(e.code, {"error": e.reason})
        except Exception as e:
            self._json_response(502, {"error": str(e)})

    def _proxy_delete(self):
        url = f"{AGENT_SERVER}{self.path}"
        try:
            req = urllib.request.Request(url, method="DELETE")
            self._copy_headers(req)
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as e:
            self._json_response(e.code, {"error": e.reason})
        except Exception as e:
            self._json_response(502, {"error": str(e)})

    def _copy_headers(self, req: urllib.request.Request):
        """Forward relevant headers to backend."""
        for header in ("Cookie", "X-Demo-User", "Authorization"):
            val = self.headers.get(header)
            if val:
                req.add_header(header, val)

    def _json_response(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass  # Suppress default logging


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), FrontendHandler)
    print(f"Frontend server running on http://0.0.0.0:{PORT}")
    server.serve_forever()
