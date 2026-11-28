"""Outside contact: every request Sanguity's server makes to another host on someone's behalf (#59, #97, #94).

By default Sanguity makes no outside requests. The exceptions are opt-in, and each is made through net.py, the one
way out, under a permit made here from what the install was told to contact:

  for_source(db, source_id, purpose, pid, who, ip, issuer_only=False)
        a connected source an admin set up (connections.py): "Test this connection" and the start of a sign-in
        reach its issuer only; the rest of a sign-in also its token and registration endpoints' hosts, as its
        tested discovery named them. Read again before each request: removing the source stops contact.
  for_connection(db, connection_id, purpose, who, ip)
        a profile's connection (a sync, or revoking on disconnect): the hosts of its source while it's at the
        address the connection was made with (#926), and none once it is disconnected, even in the middle of a pull.
  for_link(db, pid, host, who, ip, also=())
        a SMART Health Link someone pasted and confirmed (shl.py): its host (a pasted Health Card's issuer), and also
        the hosts the link itself names (file locations, Health Card issuers) that the page named and they confirmed
        (#919): one it names beyond those is asked about before it's contacted, never contacted unconfirmed.
  oidc.client(db, who, ip)   (#1109, oidc.py)
        the install's sign-in provider an admin set up: its issuer's host, and the token endpoint's and the JWKS's
        as its own discovery document names them, for a sign-in, a link or an admin's Test connection; none while
        no provider is set up.
  notify.permit(db, who, ip, actor=None)   (#1130, notify.py)
        the install's own notification server an admin set (an ntfy topic): its host alone, for each notification
        someone turned on and an admin's Test; none while no server is set.
  for_directory(db, who, ip, actor=None)
        the provider directory's two published lists (#992; directory/update.py): the hosts directory/build.py names
        for them, only when an admin presses Add the lists or Update now, or once a week after an admin turned that
        on. Account-level: no profile is involved.
  updates.permit(db, who, ip)   (#1159, updates.py)
        GitHub's releases API (api.github.com), for Sanguity's latest release, at most once a day, only while an admin
        has turned checking for new versions on (the install setting updates.check). Account-level.
  for_ocr(db, who, ip)   (#1129, ocr.py)
        a text recognition language an admin downloads from the tesseract-ocr project's tessdata_fast: its host
        (raw.githubusercontent.com) only while an admin has turned that on (the install setting ocr.downloads), and
        contacted only when one presses Download. Account-level too.

Each contact is recorded before the request is sent, so an attempt that fails is listed too:
  - as a profile activity entry "outside" with {"host", "purpose"} (account-level when no profile is involved);
  - in outside_contacts (schema 20): first and last contact and a count per profile, purpose and host, for the
    outside-connections view.
Only the host is kept: never the path, query, token, key or passcode.

  record(db, pid, host, purpose, who=None, ip=None, actor=None)
  recent(db, pid, limit=100) -> [{"at", "host", "purpose", "actor"}]   from the activity log
  view(db, s) -> what the Admin page's "Outside connections" section shows (GET /api/outside)
"""
import json, logging

from . import activity, config, crypto, db as dbm, net, settings

log = logging.getLogger("sanguity.outside")
TRACE = 5   # (#1159) main.TRACE: each contact's host and purpose, at the log level Trace

ACTION = "outside"
SOURCE = "Connected source: "                        # + sign-in, sync, disconnect, test
LINK, LINK_FILE, CARD_KEYS = "SMART Health Link", "SMART Health Link file", "Health Card issuer's keys"
DIRECTORY = "Provider directory"
NOTHING_ELSE = ("Sanguity contacts only the hosts listed here, and only for the reason given. Nothing else leaves "
                "this server: no telemetry, analytics, fonts or maps, no update check unless an admin turns it on, "
                "and no host that nobody connected. Every request to another server goes through one gateway that refuses any host not "
                "listed, and records each contact here.")


