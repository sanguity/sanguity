"""nginx for the released image (the "web" service, and the all-in-one container's), under the URL base (#1159).

The site is the repo's nginx.conf (in the image at /etc/sanguity/nginx.d/default.conf), written to /tmp/nginx with the
URL base the app runs with, read from the settings file (DATA/server-settings.json, mounted read-only here): the
pending values while the app is trying them, else the good ones, as server_settings.start() decides. It's read again
every few seconds, and nginx reloads (SIGHUP) when the base changes: the app's Restart now restarts the app's container,
not this one, and a start that falls back puts the base back. Like Sonarr and Radarr, the outer proxy forwards the base
as it is: <base>/… is served as /… (rewritten inside nginx; the app routes either), the base alone and / redirect to
<base>/, unless a proxy in front already took the base off (it sent X-Forwarded-For or X-Forwarded-Prefix): then /
is the app's root, as before.

  python3 -I /app/docker/web.py [--backend http://127.0.0.1:8000]   (the all-in-one container's app is on loopback)

site(text, base) is the rewriting alone, for deploy/check.py's test. Only the standard library."""
import json, os, re, signal, subprocess, sys, time

SITE = "/etc/sanguity/nginx.d/default.conf"
MAIN = "/etc/nginx/nginx.conf"
OUT = "/tmp/nginx"
DATA = os.environ.get("SANGUITY_DATA", "/data")
POLL = 3
BASE = re.compile(r"(/[A-Za-z0-9._~-]+)+")   # server_settings.url_base's, checked again: it's written into nginx's config
ROOT = "    location = / { try_files /index.html =404; }"
ANCHOR = "    server_tokens off;\n"


def url_base(doc):
    """The URL base the app runs with: the pending values' while it tries them ("trying"), else the good ones'; "" for
    none, or for anything that isn't a base (never written into the config)."""
    values = doc.get("pending") if doc.get("trying") and isinstance(doc.get("pending"), dict) else doc.get("good")
    v = values.get("URL_BASE", "") if isinstance(values, dict) else ""
    v = "/" + v.strip("/") if isinstance(v, str) and v.strip("/") else ""
    if (not v or not BASE.fullmatch(v) or any(p in (".", "..") for p in v.split("/")[1:])
            or v.split("/")[1].lower() in ("api", "js", "css", "fonts", "icons", "connect")):   # (as url_base refuses them)
        return ""
    return v


def read_base(path=None):
    try:
        with open(path or os.path.join(DATA, "server-settings.json"), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return ""
    return url_base(doc) if isinstance(doc, dict) else ""


def site(text, base, backend=None):
    """The site's configuration under base ("" leaves it as it is), with the backend in place of the app service."""
    if backend:
        if "http://app:8000" not in text:
            raise ValueError("nginx.conf no longer proxies to http://app:8000; update docker/web.py to match")
        text = text.replace("http://app:8000", backend)
    if not base:
        return text
    if not BASE.fullmatch(base) or ANCHOR not in text or ROOT not in text or "\nserver {" not in text:
        raise ValueError("nginx.conf isn't as docker/web.py expects it (server_tokens, the root location): update both")
    rx = base.replace(".", r"\.")
    maps = (f"# (#1159) under the URL base {base}, written by docker/web.py from the settings file\n"
            f"map $request_uri $sg_based {{ ~^{rx}(/|\\?|$) 1; default 0; }}\n"
            "map \"$http_x_forwarded_prefix$http_x_forwarded_for\" $sg_proxied { \"\" 0; default 1; }\n"
            "map \"$sg_based$sg_proxied\" $sg_to_base { \"00\" 1; default 0; }\n")
    text = text.replace("\nserver {", "\n" + maps + "\nserver {", 1)
    text = text.replace(ANCHOR, ANCHOR + f"    rewrite ^{rx}$ {base}/ redirect;   # the base alone: to <base>/\n"
                        f"    rewrite ^{rx}(/.*)$ $1 break;   # <base>/… served as /… (the app is passed the path without it)\n", 1)
    return text.replace(ROOT, f"    location = / {{ if ($sg_to_base) {{ return 302 {base}/; }} try_files /index.html =404; }}", 1)


def write(base, backend):
    os.makedirs(OUT, exist_ok=True)
    with open(SITE, encoding="utf-8") as f:
        text = site(f.read(), base, backend)
    with open(os.path.join(OUT, "default.conf"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(MAIN, encoding="utf-8") as f:
        main = f.read()
    with open(os.path.join(OUT, "nginx.conf"), "w", encoding="utf-8") as f:
        f.write(main.replace("/etc/sanguity/nginx.d/*.conf", os.path.join(OUT, "default.conf")))


def main():
    backend = sys.argv[sys.argv.index("--backend") + 1] if "--backend" in sys.argv else None
    base = read_base()
    write(base, backend)
    print(f"[web] serving at {base or '/'}", flush=True)
    nginx = subprocess.Popen(["nginx", "-e", "stderr", "-c", os.path.join(OUT, "nginx.conf"), "-g", "daemon off;"])
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: nginx.send_signal(signal.SIGQUIT if s == signal.SIGTERM else s))
    while nginx.poll() is None:
        time.sleep(POLL)
        now = read_base()
        if now != base and nginx.poll() is None:
            write(now, backend)
            base = now
            print(f"[web] the URL base is now {base or '/'}: reloading", flush=True)
            nginx.send_signal(signal.SIGHUP)
    sys.exit(nginx.returncode)


if __name__ == "__main__":
    main()
