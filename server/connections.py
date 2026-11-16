"""Connected sources (#32, #91): the vendors an admin has set up, signing in to one for a profile, and the
connection that sign-in leaves, which sync.py then keeps pulling from. The protocol itself is in smart.py.

How sign-in works was decided in #90 (docs/connected-sources-oauth.md, option 1): each install registers its own
app with each vendor, and the vendor sends the sign-in straight back to the install's own address.

  Vendor settings (smart_clients, schema version 18), one row per FHIR server an admin has set up: its issuer,
  client ID, client secret where the vendor issued one (encrypted), how it authenticates (public, confidential, or
  Epic's dynamic registration) and the scopes to ask for. "Test this connection" reads its discovery and keeps it,
  with the hosts it names: the server contacts no other host for that source.

  The return address is return_address(): today the install's own /connect/return at its PUBLIC_ORIGINS address.
  Option 2's relay would replace that one function; nothing else depends on which it is.

  Sign-in:
    start()   (owners and editors) discovery, PKCE (S256) and a random, single-use state, kept as a pending sign-in
              for 10 minutes, bound to the session and profile (connect_pending: a hash of state, the verifier
              encrypted, and the token endpoint discovery named), then the vendor's authorization address
    finish()  from /connect/return: the state must be pending for this session and unexpired (it is used up either
              way); the vendor's error, if it sent one, is shown; the code is exchanged with the verifier at the
              stored token endpoint, never one from the request; the answer must name a patient and, where it
              says, the audience and issuer it was asked for. On Epic (auth "dynamic") the install then registers
              its own key and uses the JWT bearer grant from then on. The connection is stored, its credentials
              encrypted (crypto.enc), and its first pull queued (sync.py)

  Tokens (the Tokens class, for smart.Reader): an access token until a minute before it runs out, then a new one
  from the refresh token (a rotated refresh token is saved at once, before it is used), or a new JWT assertion.
  When the vendor ends access (invalid_grant), the connection is "needs reconnect" and the page asks to sign in
  again; a source that gave no refresh token needs that once its access token runs out.

  disconnect() revokes the token where the vendor lists a revocation endpoint, deletes the token, refresh token
  and key, and keeps or removes the records it brought (each of its imports undone, as from the import history).

  counts() is the local count of connections per vendor, for admins. It is never sent anywhere.

Every request goes through net.py under a permit from outside.py, which records each host first, with the profile it
was for, and refuses any host the source's setup doesn't name (#97).
"""
import hashlib, ipaddress, json, re, secrets, time, urllib.parse

from . import activity, config, crypto, db as dbm, outside, smart

PENDING_SECONDS = 600          # a sign-in must come back within this
EARLY = 60                     # renew an access token this long before it runs out
RETURN_PATH = "/connect/return"
AUTHS = ("public", "confidential", "dynamic")
STATES = ("active", "needs reconnect", "disconnected")
SYNC_ACTOR = {"id": None, "username": activity.SCHEDULED_SYNC}

# the resource types a pull reads (sync.py), and the scopes asked for by default: SMART v2 where discovery says the
# server has it, else v1 (Epic's documentation still uses .read)
TYPES = ("Patient", "Observation", "Condition", "MedicationRequest", "Encounter", "DocumentReference",
         "DiagnosticReport", "ExplanationOfBenefit", "Coverage")
BINARY_FOR = ("DocumentReference", "DiagnosticReport")
# an insurer's patient-access API (CARIN Blue Button, #92): claims, the coverage they're under, and the patient
INSURER_TYPES = ("Patient", "ExplanationOfBenefit", "Coverage")
CLINICAL_TYPES = tuple(t for t in TYPES if t != "Coverage") + ("Binary",)


def default_scopes(conf=None, vendor=None):
    caps = set((conf or {}).get("capabilities") or [])
    suffix = "rs" if "permission-v2" in caps else "read"
    types = INSURER_TYPES if vendor == "insurer" else CLINICAL_TYPES
    return " ".join(["launch/patient", "offline_access"] + [f"patient/{t}.{suffix}" for t in types])


SCOPE_RE = re.compile(r"(?:patient|user)/([A-Za-z]+|\*)\.([a-z*]+)(?:\?\S*)?")


def granted(scopes, rtype):
    """Whether granted scopes let a pull search rtype: SMART v1 (.read, .*) or v2 (with s). True when the vendor
    named no resource scopes at all (it didn't say), so the search is tried and its answer reported."""
    found = [m.groups() for m in (SCOPE_RE.fullmatch(x) for x in str(scopes or "").split()) if m]
    if not found:
        return True
    return any(t in (rtype, "*") and (op in ("read", "*") or op != "write" and "s" in op) for t, op in found)


