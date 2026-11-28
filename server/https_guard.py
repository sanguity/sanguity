"""Built-in HTTPS's listener (#1159; its security review): what nginx does in front of the app everywhere else, for the
port clients reach directly.

  https_app(app)   the app as python -m server serves it on HTTPS_PORT: no forwarded header believed (a client could
                   send any: the peer is the address, and the scheme is the listener's own https), each request's peer
                   kept (server_settings.PeerKept), and Limits
  Limits           nginx.conf's caps per location: the methods a location takes (405) and the most a request may send
                   (413: said by its Content-Length before anything is read, else as the body arrives). Under the URL
                   base as without it. The pages and their files take GET and HEAD only, nothing sent.

tests/test_server_settings.py keeps it to nginx.conf's numbers."""
import re

from . import config, server_settings

KB, MB, GB = 1024, 1024 ** 2, 1024 ** 3
GET = ("GET", "HEAD")
# (path pattern, the methods it takes (None: any), the most a request sends), the first that matches; as nginx.conf
RULES = [
    (re.compile(r"^/api/profiles/\d+/calendar\.ics$"), GET, 1 * KB),
    (re.compile(r"^/api/share/view$"), ("POST",), 1 * KB),
    (re.compile(r"^/api/profiles/\d+/uploads/\d+/files$"), None, 4 * GB),
    (re.compile(r"^/api/profiles/\d+/(journal/photos|insurance/\d+/(front|back))$"), None, 25 * MB),
    (re.compile(r"^/api/settings/install/ocr/languages/[a-z_]{3,16}\.traineddata$"), ("PUT",), 65 * MB),
    (re.compile(r"^/api/"), None, 64 * KB),
    (re.compile(r"^/connect/return$"), GET, 0),
    (re.compile(r""), GET, 0),   # the pages, styles, scripts, fonts and icons
]


def rule(path):
    base = config.url_base()
    if base and (path == base or path.startswith(base + "/")):
        path = path[len(base):] or "/"
    return next((methods, most) for rx, methods, most in RULES if rx.match(path))


async def _answer(send, status, text):
    body = text.encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
    await send({"type": "http.response.body", "body": body})


class Limits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        methods, most = rule(scope.get("path", "/"))
        if methods and scope["method"] not in methods:
            return await _answer(send, 405, "That method isn't allowed here.")
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > most):
            return await _answer(send, 413, "That's more than this address takes.")
        seen, started, over = 0, False, False

        async def limited():   # a body sent without its length: refused once it passes the most, the app told it's gone
            nonlocal seen, over
            if over:
                return {"type": "http.disconnect"}
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > most:
                    over = True
                    if not started:
                        await _answer(send, 413, "That's more than this address takes.")
                    return {"type": "http.disconnect"}
            return msg

        async def watched(msg):
            nonlocal started
            if over:   # the 413 is the answer: nothing the app says after it is sent
                return
            started = started or msg["type"] == "http.response.start"
            await send(msg)
        try:
            await self.app(scope, limited, watched)
        except Exception:
            if not over:
                raise


def https_app(app):
    return server_settings.PeerKept(Limits(app))
