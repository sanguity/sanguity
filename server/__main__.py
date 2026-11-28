"""Run the API:  python -m server   (HOST and PORT from the environment; defaults 0.0.0.0:8000).

First the settings that need a restart (#1121, server_settings.py): the values changed on the page are tried, and kept
once the server has run healthy with them, else the next start falls back to the last good ones. That's decided before
anything reads a setting (security.py reads HASH_PARALLEL as it's imported), so the app is imported after.

(#1159) With built-in HTTPS set (a certificate and key in the data folder), the same app is also served over HTTPS on
HTTPS_PORT, pages included, by a second uvicorn server in a thread of its own (the first runs the app's start-up and
handles the signals); healthy then means both are serving, and, with an outbound proxy set, that it answers."""
import threading

import uvicorn

from . import server_settings

if __name__ == "__main__":
    server_settings.start()
    opts = server_settings.uvicorn_options()
    from . import main, net
    main.set_log_level()
    # whose X-Forwarded-For/-Proto to believe (FORWARDED_ALLOW_IPS: the environment's as it's set, else the page's): only
    # a proxy on this machine unless told otherwise (every template sets "*": there only nginx can reach the app, and it
    # overwrites the header). uvicorn's own middleware, as proxy_headers=True adds it, with each request's peer kept
    served = server_settings.asgi_app(main.app, opts["forwarded_allow_ips"])
    server = uvicorn.Server(uvicorn.Config(served, host=opts["host"], port=opts["port"], proxy_headers=False,
                                           server_header=False, access_log=False))
    https = None
    if (files := server_settings.https_files()):
        # (#1159's review) reached by clients directly: no forwarded header believed, and nginx.conf's limits
        from . import https_guard
        https = uvicorn.Server(uvicorn.Config(https_guard.https_app(main.app), host="0.0.0.0", port=server_settings.value("HTTPS_PORT"),
                                              ssl_certfile=files[0], ssl_keyfile=files[1], proxy_headers=False,
                                              server_header=False, access_log=False, lifespan="off"))
        threading.Thread(target=https.run, name="https", daemon=True).start()
    proxy_seen = []   # the outbound proxy answered once, as these settings are tried (net.proxy_answers)

    def healthy():
        if not (server.started and not server.should_exit) or (https is not None and not https.started):
            return False
        if server_settings.value("OUTBOUND_PROXY") and not proxy_seen:
            if not net.proxy_answers():
                return False
            proxy_seen.append(True)
        return True
    # healthy, for new values to be kept: listening with the app's start-up done, and not stopping (asked in-process)
    server_settings.watch(healthy)
    server.run()