# what the setup screen says per vendor. Facts from docs/connected-sources-oauth.md (read November 2026); the
# unverified ones are marked so
GUIDES = {
    "epic": {
        "label": "Epic (MyChart)",
        "register": "https://fhir.epic.com/Developer/Apps",
        "sandbox": "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4",
        "steps": [
            "Sign in at Epic on FHIR (fhir.epic.com) with a developer account, and choose Build Apps → Create.",
            "Application audience: Patients. Add the FHIR R4 read APIs this install pulls (listed under Scopes below); "
            "apps that use only qualifying patient APIs are made available to Epic hospitals automatically.",
            "Add the return address below as a redirect URI, exactly as shown.",
            "For long-lived access without a client secret, turn on dynamic client registration and pick "
            "\"Epic dynamic registration\" here: the install makes its own key and Epic never sees a secret. "
            "Otherwise a confidential client (with a secret) is what Epic needs for refresh tokens.",
            "Copy the non-production client ID into Sanguity to use Epic's sandbox (the FHIR base above) with Epic's "
            "published test patients; the production client ID works with real hospitals once Epic has "
            "distributed it to them, which reportedly takes hours (unverified).",
        ],
        "notes": ["Whether Epic accepts an app registered by one person for production, or a tailnet name such as "
                  "*.ts.net as its redirect URI, is unverified."],
    },
    "oracle": {
        "label": "Oracle Health (Cerner)",
        "register": "https://code-console.cerner.com",
        "sandbox": None,
        "steps": [
            "Register a patient app in Oracle Health's code console, with the return address below as its redirect URI.",
            "Oracle Health wants a valid TLS certificate and a DNS TXT record (cerner-client-id=…) on the redirect "
            "host's domain, which a home install usually can't provide; without the record, people see \"Unknown "
            "Application\".",
        ],
        "notes": ["Effectively out of reach for home installs under option 1 (docs/connected-sources-oauth.md)."],
    },
    "insurer": {
        "label": "Insurer (patient-access API)",
        "register": None,
        "sandbox": "https://sandbox.bluebutton.cms.gov/v2/fhir/",
        "sandbox_name": "Medicare Blue Button sandbox",
        "scopes": "launch/patient offline_access patient/Patient.read patient/ExplanationOfBenefit.read patient/Coverage.read",
        "steps": [
            "Since 2021 US health plans must offer a Patient Access API (CMS-9115-F): claims as CARIN Blue Button "
            "ExplanationOfBenefit resources, with Coverage and Patient. Sanguity pulls those three and shows the "
            "claims on the Costs tab.",
            "Find your plan's developer portal (each runs its own, with its own terms and review) and register a "
            "patient-facing app with the return address below as its redirect URI, exactly as shown.",
            "Ask for the CARIN Blue Button scopes below (patient/ExplanationOfBenefit.read, patient/Coverage.read, "
            "patient/Patient.read, or the .rs forms on SMART v2), plus launch/patient and offline_access.",
            "If the plan issues a client secret, pick \"Confidential client\" and paste it: this install keeps it "
            "encrypted on its own server, which is why option 1 can work with confidential-only payers.",
            "Medicare Blue Button 2.0: create an account at sandbox.bluebutton.cms.gov, register an app (client "
            "type confidential, authorization code with PKCE, the return address as its callback URL), copy the "
            "client ID and secret, and use the sandbox FHIR base above with Blue Button's synthetic beneficiaries.",
        ],
        "portals": [
            {"name": "Medicare Blue Button 2.0 (CMS)", "url": "https://bluebutton.cms.gov/developers/",
             "note": "Sandbox: self-service, confidential clients only, PKCE S256 required. Production needs a "
                     "privacy policy, terms, an application and a demo to CMS, impractical for one person."},
            {"name": "Humana", "url": "https://developers.humana.com/apis/registerapp",
             "note": "A FHIR sandbox with synthetic members; registering an app goes through Humana's Data "
                     "Exchange portal. Whether an individual's app is approved for production is unverified."},
            {"name": "Cigna", "url": "https://developer.cigna.com",
             "note": "Developer registration, then a sandbox client on request (unverified: from a 2021 Cigna "
                     "sandbox guide). Production terms for individuals unverified."},
            {"name": "Aetna", "url": "https://developerportal.aetna.com",
             "note": "Self-registration asks for a company email (unverified: third-party guide); production "
                     "review for individuals unverified."},
            {"name": "Elevance Health (Anthem)", "url": "https://anthem.com/developers",
             "note": "Sandbox, then a separate production request form. Whether individuals qualify is unverified."},
            {"name": "UnitedHealthcare", "url": "https://www.uhc.com/legal/interoperability-apis",
             "note": "Its developer registration route wasn't confirmed (the page refused automated reading): "
                     "unverified."},
            {"name": "VA Lighthouse (Patient Health API)", "url": "https://developer.va.gov",
             "note": "Sandbox access by application; client type for patient apps unverified. Not tested with Sanguity."},
        ],
        "notes": ["Payer portals and their rules change; everything here except Blue Button's own documentation "
                  "is marked unverified until someone confirms it. Plans on Epic's payer platform may accept an "
                  "Epic payer app instead (the \"CMS Payer Apps\" category: see docs/connected-sources-oauth.md).",
                  "A sync asks only for the kinds of records the granted scopes cover, so an insurer connection "
                  "reads Patient, ExplanationOfBenefit and Coverage, nothing clinical."],
    },
    "other": {
        "label": "Another SMART on FHIR server",
        "register": None,
        "sandbox": None,
        "steps": [
            "Register a patient-facing (standalone launch) app on the vendor's developer site, with the return "
            "address below as its redirect URI, exactly as shown.",
            "Paste the client ID it gives you, and the client secret if it issued one (a confidential client).",
            "Ask for the scopes below, or fewer: each sync says which kinds of records it could and couldn't pull.",
        ],
        "notes": [],
    },
}


