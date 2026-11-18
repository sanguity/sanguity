#!/usr/bin/env python3
"""Smoke test of a running install, the way a new user meets it: through nginx, from first-run setup to a document.

  docker compose up -d --build
  python3 tests/docker_smoke.py --code "$(docker compose logs app | grep -o 'Setup code: [A-Z0-9-]*' | tail -1 | cut -d' ' -f3)"

Options: --base URL (default http://127.0.0.1:8730), --receiver URL (also check the watch receiver answers),
--code CODE (the setup code from the app's log; required while no account exists). Standard library only.
Exits non-zero, listing what failed, if anything did.
"""
import argparse, http.cookiejar, json, sys, urllib.error, urllib.request

USER, PW = "smoke", "a long smoke test passphrase"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8730")
    ap.add_argument("--receiver")
    ap.add_argument("--code", default="")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    passed, failed = [], []

    def check(name, ok, detail=""):
        (passed if ok else failed).append(f"{name}{': ' + str(detail) if detail != '' else ''}")
        return ok

    def req(path, body=None, method=None, signed_in=True):
        """(status, headers, bytes). Changes carry the X-Sanguity header and this site's Origin, as the pages do."""
        h = {}
        if body is not None or method not in (None, "GET"):
            h = {"X-Sanguity": "1", "Origin": base, "Content-Type": "application/json"}
        r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(), headers=h, method=method)
        try:
            with (opener if signed_in else urllib.request.build_opener()).open(r, timeout=30) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    try:
        # ---- pages and headers (nginx)
        st, h, body = req("/", signed_in=False)
        check("app page is served", st == 200 and b"<title>Sanguity" in body, st)
        check("pages send a content security policy", "default-src 'self'" in (h.get("Content-Security-Policy") or ""))
        check("pages can't be framed", h.get("X-Frame-Options") == "DENY")
        # install as a phone app (#56): the manifest and service worker, with their types and the pages' headers
        for path, kind in (("/manifest.webmanifest", "application/manifest+json"), ("/sw.js", "javascript"), ("/icons/icon-192.png", "image/png")):
            st, h, _ = req(path, signed_in=False)
            check(f"{path} is served as {kind}, not stored", st == 200 and kind in (h.get("Content-Type") or "")
                  and h.get("Cache-Control") == "no-store" and "default-src 'self'" in (h.get("Content-Security-Policy") or ""), (st, h.get("Content-Type")))
        for old in ("/data.json", "/settings.json", "/files/demo-report.pdf", "/records/", "/watch/daily.json"):
            check(f"{old} is not served", req(old, signed_in=False)[0] == 404)

        # ---- nothing without signing in
        check("records need a session", req("/api/profiles/1/data", signed_in=False)[0] == 401)

        # ---- first-run setup with the code from the log
        st, _, body = req("/api/setup")
        needed = st == 200 and json.loads(body).get("needed")
        if needed:
            check("setup code given", bool(a.code), "" if a.code else "pass --code from `docker compose logs app`")
            st, _, body = req("/api/setup", {"code": a.code, "username": USER, "password": PW, "profile_name": "Smoke test"})
            check("first account created with the setup code", st == 200, body[:200])
        else:
            st, _, body = req("/api/login", {"username": USER, "password": PW})
            check("signs in (already set up)", st == 200, body[:200])
        check("a cross-site change is refused", _cross_site(base) == 403)

        # ---- the account, its profile and the demo records
        st, _, body = req("/api/me")
        me = json.loads(body) if st == 200 else {}
        check("signed in as the new admin", st == 200 and me["user"]["username"] == USER and me["user"]["is_admin"], body[:200])
        profiles = me.get("profiles", [])
        check("owns the records already on the server", len(profiles) == 1 and profiles[0]["role"] == "owner", profiles)
        pid = profiles[0]["id"] if profiles else 1
        st, h, body = req(f"/api/profiles/{pid}/data")
        data = json.loads(body) if st == 200 else {}
        check("demo records load", st == 200 and len(data.get("events", [])) > 100, st)
        check("records aren't cached", "no-store" in (h.get("Cache-Control") or ""))
        # each record's details come from .../details when it's opened (#217), at most 500 at a time: through nginx too
        ids, details, det_ok = [e["id"] for e in data.get("events", [])], {}, True
        for i in range(0, len(ids), 500):
            st, _, body = req(f"/api/profiles/{pid}/details?ids=" + ",".join(str(x) for x in ids[i:i + 500]))
            det_ok = det_ok and st == 200
            details.update(json.loads(body).get("details", {}) if st == 200 else {})
        check("records' details load when asked for, through nginx (#217)", det_ok and len(details) > 0, len(details))
        docs = [d["url"] for d in details.values() if isinstance(d, dict) and d.get("type") == "file" and d.get("url")]
        check("document links point at the API", docs and all(u.startswith(f"api/profiles/{pid}/documents/") for u in docs), docs[:2])
        if docs:
            st, h, body = req("/" + docs[0].split("#")[0])
            check("a document opens", st == 200 and body[:5] == b"%PDF-", st)
            check("documents can't run scripts or fetch elsewhere", "script-src 'none'" in (h.get("Content-Security-Policy") or "")
                  and "default-src 'none'" in (h.get("Content-Security-Policy") or ""))
            check("a document isn't reachable signed out", req("/" + docs[0].split("#")[0], signed_in=False)[0] == 401)
        check("watch data is served for the profile", req(f"/api/profiles/{pid}/watch")[0] in (200, 204))
        # backups take the receiver's data from its folder, mounted read-only (#227); the first account is an admin,
        # and the only one, so it needs no second factor yet
        st, _, body = req("/api/backups")
        watch = (json.loads(body).get("watch") or {}) if st == 200 else {}
        check("backups can see the watch receiver's folder", watch.get("visible") is True, (st, watch))

        # ---- an upload through nginx: one file far above the 64 KB every other API request is held to
        check("the import page is served", req("/import.html", signed_in=False)[0] == 200)
        check("the getting-started guide is served (#199)", req("/guide.html", signed_in=False)[0] == 200)
        st, _, body = req(f"/api/profiles/{pid}/uploads", method="POST")
        check("an upload starts", st == 200, body[:200])
        if st == 200:
            up = json.loads(body)["id"]
            big = b"date,test,value,unit\n" + b"2026-01-05,Hemoglobin,13.9,g/dL\n" * 32000   # about 1 MB, fictional
            r = urllib.request.Request(f"{base}/api/profiles/{pid}/uploads/{up}/files?path=smoke%2Flabs.csv", data=big, method="PUT",
                                       headers={"X-Sanguity": "1", "Origin": base, "Content-Type": "application/octet-stream"})
            try:
                with opener.open(r, timeout=60) as resp:
                    st, body = resp.status, resp.read()
            except urllib.error.HTTPError as e:
                st, body = e.code, e.read()
            check("a 1 MB file uploads through nginx", st == 200 and json.loads(body)["bytes"] == len(big), body[:200])
            st, _, body = req(f"/api/profiles/{pid}/uploads/{up}/finish", method="POST")
            check("the upload finishes", st == 200 and json.loads(body)["files"] == 1, body[:200])
            check("the upload is discarded", req(f"/api/profiles/{pid}/uploads/{up}", method="DELETE")[0] == 200)
        check("other API requests keep the 64 KB cap", req("/api/me/password", {"current": "x" * 100_000, "new": "y"})[0] == 413)

        # ---- sign-out ends the session on the server
        check("signs out", req("/api/logout", method="POST")[0] == 200)
        check("signed out for real", req("/api/me")[0] == 401)
        check("a wrong password is refused", req("/api/login", {"username": USER, "password": "not the password at all"})[0] == 401)

        if a.receiver:
            try:
                with urllib.request.urlopen(a.receiver.rstrip("/") + "/healthz", timeout=10) as r:
                    check("watch receiver answers", r.status in (200, 204), r.status)
            except urllib.error.HTTPError as e:
                check("watch receiver answers", e.code in (200, 204), e.code)
    except Exception as e:   # report what was caught so far instead of a bare traceback
        failed.append(f"stopped: {type(e).__name__}: {e}")

    for x in passed:
        print("ok    ", x)
    for x in failed:
        print("FAIL  ", x)
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    sys.exit(1 if failed else 0)


def _cross_site(base):
    """A change sent the way another site's page would: no X-Sanguity header, a foreign Origin."""
    r = urllib.request.Request(base + "/api/logout", data=b"{}", method="POST",
                               headers={"Origin": "https://evil.example", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


if __name__ == "__main__":
    main()
