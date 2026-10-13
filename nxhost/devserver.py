"""开发用：把工具页面放到本机浏览器里跑，接口走 HTTP 转给同一个 Api 对象。

只在源码方式下可用，只监听 127.0.0.1。这样可以用浏览器的开发者工具调页面。
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SHIM = """<script>
window.pywebview = { api: new Proxy({}, { get: (_, name) => (...args) =>
  fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) }).then(r => r.json()) }) };
window.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));
</script>"""


def serve(api, html: str, port: int) -> None:
    page = html.replace("<head>", "<head>" + SHIM, 1).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            self._send(page, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            name = self.path.rsplit("/", 1)[-1]
            method = getattr(api, name, None)
            if name.startswith("_") or not callable(method):
                self._send(b'{"error": "no such method"}', "application/json", 404)
                return
            args = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"[]")
            try:
                result = method(*args)
            except Exception as exc:
                result = {"error": f"{type(exc).__name__}: {exc}"}
            self._send(json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json")

    print(f"http://127.0.0.1:{port}/")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
