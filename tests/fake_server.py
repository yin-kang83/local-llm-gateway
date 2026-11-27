"""Threaded fake OpenAI-compatible server used by the offline test-suite.

No network access is needed: the server binds ``127.0.0.1`` on an ephemeral
port.  Tests can queue per-request behaviours (status / body / delay) to
exercise retry and failure paths deterministically.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _ThreadingHTTPServer(ThreadingHTTPServer):
    """Quiet, fast-shutdown test server (no traceback spam from hung clients)."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # noqa: D102 - silence
        pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _handle(self):
        server = self.server
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        payload = None
        if raw:
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                payload = raw.decode("utf-8", errors="replace")
        server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
                "json": payload,
            }
        )

        behaviour = server.behaviours.pop(0) if server.behaviours else None
        if behaviour is None:
            behaviour = server.default_behaviour
        delay = behaviour.get("delay", 0) or 0
        if delay:
            time.sleep(delay)
        status = behaviour.get("status", 200)
        body = behaviour.get("body")
        if body is None:
            body = server.default_body_for(self.path)
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def finish(self):  # pragma: no cover - keep-alive plumbing
        try:
            super().finish()
        except Exception:
            pass


class FakeServer:
    """Small controllable OpenAI-compatible HTTP server."""

    def __init__(
        self,
        completion_text: str = '{"ok": true}',
        model: str = "fake-model",
        models: list[str] | None = None,
        usage: dict | None = None,
        finish_reason: str = "stop",
    ) -> None:
        self.completion_text = completion_text
        self.model = model
        self.models = list(models) if models is not None else [model]
        self.usage = usage if usage is not None else {"prompt_tokens": 7, "completion_tokens": 3}
        self.finish_reason = finish_reason
        self.requests: list[dict] = []
        self.behaviours: list[dict] = []
        self.default_behaviour: dict = {}

        self._httpd = _ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.requests = self.requests
        self._httpd.behaviours = self.behaviours
        self._httpd.default_behaviour = {}
        self._httpd.default_body_for = self._default_body_for
        self._thread = threading.Thread(
            target=lambda: self._httpd.serve_forever(poll_interval=0.01), daemon=True
        )

    # ------------------------------------------------------------------ API

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}/v1"

    def queue(self, *, status: int = 200, body=None, delay: float = 0) -> "FakeServer":
        self.behaviours.append({"status": status, "body": body, "delay": delay})
        return self

    def __enter__(self) -> "FakeServer":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    # -------------------------------------------------------------- internals

    def _default_body_for(self, path: str):
        if path.endswith("/models"):
            return {"object": "list", "data": [{"id": model_id} for model_id in self.models]}
        if path.endswith("/chat/completions"):
            return {
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "model": self.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.completion_text},
                        "finish_reason": self.finish_reason,
                    }
                ],
                "usage": self.usage,
            }
        return {"error": "unsupported path"}