def record(db, pid, host, purpose, who=None, ip=None, actor=None):
    """Note that Sanguity is about to contact host for purpose (a short phrase, like "SMART Health Link")."""
    host, purpose = net.norm(str(host)[:255]), str(purpose)[:80]
    log.log(TRACE, "outside contact %s for %s", host, purpose)
    activity.log(db, ACTION, who, pid, {"host": host, "purpose": purpose}, ip, actor=actor)
    now = dbm.now()
    for r in db.execute("SELECT id, host FROM outside_contacts WHERE profile_id IS ? AND purpose=?", (pid, purpose)).fetchall():
        if crypto.dec(r["host"]) == host:
            db.execute("UPDATE outside_contacts SET last=?, count=count+1 WHERE id=?", (now, r["id"]))
            return
    db.execute("INSERT INTO outside_contacts(profile_id, purpose, host, first, last) VALUES(?, ?, ?, ?, ?)",
               (pid, purpose, crypto.enc(host), now, now))


def recent(db, pid, limit=100):
    return [{"at": r["at"], "host": r["detail"].get("host"), "purpose": r["detail"].get("purpose"), "actor": r["actor"]}
            for r in activity.for_profile(db, pid, action=ACTION, limit=limit)]


# ---------------------------------------------------------------- permits (net.Permit)

def _recorder(db, pid, who, ip, actor=None):
    return lambda host, purpose: record(db, pid, host, purpose, who, ip, actor)


def _source_hosts(db, source_id, issuer_only=False):
    from . import connections, smart
    r = db.execute("SELECT * FROM smart_clients WHERE id=?", (source_id,)).fetchone() if source_id is not None else None
    if not r or not settings.connected_sources():   # (#1096) none while connected sources are off
        return set()
    return {smart.host_of(r["issuer"])} if issuer_only else connections.hosts_for(r)


def for_source(db, source_id, purpose, pid=None, who=None, ip=None, issuer_only=False):
    return net.Permit(purpose, lambda: _source_hosts(db, source_id, issuer_only), _recorder(db, pid, who, ip))


def _connection_hosts(db, connection_id):
    """Its source's hosts while the source is at the address the connection was made with, else (the source
    removed, or moved before #926) that address's host alone."""
    from . import connections, smart
    c = db.execute("SELECT state, issuer, client_id FROM connections WHERE id=?", (connection_id,)).fetchone()
    if not c or c["state"] == "disconnected" or not settings.connected_sources():
        return set()
    r = db.execute("SELECT * FROM smart_clients WHERE id=?", (c["client_id"],)).fetchone() if c["client_id"] is not None else None
    return connections.hosts_for(r) if connections.same_server(r, c) else {smart.host_of(c["issuer"])}


def for_connection(db, connection_id, purpose, who=None, ip=None):
    c = db.execute("SELECT profile_id FROM connections WHERE id=?", (connection_id,)).fetchone()
    return net.Permit(purpose, lambda: _connection_hosts(db, connection_id), _recorder(db, c["profile_id"] if c else None, who, ip))


def for_link(db, pid, host, who=None, ip=None, also=()):
    return net.Permit(LINK, {h for h in (host, *also) if h}, _recorder(db, pid, who, ip))


def directory_hosts():
    from .directory import build
    return {net.host_of(s["url"]) for s in build.SOURCES.values() if s["url"]}


def for_directory(db, who=None, ip=None, actor=None):
    return net.Permit(DIRECTORY, lambda: directory_hosts() if settings.connected_sources() else set(), _recorder(db, None, who, ip, actor))


def ocr_hosts():
    """(#1129) tessdata_fast's host while downloading languages is on, else none."""
    from . import ocr
    return {net.host_of(ocr.DOWNLOAD)} if settings.install_value("ocr.downloads") else set()


def for_ocr(db, who=None, ip=None):
    from . import ocr
    return net.Permit(ocr.PURPOSE, ocr_hosts, _recorder(db, None, who, ip))


# ---------------------------------------------------------------- the view