class Problem(Exception):
    """Something to tell the person, with an HTTP status."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _hash(state):
    return hashlib.sha256(state.encode()).hexdigest()


def _jenc(obj):
    return crypto.enc(json.dumps(obj, separators=(",", ":")))


def _jdec(text):
    return json.loads(crypto.dec(text)) if text else None


# ---------------------------------------------------------------- the return address

def return_address():
    """Where the vendor sends a sign-in back: this install's /connect/return, at its first https:// address in
    PUBLIC_ORIGINS (else its first address there). None until PUBLIC_ORIGINS is set. Under option 2 this would be
    the relay's address instead; it is the only thing that would change."""
    origins = sorted(config.PUBLIC_ORIGINS, key=lambda o: (not o.startswith("https://"), o))
    return origins[0] + RETURN_PATH if origins else None


def address_warnings(uri):
    """Why a vendor may not accept this return address in production (docs/connected-sources-oauth.md)."""
    if not uri:
        return ["Set PUBLIC_ORIGINS (in .env) to the address people open Sanguity at, like "
                "https://health.example.com, and restart: the return address is made from it."]
    u = urllib.parse.urlsplit(uri)
    host, out = (u.hostname or "").lower(), []
    if u.scheme != "https":
        out.append("This address uses plain HTTP. Vendors' sandboxes may take it, but production apps are unlikely "
                   "to be accepted without HTTPS.")
    try:
        ip = ipaddress.ip_address(host)
        lan = ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        lan = (host == "localhost" or "." not in host or
               host.endswith((".local", ".lan", ".home.arpa", ".internal", ".localhost", ".home", ".test")))
    if lan:
        out.append("This address only works on your own network. A vendor's sign-in can still return to it from a "
                   "browser on that network, but vendors are unlikely to accept it for production (Epic discourages "
                   "localhost; Oracle Health needs a public domain with a DNS record).")
    elif host.endswith(".ts.net"):
        out.append("This is a tailnet name. Whether vendors accept one for production is unverified.")
    return out


# ---------------------------------------------------------------- vendor settings (admins)

def _client(db, cid):
    r = db.execute("SELECT * FROM smart_clients WHERE id=?", (cid,)).fetchone()
    if not r:
        raise Problem("No such source.", 404)
    return r


def _discovery(r):
    return json.loads(r["discovery"]) if r["discovery"] else None


def hosts_for(r):
    """The hosts the server may contact for a vendor setting: the issuer's, and those its tested discovery named."""
    conf = _discovery(r)
    return smart.server_hosts(r["issuer"], conf) if conf else {smart.host_of(r["issuer"])}


def counts(db):
    """{vendor setting id: {state: connections}}: the local count, never sent anywhere."""
    out = {}
    for r in db.execute("SELECT client_id, state, COUNT(*) AS n FROM connections WHERE client_id IS NOT NULL GROUP BY client_id, state"):
        out.setdefault(r["client_id"], {s: 0 for s in STATES})[r["state"]] = r["n"]
    return out


def describe_client(r, n=None):
    conf = _discovery(r)
    return {"id": r["id"], "name": r["name"], "vendor": r["vendor"], "issuer": r["issuer"], "client_id": r["client_id"],
            "has_secret": bool(r["secret"]), "auth": r["auth"], "scopes": r["scopes"], "tested": r["tested"],
            "hosts": sorted(hosts_for(r)),
            "endpoints": {k: conf.get(k) for k in ("authorization_endpoint", "token_endpoint", "revocation_endpoint",
                                                   "registration_endpoint")} if conf else None,
            "capabilities": (conf or {}).get("capabilities") or [],
            "connections": n or {s: 0 for s in STATES}}


def setup(db):
    """What the admins' setup screen shows: the return address and its warnings, a guide per vendor, the default
    scopes, and each vendor setting with its local count of connections."""
    uri, n = return_address(), counts(db)
    return {"return_address": uri, "warnings": address_warnings(uri), "guides": GUIDES, "default_scopes": default_scopes(),
            "insurer_scopes": default_scopes(vendor="insurer"),
            "sources": [describe_client(r, n.get(r["id"])) for r in db.execute("SELECT * FROM smart_clients ORDER BY name, id")]}


