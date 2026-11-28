"""Run the API:  python -m server   (HOST and PORT from the environment; defaults 0.0.0.0:8000).

First the settings that need a restart (#1121, server_settings.py): the values changed on the page are tried, and kept
once the server has run healthy with them, else the next start falls back to the last good ones. That's decided before
anything reads a setting (security.py reads HASH_PARALLEL as it's imported), so the app is imported after."""
import uvicorn

from . import server_settings

if __name__ == "__main__":
    server_settings.start()
    opts = server_settings.uvicorn_options()
    from . import main
    # whose X-Forwarded-For/-Proto to believe (FORWARDED_ALLOW_IPS: the environment's as it's set, else the page's): only
    # a proxy on this machine unless told otherwise (every template sets "*": there only nginx can reach the app, and it
    # overwrites the header). uvicorn's own middleware, as proxy_headers=True adds it, with each request's peer kept
    server = uvicorn.Server(uvicorn.Config(server_settings.asgi_app(main.app, opts["forwarded_allow_ips"]), host=opts["host"],
                                           port=opts["port"], proxy_headers=False, server_header=False, access_log=False))
    # healthy, for new values to be kept: listening with the app's start-up done, and not stopping (asked in-process)
    server_settings.watch(lambda: server.started and not server.should_exit)
    server.run()