def _profiles(db, s):
    """{id: name} of the profiles whose entries s sees: every profile for admins, else those s owns or edits."""
    if s["is_admin"]:
        rows = db.execute("SELECT id, name FROM profiles")
    else:
        rows = db.execute("""SELECT p.id, p.name FROM memberships m JOIN profiles p ON p.id = m.profile_id
                             WHERE m.user_id=? AND m.role IN ('owner', 'editor')""", (s["id"],))
    return {r["id"]: r["name"] for r in rows}


def _contacts(db, profiles, admin):
    out = []
    for r in db.execute("SELECT * FROM outside_contacts ORDER BY last DESC"):
        pid = r["profile_id"]
        if pid in profiles or (admin and pid is None):
            out.append({"profile": pid, "profile_name": profiles.get(pid), "purpose": r["purpose"],
                        "host": crypto.dec(r["host"]), "first": r["first"], "last": r["last"], "count": r["count"]})
    return out


def _last(contacts, hosts, pid=...):
    times = [c["last"] for c in contacts if c["purpose"].startswith(SOURCE) and c["host"] in hosts
             and (pid is ... or c["profile"] == pid)]
    return max(times) if times else None


def view(db, s):
    """Every host this install may contact, as s may see it, grouped by purpose, with when each was last contacted.
    Admins see the whole install; owners and editors their profiles' entries; nothing here is a health record. While
    connected sources are off (#1096), nothing of theirs: no sources, no directory, none contacted before."""
    from . import connections, smart
    admin, on = bool(s["is_admin"]), settings.connected_sources()
    profiles = _profiles(db, s)
    contacts = [c for c in _contacts(db, profiles, admin) if on or not (c["purpose"].startswith(SOURCE) or c["purpose"] == DIRECTORY)]
    live = [c for c in db.execute("SELECT * FROM connections WHERE state != 'disconnected' ORDER BY id")
            if on and c["profile_id"] in profiles]
    if not on:
        clients = []
    elif admin:
        clients = db.execute("SELECT * FROM smart_clients ORDER BY name, id").fetchall()
    else:
        ids = sorted({c["client_id"] for c in live if c["client_id"] is not None})
        clients = [db.execute("SELECT * FROM smart_clients WHERE id=?", (i,)).fetchone() for i in ids]
    sources = []

    def entry(name, vendor, issuer, conf, hosts, requested, tested, mine, kid):
        named = {"issuer": smart.host_of(issuer)}
        for k, key in (("token", "token_endpoint"), ("revocation", "revocation_endpoint"), ("registration", "registration_endpoint")):
            named[k] = net.host_of(conf[key]) if isinstance(conf.get(key), str) else None
        hosts = {net.norm(h) for h in hosts}
        return {"id": kid, "name": name, "vendor": vendor, "issuer": issuer,
                # what Set up a source calls it ("Epic (MyChart)"; #528: its key, "epic", was shown)
                "vendor_label": connections.GUIDES[vendor]["label"] if vendor in connections.GUIDES else None,
                "hosts": {k: net.norm(v) if v else None for k, v in named.items()}, "may_contact": sorted(hosts),
                "scopes_requested": requested, "tested": tested,
                "connections": [{"id": c["id"], "profile": c["profile_id"], "profile_name": profiles.get(c["profile_id"]),
                                 "state": c["state"], "scopes_granted": c["scopes"], "last_sync": c["last_sync"],
                                 "last_contact": _last(contacts, hosts, c["profile_id"])} for c in mine],
                "last_contact": _last(contacts, hosts)}

    for k in clients:
        conf = json.loads(k["discovery"]) if k["discovery"] else {}
        sources.append(entry(k["name"], k["vendor"], k["issuer"], conf, connections.hosts_for(k), k["scopes"], k["tested"],
                             [c for c in live if c["client_id"] == k["id"]], k["id"]))
    for c in live:   # a connection whose source an admin has since removed: its issuer only
        if c["client_id"] is None:
            sources.append(entry(c["source"], None, c["issuer"], {}, {smart.host_of(c["issuer"])}, None, None, [c], None))
    from . import notify, oidc
    signin = oidc.hosts(db) if admin else []   # (#1109) the sign-in provider's: an admin's to see, as the directory's
    told = sorted(notify.hosts())   # (#1130) the notification server: everyone's to see, as anyone may turn them on
    from . import ocr
    languages = sorted(ocr_hosts()) if admin else []   # (#1129) tessdata_fast's, while downloads are on
    from . import updates
    newer = sorted(updates.hosts()) if admin else []   # (#1159) GitHub's releases, while an admin has the check on
    allowed = sorted({h for x in sources for h in x["may_contact"]} | set(signin) | set(told) | set(languages) | set(newer))
    pick = lambda *ps: [c for c in contacts if c["purpose"] in ps]
    return {"nothing_else": NOTHING_ELSE, "admin": admin, "connected_sources": on, "may_contact": allowed, "sources": sources,
            "health_links": pick(LINK, LINK_FILE), "card_keys": pick(CARD_KEYS),
            **({"directory": {"hosts": sorted(directory_hosts()), "contacts": pick(DIRECTORY)}} if admin and on else {}),
            **({"sign_in": {"hosts": signin, "contacts": pick(oidc.PURPOSE)}} if signin else {}),
            **({"notifications": {"hosts": told, "contacts": pick(notify.PURPOSE)}} if told else {}),
            **({"languages": {"hosts": languages, "contacts": pick(ocr.PURPOSE)}} if admin and (languages or pick(ocr.PURPOSE)) else {}),
            **({"updates": {"hosts": newer, "contacts": pick(updates.PURPOSE)}} if admin and (newer or pick(updates.PURPOSE)) else {}),
            "earlier": [c for c in contacts if c["purpose"].startswith(SOURCE) and c["host"] not in allowed]}