def choices(db):
    """For owners and editors: the sources they can connect a profile to (set up and tested by an admin)."""
    return [{"id": r["id"], "name": r["name"], "vendor": r["vendor"], "issuer": r["issuer"], "host": smart.host_of(r["issuer"])}
            for r in db.execute("SELECT * FROM smart_clients WHERE tested IS NOT NULL ORDER BY name, id")]


def _clean(data, old=None):
    name = (data.get("name") or (old and old["name"]) or "").strip()
    if not name or len(name) > 80:
        raise Problem("Give the source a name of up to 80 characters.", 400)
    vendor = data.get("vendor") or (old and old["vendor"]) or "other"
    if vendor not in GUIDES:
        raise Problem("Pick the vendor: epic, oracle, insurer or other.", 400)
    try:
        issuer = smart.base_url(data.get("issuer") or (old and old["issuer"]) or "", "FHIR base address")
    except smart.SmartError as e:
        raise Problem(str(e), 400) from None
    cid = (data.get("client_id") if data.get("client_id") is not None else (old and old["client_id"]) or "").strip()
    if not cid or len(cid) > 200 or not cid.isprintable():
        raise Problem("Paste the client ID the vendor gave you.", 400)
    auth = data.get("auth") or (old and old["auth"]) or "public"
    if auth not in AUTHS:
        raise Problem("How the app signs in must be public, confidential or dynamic.", 400)
    scopes = " ".join((data.get("scopes") if data.get("scopes") is not None else (old and old["scopes"]) or default_scopes(vendor=vendor)).split())
    if not scopes or len(scopes) > 2000 or not re.fullmatch(r"[\x21\x23-\x5b\x5d-\x7e ]+", scopes):
        raise Problem("The scopes must be space-separated, like launch/patient offline_access patient/*.read.", 400)
    return name, vendor, issuer, cid, auth, scopes


def save_client(db, who, data, cid=None, ip=None):
    """Add (cid None) or change a vendor setting. secret: a new one; "" removes it; None or missing keeps it."""
    old = _client(db, cid) if cid else None
    name, vendor, issuer, client_id, auth, scopes = _clean(data, old)
    secret = data.get("secret")
    secret = old["secret"] if secret is None and old else (crypto.enc(secret.strip()) if secret and secret.strip() else None)
    if secret and len(crypto.dec(secret)) > 2000:
        raise Problem("That client secret is too long.", 400)
    if auth == "confidential" and not secret:
        raise Problem("A confidential client needs its client secret.", 400)
    if auth != "confidential":
        secret = None   # a public or dynamic client has none; one left over isn't kept
    if db.execute("SELECT 1 FROM smart_clients WHERE issuer=? AND id IS NOT ?", (issuer, cid)).fetchone():
        raise Problem("Another source has that FHIR base address already.", 409)
    now = dbm.now()
    if old:
        moved = issuer != old["issuer"]
        db.execute("""UPDATE smart_clients SET name=?, vendor=?, issuer=?, client_id=?, secret=?, auth=?, scopes=?, changed=?,
                      discovery=CASE WHEN ? THEN NULL ELSE discovery END, tested=CASE WHEN ? THEN NULL ELSE tested END WHERE id=?""",
                   (name, vendor, issuer, client_id, secret, auth, scopes, now, moved, moved, cid))
    else:
        cid = db.execute("""INSERT INTO smart_clients(name, vendor, issuer, client_id, secret, auth, scopes, created, created_by)
                            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""", (name, vendor, issuer, client_id, secret, auth, scopes, now, who["id"])).lastrowid
    activity.log(db, "connect.source", who, None, {"name": name, "issuer": issuer, "change": "edit" if old else "add"}, ip)
    return describe_client(_client(db, cid), counts(db).get(cid))


def remove_client(db, who, cid, ip=None):
    r = _client(db, cid)
    live = db.execute("SELECT COUNT(*) FROM connections WHERE client_id=? AND state != 'disconnected'", (cid,)).fetchone()[0]
    if live:
        raise Problem(f"{live} profile{'s are' if live != 1 else ' is'} still connected to this source. Disconnect "
                      "them first (on each profile's Connected sources).", 409)
    with dbm.transaction(db):
        db.execute("DELETE FROM smart_clients WHERE id=?", (cid,))
        activity.log(db, "connect.source", who, None, {"name": r["name"], "issuer": r["issuer"], "change": "remove"}, ip)


