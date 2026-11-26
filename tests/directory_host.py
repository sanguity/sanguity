"""A fake of the two hosts the provider directory's lists come from (#992): open.epic.com's User-access Brands bundle
and Oracle Health's list on raw.githubusercontent.com, served on this machine from the fictional samples in
tests/fixtures/directory, over plain HTTP. Used by test_directory, test_outside and the browser check.

  host = DirectoryHost().start()
  host.use()                 server/directory/build.py's SOURCES point here until host.stop()
  host.mode["epic-brands"] = "ok" | "cut" (cut short) | "error" (503) | "junk" (not the list) | "slow" (waits first)
  host.hits                  the paths asked for
"""
import http.server, os, threading, time

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "directory")
FILES = {"epic-brands": "epic_brands_sample.json", "oracle-millennium": "oracle_millennium_sample.json"}


class DirectoryHost:
    def __init__(self):
        self.mode = {k: "ok" for k in FILES}
        self.hits, self.slow = [], threading.Event()
        host = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                key = self.path.strip("/")
                host.hits.append(self.path)
                if key not in FILES:
                    self.send_error(404)
                    return
                with open(os.path.join(FIX, FILES[key]), "rb") as f:
                    body = f.read()
                mode = host.mode[key]
                if mode == "error":
                    self.send_error(503, "Service Unavailable")
                    return
                if mode == "junk":
                    body = b"<html>Down for maintenance</html>"
                if mode == "slow":   # until the test lets it go
                    host.slow.wait(20)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body) * (2 if mode == "cut" else 1)))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                if mode == "cut":
                    self.close_connection = True

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._was = None

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def use(self):
        from server.directory import build
        self._was = {k: build.SOURCES[k]["url"] for k in FILES}
        for k in FILES:
            build.SOURCES[k]["url"] = f"{self.base}/{k}"
        return self

    def stop(self):
        if self._was:
            from server.directory import build
            for k, u in self._was.items():
                build.SOURCES[k]["url"] = u
        self.slow.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        time.sleep(0)
