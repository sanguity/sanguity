"""Outside contact: every request Sanguity's server makes to another host on someone's behalf (#59, #97, #94).

By default Sanguity makes no outside requests. The exceptions are opt-in, and each is made through net.py, the one
way out, under a permit made here from what the install was told to contact:

  for_source(db, source_id, purpose, pid, who, ip, issuer_only=False)
        a connected source an admin set up (connections.py): "Test this connection" and the start of a sign-in
        reach its issuer only; the rest of a sign-in also its token and registration endpoints' hosts, as its
        tested discovery named them. Read again before each request: removing the source stops contact.
  for_connection(db, connection_id, purpose, who, ip)
        a profile's connection (a sync, or revoking on disconnect): the hosts of its source, and none once it is
        disconnected, even in the middle of a pull.
  for_link(db, pid, host, who, ip, also=())
        a SMART Health Link someone pasted and confirmed (shl.py): its host (a pasted Health Card's issuer), and also
        the hosts the link itself names (file locations, Health Card issuers) that the page named and they confirmed
        (#919): one it names beyond those is asked about before it's contacted, never contacted unconfirmed.

Each contact is recorded before the request is sent, so an attempt that fails is listed too:
  - as a profile activity entry "outside" with {"host", "purpose"} (account-level when no profile is involved);
  - in outside_contacts (schema 20): first and last contact and a count per profile, purpose and host, for the
    outside-connections view.
Only the host is kept: never the path, query, token, key or passcode.

  record(db, pid, host, purpose, who=None, ip=None)
  recent(db, pid, limit=100) -> [{"at", "host", "purpose", "actor"}]   from the activity log
  view(db, s) -> what the Admin page's "Outside connections" section shows (GET /api/outside)
"""
import json

from . import activity, crypto, db as dbm, net

ACTION = "outside"
SOURCE = "Connected source: "                        # + sign-in, sync, disconnect, test
LINK, LINK_FILE, CARD_KEYS = "SMART Health Link", "SMART Health Link file", "Health Card issuer's keys"
NOTHING_ELSE = ("Sanguity contacts only the hosts listed here, and only for the reason given. Nothing else leaves "
                "this server: no telemetry, analytics, update checks, fonts or maps, and no host that nobody "
                "connected. Every request to another server goes through one gateway that refuses any host not "
                "listed, and records each contact here.")


def record(db, pid, host, purpose, who=None, ip=None):
    """Note that Sanguity is about to contact host for purpose (a short phrase, like "SMART Health Link")."""
    host, purpose = net.norm(str(host)[:255]), str(purpose)[:80]
    activity.log(db, ACTION, who, pid, {"host": host, "purpose": purpose}, ip)
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

def _recorder(db, pid, who, ip):
    return lambda host, purpose: record(db, pid, host, purpose, who, ip)


def _source_hosts(db, source_id, issuer_only=False):
    from . import connections, smart
    r = db.execute("SELECT * FROM smart_clients WHERE id=?", (source_id,)).fetchone() if source_id is not None else None
    if not r:
        return set()
    return {smart.host_of(r["issuer"])} if issuer_only else connections.hosts_for(r)


def for_source(db, source_id, purpose, pid=None, who=None, ip=None, issuer_only=False):
    return net.Permit(purpose, lambda: _source_hosts(db, source_id, issuer_only), _recorder(db, pid, who, ip))


def _connection_hosts(db, connection_id):
    from . import smart
    c = db.execute("SELECT state, issuer, client_id FROM connections WHERE id=?", (connection_id,)).fetchone()
    if not c or c["state"] == "disconnected":
        return set()
    return _source_hosts(db, c["client_id"]) if c["client_id"] is not None else {smart.host_of(c["issuer"])}


def for_connection(db, connection_id, purpose, who=None, ip=None):
    c = db.execute("SELECT profile_id FROM connections WHERE id=?", (connection_id,)).fetchone()
    return net.Permit(purpose, lambda: _connection_hosts(db, connection_id), _recorder(db, c["profile_id"] if c else None, who, ip))


def for_link(db, pid, host, who=None, ip=None, also=()):
    return net.Permit(LINK, {h for h in (host, *also) if h}, _recorder(db, pid, who, ip))


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
    Admins see the whole install; owners and editors their profiles' entries; nothing here is a health record."""
    from . import connections, smart
    admin = bool(s["is_admin"])
    profiles = _profiles(db, s)
    contacts = _contacts(db, profiles, admin)
    live = [c for c in db.execute("SELECT * FROM connections WHERE state != 'disconnected' ORDER BY id")
            if c["profile_id"] in profiles]
    if admin:
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
    allowed = sorted({h for x in sources for h in x["may_contact"]})
    pick = lambda *ps: [c for c in contacts if c["purpose"] in ps]
    return {"nothing_else": NOTHING_ELSE, "admin": admin, "may_contact": allowed, "sources": sources,
            "health_links": pick(LINK, LINK_FILE), "card_keys": pick(CARD_KEYS),
            "earlier": [c for c in contacts if c["purpose"].startswith(SOURCE) and c["host"] not in allowed]}