# ---------------------------------------------------------------- (#1159) the outbound proxy

PROXY_PASSWORD_META, PROXY_PASSWORD_ENV = "outside.proxy_password", "OUTBOUND_PROXY_PASSWORD"
_password = {}   # the saved password, read once: {"proxy": the proxy's address it's for, "password"}


def proxy_password_source(db=None, address=None):
    """Where the proxy's password comes from: "environment" (OUTBOUND_PROXY_PASSWORD or its _FILE, #1158's opt-in),
    "install" (saved here for the proxy at address: by default the one in force), or None."""
    if config.managed(PROXY_PASSWORD_ENV) is not None:
        return "environment"
    return "install" if _saved_password(db, address) else None


def _saved_password(db=None, address=None):
    from . import server_settings
    if "proxy" not in _password:
        own = db is None
        db = db or dbm.connect()
        try:
            stored = dbm.meta_get(db, PROXY_PASSWORD_META)
            _password.update(json.loads(crypto.dec(stored)) if stored else {"proxy": None})
        except ValueError:
            _password.update(proxy=None)
        finally:
            if own:
                db.close()
    address = server_settings.value("OUTBOUND_PROXY") if address is None else address
    return _password.get("password") if address and _password.get("proxy") == address else None


def set_proxy_password(db, value, for_proxy):
    """A password for the proxy for_proxy (the address it's kept for: sent to no other); "" removes it."""
    value = (value or "").strip()
    if len(value) > 200 or any(not 0x20 <= ord(c) <= 0x7e for c in value):
        raise ValueError("A proxy password is up to 200 letters, digits, spaces and signs.")
    dbm.meta_set(db, PROXY_PASSWORD_META, crypto.enc(json.dumps({"proxy": for_proxy, "password": value})) if value else None)
    _password.clear()


def proxy():
    """The outbound proxy in force (net.PROXY): Needs a restart's, with its password, or None."""
    from . import server_settings
    address = server_settings.value("OUTBOUND_PROXY")
    if not address:
        return None
    scheme, rest = address.split("://", 1)
    host, port = rest.rsplit(":", 1)
    user = server_settings.value("OUTBOUND_PROXY_USER")
    password = config.managed(PROXY_PASSWORD_ENV)
    if password is None and user:
        password = _saved_password() or ""
    return net.Proxy(scheme, host.strip("[]"), int(port), user, password or "",
                     [b.strip() for b in server_settings.value("OUTBOUND_PROXY_BYPASS").split(",")])


net.PROXY = proxy