def test_client(db, who, cid, ip=None):
    """"Test this connection": read the source's discovery (no sign-in), keep it, and say what it found. Only the
    issuer's host is contacted, and it is recorded first."""
    r = _client(db, cid)
    http = smart.Client(outside.for_source(db, cid, "Connected source: test", None, who, ip, issuer_only=True))
    old = _discovery(r)
    try:
        conf = smart.discover(http, r["issuer"], (old or {}).get("issuer"))
    except smart.SmartError as e:
        return {"ok": False, "problem": str(e), "kind": e.kind}
    caps = set(conf.get("capabilities") or [])
    notes = []
    if r["auth"] == "dynamic" and not conf.get("registration_endpoint"):
        notes.append("Its discovery lists no registration_endpoint, which Epic dynamic registration needs.")
    if r["auth"] == "public" and caps and "client-public" not in caps:
        notes.append("Its discovery doesn't list client-public: it may only accept confidential clients.")
    if r["auth"] == "confidential" and caps and "client-confidential-symmetric" not in caps:
        notes.append("Its discovery doesn't list client-confidential-symmetric (a client secret).")
    if caps and "permission-offline" not in caps:
        notes.append("Its discovery doesn't list permission-offline: it may give no refresh tokens, and each "
                     "connection would then need signing in again when its access runs out.")
    if not conf.get("revocation_endpoint"):
        notes.append("It lists no revocation_endpoint: disconnecting deletes the tokens here, and the patient can "
                     "also end access in their portal.")
    if not isinstance(conf.get("code_challenge_methods_supported"), list):
        notes.append("It doesn't list code_challenge_methods_supported; Sanguity sends PKCE (S256) anyway.")
    hosts = smart.server_hosts(r["issuer"], conf)
    keep = {k: v for k, v in conf.items() if k in ("issuer", "authorization_endpoint", "token_endpoint", "revocation_endpoint",
                                                     "registration_endpoint", "capabilities", "scopes_supported",
                                                     "code_challenge_methods_supported", "grant_types_supported")}
    db.execute("UPDATE smart_clients SET discovery=?, tested=? WHERE id=?", (json.dumps(keep), dbm.now(), cid))
    others = sorted(hosts - {smart.host_of(r["issuer"])})
    if others:
        notes.append("Its token, revocation or registration endpoints are on " + ", ".join(others) +
                     ": Sanguity will contact those hosts too for this source.")
    return {"ok": True, "endpoints": {k: keep.get(k) for k in ("authorization_endpoint", "token_endpoint",
                                                              "revocation_endpoint", "registration_endpoint")},
            "capabilities": sorted(caps), "hosts": sorted(hosts), "suggested_scopes": default_scopes(conf, r["vendor"]),
            "return_address": return_address(), "warnings": address_warnings(return_address()), "notes": notes}


# ---------------------------------------------------------------- signing in

def _resolve(db, vendor=None, issuer=None):
    """The vendor setting for a POST start: by id, or by issuer (a directory entry's FHIR base, #93)."""
    if vendor is not None:
        r = db.execute("SELECT * FROM smart_clients WHERE id=?", (vendor,)).fetchone()
    elif issuer:
        try:
            r = db.execute("SELECT * FROM smart_clients WHERE issuer=?", (smart.base_url(issuer),)).fetchone()
        except smart.SmartError as e:
            raise Problem(str(e), 400) from None
    else:
        raise Problem("Say which source to connect.", 400)
    if not r:
        raise Problem("This source isn't set up on this install yet. An admin can set it up under Connected "
                      "sources: each install registers its own app with each vendor.", 409)
    return r


