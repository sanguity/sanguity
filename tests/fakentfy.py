"""A fake ntfy server for the notification tests (#1130): every POST to it recorded (path, headers, body), answered
with `status`. Standard library only; it runs in a thread on a free 127.0.0.1 port.

    ntfy = FakeNtfy()          # ntfy.url: a topic's address on it
    ... ntfy.posts ...
    ntfy.stop()
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeNtfy:
    def __init__(self, topic="sanguity-fictional"):
        self.posts, self.status = [], 200
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                fake.posts.append({"path": self.path, "headers": dict(self.headers), "body": self.rfile.read(n).decode()})
                self.send_response(fake.status)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        self.url = f"http://{self.host}/{topic}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
