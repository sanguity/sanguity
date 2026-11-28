"""Run the API:  python -m server   (HOST and PORT from the environment; defaults 0.0.0.0:8000).

First the settings that need a restart (#1121, server_settings.py): the values changed on the page are tried, and kept
once the server has run healthy with them, else the next start falls back to the last good ones. That's decided before
anything reads a setting (security.py reads HASH_PARALLEL as it's imported), so the app is imported by uvicorn after."""
import uvicorn

from . import server_settings

if __name__ == "__main__":
    server_settings.start()
    opts = server_settings.uvicorn_options()
    server_settings.watch(opts["host"], opts["port"])
    uvicorn.run("server.main:app", host=opts["host"], port=opts["port"], proxy_headers=True,
                # whose X-Forwarded-For/-Proto to believe (FORWARDED_ALLOW_IPS): only a proxy on this machine unless told
                # otherwise (every template sets "*", since there only nginx can reach the app and it overwrites the header)
                forwarded_allow_ips=opts["forwarded_allow_ips"],
                server_header=False, access_log=False)