def start(db, s, pid, vendor=None, issuer=None, connection_id=None, ip=None):
    """Begin a sign-in for profile pid (the caller has checked the role). Returns the vendor's authorization address."""
    r = _resolve(db, vendor, issuer)
    if not r["tested"]:
        raise Problem("An admin needs to test this source's setup first (Test this connection).", 409)
    uri = return_address()
    if not uri:
        raise Problem(address_warnings(None)[0], 409)
    if connection_id is not None:
        c = db.execute("SELECT * FROM connections WHERE id=? AND profile_id=?", (connection_id, pid)).fetchone()
        if not c or c["state"] == "disconnected":
            raise Problem("No such connection.", 404)
        if c["client_id"] != r["id"]:
            raise Problem("This connection belongs to another source.", 409)
    allowed = hosts_for(r)
    http = smart.Client(outside.for_source(db, r["id"], "Connected source: sign-in", pid, s, ip, issuer_only=True))
    try:
        conf = smart.discover(http, r["issuer"], (_discovery(r) or {}).get("issuer"))
    except smart.SmartError as e:
        raise Problem(str(e), 502) from None
    if not smart.server_hosts(r["issuer"], conf) <= allowed:
        raise Problem("This source's endpoints have moved to another host since it was tested. Ask an admin to "
                      "test it again before connecting.", 409)
    verifier, challenge = smart.pkce()
    state = secrets.token_urlsafe(32)   # 256 bits; SMART asks for at least 122
    now = dbm.now()
    with dbm.transaction(db):
        db.execute("DELETE FROM connect_pending WHERE expires <= ?", (now,))
        db.execute("""INSERT INTO connect_pending(state_hash, session_id, profile_id, client_id, connection_id, verifier,
                      redirect_uri, scopes, token_url, expires) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (_hash(state), s["sid"], pid, r["id"], connection_id, crypto.enc(verifier), uri, r["scopes"],
                    conf["token_endpoint"], now + PENDING_SECONDS))
    return smart.authorize_url(conf, r["client_id"], uri, r["scopes"], state, challenge, r["issuer"])


def _claims(token):
    """The claims of a JWT access token, unverified (only to compare aud and iss with what was asked); {} otherwise."""
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        return {}
    try:
        import base64
        c = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        return c if isinstance(c, dict) else {}
    except ValueError:
        return {}


def _check_audience(tok, issuer, client_id):
    """The token answer, where it says, is for this FHIR server: an aud in the answer or in a JWT access token must
    name the issuer (or, for a JWT's aud, the client), and a JWT's iss must be the issuer's server."""
    said = [tok.get("aud")] if tok.get("aud") is not None else []
    claims = _claims(tok.get("access_token"))
    if "aud" in claims:
        said.append(claims["aud"])
    for aud in said:
        auds = aud if isinstance(aud, list) else [aud]
        if not any(isinstance(a, str) and a.rstrip("/") in (issuer, client_id) for a in auds):
            raise Problem(f"The vendor's token is for another server ({str(auds)[:120]}), not {issuer}. Nothing was saved.", 400)
    iss = claims.get("iss")
    if isinstance(iss, str) and smart.host_of(iss) != smart.host_of(issuer):
        raise Problem(f"The vendor's token was issued by {smart.host_of(iss)}, not {smart.host_of(issuer)}. Nothing was saved.", 400)


def finish(db, s, code=None, state=None, error=None, error_description=None, ip=None, role_check=None):
    """The return from the vendor. Returns {"profile", "connection", "reconnected"}. Raises Problem with what went
    wrong; the pending sign-in is used up in every case."""
    from . import sync
    if not state or len(state) > 200:
        raise Problem("The sign-in came back without its state, so it can't be matched to one started here. Start "
                      "it again from Connected sources.", 400)
    with dbm.transaction(db):
        p = db.execute("SELECT * FROM connect_pending WHERE state_hash=?", (_hash(state),)).fetchone()
        if p:
            db.execute("DELETE FROM connect_pending WHERE state_hash=?", (p["state_hash"],))   # single use
    if not p:
        raise Problem("This sign-in isn't one waiting here: it was used already, or started somewhere else. Start "
                      "it again from Connected sources.", 400)
    if p["session_id"] != s["sid"]:
        raise Problem("This sign-in was started in another browser session, so it isn't accepted here. Start it "
                      "again from Connected sources.", 400)
    if p["expires"] <= dbm.now():
        raise Problem("The sign-in took longer than 10 minutes. Start it again.", 400)
    if role_check:
        role_check(p["profile_id"])
    if error:
        e = str(error)[:60]
        if e == "access_denied":
            raise Problem("You didn't allow access at the vendor, so nothing was connected.", 400)
        raise Problem(f"The vendor refused the sign-in ({e}" + (f": {str(error_description)[:200]}" if error_description else "")
                      + "). Nothing was connected.", 400)
    if not code or len(code) > 4000:
        raise Problem("The vendor sent no authorization code back.", 400)
    r = db.execute("SELECT * FROM smart_clients WHERE id=?", (p["client_id"],)).fetchone()
    if not r:
        raise Problem("This source was removed meanwhile.", 409)
    pid = p["profile_id"]
    allowed = hosts_for(r)
    if smart.host_of(p["token_url"]) not in allowed:
        raise Problem("This source's token endpoint isn't one an admin tested.", 409)
    http = smart.Client(outside.for_source(db, r["id"], "Connected source: sign-in", pid, s, ip))
    secret = crypto.dec(r["secret"]) if r["secret"] else None
    try:
        tok = smart.exchange_code(http, {"token_endpoint": p["token_url"]}, r["client_id"], secret, code,
                                  crypto.dec(p["verifier"]), p["redirect_uri"])
    except smart.SmartError as e:
        raise Problem("The vendor didn't accept the sign-in: " + str(e) + " Nothing was connected.", 502 if e.kind == "unavailable" else 400) from None
    patient = tok.get("patient")
    if not isinstance(patient, str) or not patient.strip() or len(patient) > 200:
        raise Problem("The vendor didn't say which patient signed in (no patient in its answer). Nothing was connected.", 400)
    _check_audience(tok, r["issuer"], r["client_id"])
    granted = " ".join(str(tok.get("scope") or p["scopes"]).split())
    now = dbm.now()
    cred = {"access": tok["access_token"], "token_url": p["token_url"]}
    expires = now + int(tok.get("expires_in") or 300)
    if r["auth"] == "dynamic":
        conf = _discovery(r) or {}
        reg = conf.get("registration_endpoint")
        if not reg:
            raise Problem("This source's discovery lists no registration endpoint, which Epic dynamic registration "
                          "needs. Ask an admin to change how it signs in.", 409)
        pem, jwk = smart.new_key()
        try:
            dyn = smart.register(http, reg, r["client_id"], tok["access_token"], jwk)
            tok2 = smart.jwt_grant(http, p["token_url"], dyn, pem, jwk["kid"])
        except smart.SmartError as e:
            raise Problem("Signed in, but the vendor didn't register this install's key: " + str(e), 502) from None
        cred.update(access=tok2["access_token"], dyn_client=dyn, key=pem, kid=jwk["kid"])
        expires, kind = now + int(tok2.get("expires_in") or 300), "jwt"
    elif isinstance(tok.get("refresh_token"), str):
        cred["refresh"], kind = tok["refresh_token"], "refresh"
    else:
        kind = "access"
    host = smart.host_of(r["issuer"])
    with dbm.transaction(db):
        cid = p["connection_id"]
        if cid is None:   # the same patient at the same source again: that connection, not a second one
            for c in db.execute("SELECT id, patient FROM connections WHERE profile_id=? AND issuer=? AND state != 'disconnected'",
                                (pid, r["issuer"])).fetchall():
                if crypto.dec(c["patient"]) == patient:
                    cid = c["id"]
        if cid is not None:
            db.execute("""UPDATE connections SET client_id=?, patient=?, credential=?, secret=?, scopes=?, expires=?,
                          state='active', error_kind=NULL, last_error=NULL, failures=0,
                          next_sync=CASE WHEN cursor IS NOT NULL OR first_job IS NULL THEN ? ELSE next_sync END WHERE id=?""",
                       (r["id"], crypto.enc(patient), kind, _jenc(cred), granted, expires, now, cid))
            reconnected = True
        else:
            cid = db.execute("""INSERT INTO connections(profile_id, client_id, source, issuer, fhir_base, patient, credential,
                                secret, scopes, expires, state, next_sync, created, created_by)
                                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                             (pid, r["id"], r["name"], r["issuer"], r["issuer"], crypto.enc(patient), kind, _jenc(cred),
                              granted, expires, now, now, s["id"])).lastrowid
            reconnected = False
        activity.log(db, "connect", s, pid, {"source": r["name"], "host": host, **({"reconnect": True} if reconnected else {})}, ip)
    sync.wake()
    return {"profile": pid, "connection": cid, "reconnected": reconnected}


# ---------------------------------------------------------------- tokens, for a pull

class Tokens:
    """An access token for a connection, renewed when it's about to run out or the vendor refuses it."""

    def __init__(self, db, conn_id, http):
        self.db, self.id, self.http = db, conn_id, http
        c = db.execute("SELECT c.*, k.client_id AS app_id, k.secret AS app_secret FROM connections c "
                       "LEFT JOIN smart_clients k ON k.id = c.client_id WHERE c.id=?", (conn_id,)).fetchone()
        if not c or c["state"] != "active" or not c["secret"]:
            raise smart.AccessEnded("This connection isn't active.")
        self.kind, self.cred, self.expires = c["credential"], _jdec(c["secret"]), c["expires"] or 0
        self.app_id, self.app_secret = c["app_id"], crypto.dec(c["app_secret"]) if c["app_secret"] else None
        if self.kind in ("refresh", "access") and not self.app_id:
            raise smart.AccessEnded("This connection's source was removed.")

    def token(self):
        if self.expires - EARLY <= time.time():
            self.renew()
        return self.cred["access"]

    def renew(self):
        if self.kind == "refresh":
            tok = smart.refresh(self.http, self.cred["token_url"], self.app_id, self.app_secret, self.cred["refresh"])
            if isinstance(tok.get("refresh_token"), str):   # rotated: the old one stops working now
                self.cred["refresh"] = tok["refresh_token"]
        elif self.kind == "jwt":
            tok = smart.jwt_grant(self.http, self.cred["token_url"], self.cred["dyn_client"], self.cred["key"], self.cred["kid"])
        else:
            raise smart.AccessEnded("This source gave no refresh token, and its access has run out.")
        self.cred["access"] = tok["access_token"]
        self.expires = int(time.time()) + int(tok.get("expires_in") or 300)
        # saved at once, on its own: a rotated refresh token must not be lost if the pull fails later
        n = self.db.execute("UPDATE connections SET secret=?, expires=? WHERE id=? AND state='active'",
                            (_jenc(self.cred), self.expires, self.id)).rowcount
        if not n:
            raise smart.AccessEnded("This connection was disconnected meanwhile.")


def needs_reconnect(db, conn_id, message):
    """The vendor ended access: keep the connection (and its records), drop the access token, ask to sign in again."""
    with dbm.transaction(db):
        c = db.execute("SELECT profile_id, source, state FROM connections WHERE id=?", (conn_id,)).fetchone()
        if not c or c["state"] != "active":
            return
        db.execute("""UPDATE connections SET state='needs reconnect', error_kind='access', last_error=?, next_sync=NULL
                      WHERE id=?""", (crypto.enc(message), conn_id))
        activity.log(db, "disconnect", SYNC_ACTOR, c["profile_id"], {"source": c["source"], "ended": True})


# ---------------------------------------------------------------- reading and disconnecting

def describe(db, c):
    job = db.execute("SELECT id, state FROM jobs WHERE id=?", (c["first_job"],)).fetchone() if c["first_job"] else None
    if c["state"] != "active":
        phase = c["state"]
    elif c["cursor"] is not None:
        phase = "syncing"
    elif job and job["state"] in ("failed", "discarded"):
        phase = "first pull " + job["state"]
    elif job:
        phase = "first pull preview" if job["state"] == "preview" else "first pull saving"
    else:
        phase = "first pull"
    return {"id": c["id"], "source": c["source"], "issuer": c["issuer"], "host": smart.host_of(c["issuer"]),
            "vendor_id": c["client_id"], "state": c["state"], "phase": phase, "credential": c["credential"],
            "scopes": c["scopes"], "last_sync": c["last_sync"], "next_sync": c["next_sync"], "error_kind": c["error_kind"],
            "error": crypto.dec(c["last_error"]) if c["last_error"] else None, "report": _jdec(c["report"]),
            "first_job": c["first_job"], "first_job_state": job["state"] if job else None,
            "created": c["created"], "disconnected": c["disconnected"],
            "imports": db.execute("SELECT COUNT(*) FROM imports WHERE connection_id=?", (c["id"],)).fetchone()[0]}


def for_profile(db, pid):
    return [describe(db, c) for c in db.execute("SELECT * FROM connections WHERE profile_id=? ORDER BY state='disconnected', id DESC", (pid,))]


def get(db, pid, cid):
    c = db.execute("SELECT * FROM connections WHERE id=? AND profile_id=?", (cid, pid)).fetchone()
    if not c:
        raise Problem("No such connection.", 404)
    return c


def sync_now(db, pid, cid):
    """Pull now: a due time of now, and the sync thread woken. A first pull that was cancelled or failed starts over."""
    from . import sync
    c = get(db, pid, cid)
    if c["state"] != "active":
        raise Problem("This connection needs signing in again first." if c["state"] == "needs reconnect"
                      else "This source is disconnected.", 409)
    job = db.execute("SELECT state FROM jobs WHERE id=?", (c["first_job"],)).fetchone() if c["first_job"] else None
    if c["cursor"] is None and job and job["state"] in ("queued", "running", "preview"):
        raise Problem("The first pull is waiting for you to look at its preview and save it.", 409)
    db.execute("UPDATE connections SET next_sync=?, first_job=CASE WHEN cursor IS NULL THEN NULL ELSE first_job END WHERE id=?",
               (dbm.now(), cid))
    sync.wake()
    return describe(db, get(db, pid, cid))


def disconnect(db, who, pid, cid, remove_records=False, ip=None):
    """Revoke where the vendor can, delete the credentials, and keep or remove the records. Returns
    {"revoked": True/False/None (no revocation endpoint), "removed": imports undone}."""
    from . import history
    c = get(db, pid, cid)
    revoked = None
    if c["state"] != "disconnected" and c["secret"]:
        r = db.execute("SELECT * FROM smart_clients WHERE id=?", (c["client_id"],)).fetchone() if c["client_id"] else None
        url = (_discovery(r) or {}).get("revocation_endpoint") if r else None
        cred = _jdec(c["secret"])
        if url:
            http = smart.Client(outside.for_connection(db, cid, "Connected source: disconnect", who, ip))
            app = (cred.get("dyn_client"), None) if c["credential"] == "jwt" else (r["client_id"], crypto.dec(r["secret"]) if r["secret"] else None)
            token, hint = (cred["refresh"], "refresh_token") if cred.get("refresh") else (cred["access"], "access_token")
            try:
                revoked = smart.revoke(http, url, app[0], app[1], token, hint)
            except smart.SmartError:
                revoked = False
    from . import sync
    with dbm.transaction(db):
        db.execute("""UPDATE connections SET state='disconnected', secret=NULL, patient=NULL, expires=NULL, next_sync=NULL,
                      pending_cursor=NULL, disconnected=? WHERE id=?""", (dbm.now(), cid))
        db.execute("DELETE FROM connect_pending WHERE connection_id=?", (cid,))
    sync.forget_store(pid, cid)   # the resources it read, kept for rebuilding lab days
    removed = 0
    if remove_records:
        for (iid,) in db.execute("SELECT id FROM imports WHERE connection_id=? ORDER BY id DESC", (cid,)).fetchall():
            try:
                history.undo(db, pid, iid, who, ip)
                removed += 1
            except history.Refused:
                pass
    activity.log(db, "disconnect", who, pid, {"source": c["source"], "revoked": revoked, "removed": removed}, ip)
    return {"revoked": revoked, "removed": removed}
