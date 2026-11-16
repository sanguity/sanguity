"""A fake SMART on FHIR server for tests (#98): SMART discovery, OAuth 2 authorization code with PKCE, refresh,
revocation, optional Epic-style dynamic client registration with the JWT bearer grant, FHIR R4 reads and searches
with paging and `_lastUpdated`, injected errors, and a record of every request. Standard library only; it runs in
a thread on a free 127.0.0.1 port. Fictional patients only (tests/fixtures/fakefhir/). See README.md beside this file.
"""
import base64, copy, hashlib, hmac, http.client, json, os, re, secrets, threading, time, urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "fakefhir")
PAYER_FIXTURES = os.path.join(FIXTURES, "payer")   # an insurer's patient-access API (#92): FakeFHIR(fixtures=PAYER_FIXTURES)

# Where each patient-compartment resource names its patient; types not listed here aren't in a patient's compartment
# (Organization, Practitioner, Location, Medication...) and anyone with the scope may read them.
PATIENT_FIELD = {
    "Observation": "subject", "Condition": "subject", "MedicationRequest": "subject", "Encounter": "subject",
    "DocumentReference": "subject", "DiagnosticReport": "subject", "MedicationStatement": "subject",
    "Procedure": "subject", "ExplanationOfBenefit": "patient", "Immunization": "patient",
    "AllergyIntolerance": "patient", "Coverage": "beneficiary",
}
FIXTURE_UPDATED = "2025-10-01T00:00:00.000Z"   # meta.lastUpdated of everything loaded from fixtures
JWT_BEARER = "urn:ietf:params:oauth:grant-type:jwt-bearer"
DIGEST_INFO = {   # PKCS#1 v1.5 DigestInfo prefixes (RFC 8017 §9.2)
    "RS256": (hashlib.sha256, bytes.fromhex("3031300d060960864801650304020105000420")),
    "RS384": (hashlib.sha384, bytes.fromhex("3041300d060960864801650304020205000430")),
    "RS512": (hashlib.sha512, bytes.fromhex("3051300d060960864801650304020305000440")),
}
VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")


# ---------------------------------------------------------------------------------------------------------- helpers

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pkce() -> tuple:
    """A (code_verifier, code_challenge) pair for S256, for tests that play the client."""
    verifier = b64url(secrets.token_bytes(32))
    return verifier, b64url(hashlib.sha256(verifier.encode()).digest())


def iso_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def parse_instant(text: str) -> tuple:
    """A FHIR date or instant as a [start, end) range in epoch milliseconds."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        start = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
        return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    ms = int(round(t.timestamp() * 1000))
    return ms, ms + 1


def rsa_verify(jwk: dict, message: bytes, signature: bytes, alg: str) -> bool:
    """RSASSA-PKCS1-v1_5 verification with only the standard library."""
    if alg not in DIGEST_INFO or jwk.get("kty") != "RSA":
        return False
    n, e = int.from_bytes(b64url_decode(jwk["n"]), "big"), int.from_bytes(b64url_decode(jwk["e"]), "big")
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    digest, prefix = DIGEST_INFO[alg]
    t = prefix + digest(message).digest()
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big"), expected)


def scope_allows(scopes, rtype: str, op: str) -> bool:
    """Whether SMART scopes (v1 `.read`/`.*`, v2 `.rs`/`.cruds`, with or without a `?query`) allow `op` ('r' read,
    's' search) on `rtype`, in patient or user context."""
    for s in scopes:
        m = re.fullmatch(r"(patient|user|system)/([A-Za-z*]+)\.([a-z*]+)(\?.*)?", s)
        if not m or m.group(2) not in (rtype, "*"):
            continue
        perm = m.group(3)
        if perm in ("read", "*") or (perm not in ("write",) and re.fullmatch(r"c?r?u?d?s?", perm) and op in perm):
            return True
    return False


def outcome(code: str, message: str) -> dict:
    return {"resourceType": "OperationOutcome", "issue": [{"severity": "error", "code": code, "diagnostics": message}]}


def ref_id(value: str) -> str:
    """'pat-1', 'Patient/pat-1' or 'http://host/fhir/Patient/pat-1' -> 'pat-1'."""
    return value.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------------------------------------- state

@dataclass
class Client:
    client_id: str
    redirect_uris: list
    secret: str = None            # set = a confidential client: the token and revoke endpoints require it
    jwks: dict = None             # dynamic clients (Epic-style): the public keys their JWT assertions are signed with
    software_id: str = None       # dynamic clients: the client they were registered from
    patient: str = None           # dynamic clients: bound to one patient
    grant: "Grant" = None         # dynamic clients: the sign-in they came from (revoking it ends their access)


@dataclass
class Grant:
    """One sign-in: everything issued from one authorization code (or one dynamic client)."""
    id: str
    client_id: str
    patient: str
    scopes: list
    revoked: bool = False


@dataclass
class Token:
    value: str
    kind: str                     # "access" | "refresh"
    grant: Grant
    expires: float = None         # fake-clock seconds; None = never
    revoked: bool = False
    rotated: bool = False         # a refresh token already exchanged under rotation


@dataclass
class Fault:
    status: int = None            # None = answer normally after `delay`
    times: int = 1                # None = every matching request until clear_faults()
    path: str = None              # only requests whose path starts with this
    retry_after: int = None
    delay: float = 0.0


@dataclass
class Request:
    """One request the fake received. The Authorization header's value is never kept, only whether it was there."""
    method: str
    host: str                     # the Host header, as the client sent it
    path: str
    query: dict
    authorization: bool
    status: int = None
    time: float = None            # fake-clock seconds


@dataclass
class Redirect:
    """Where the authorize endpoint sent the "browser"."""
    status: int
    location: str = None
    code: str = None
    state: str = None
    error: str = None
    error_description: str = None
    body: str = None              # when it refused without redirecting (unknown client, unregistered redirect_uri)


# ---------------------------------------------------------------------------------------------------------- server

class FakeFHIR:
    """An in-process fake SMART on FHIR R4 server. Use as a context manager, or call start() and stop().

    Settings are attributes, read on every request, so a test can change them while it runs."""

    def __init__(self, *, patients=None, fixtures=FIXTURES, access_ttl=3600, refresh_ttl=None, refresh="rotate",
                 code_ttl=60, offline_for_public=True, require_aud=True, revocation=True, dynamic_registration=False,
                 capabilities=None, scopes_supported=None, smart_config=None, page_size=50, max_count=200):
        self.access_ttl, self.refresh_ttl, self.refresh, self.code_ttl = access_ttl, refresh_ttl, refresh, code_ttl
        self.offline_for_public, self.require_aud, self.revocation = offline_for_public, require_aud, revocation
        self.dynamic_registration, self.capabilities, self.scopes_supported = dynamic_registration, capabilities, scopes_supported
        self.smart_config, self.page_size, self.max_count = smart_config or {}, page_size, max_count
        self.signin_patient, self.signin_approve = None, True   # who "signs in" at the authorize endpoint
        self._lock = threading.RLock()
        self._offset = 0.0
        self._last_ms = 0
        self._res, self._owner, self._binary = {}, {}, {}
        self.clients, self._codes, self._tokens, self._jtis = {}, {}, {}, set()
        self._faults, self._requests = [], []
        self._httpd = self._thread = None
        for name in patients or sorted(f[:-5] for f in os.listdir(fixtures) if f.endswith(".json")):
            self._load(os.path.join(fixtures, name + ".json"))
        self._last_ms = max(self._last_ms, int(self.now() * 1000))

    # -- lifecycle

    def start(self):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, args=(0.05,), name="fakefhir", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def fhir_base(self) -> str:
        """The FHIR base URL, which is also the issuer and the `aud` a client must send."""
        return self.url + "/fhir"

    authorize_url = property(lambda self: self.url + "/oauth2/authorize")
    token_url = property(lambda self: self.url + "/oauth2/token")
    revoke_url = property(lambda self: self.url + "/oauth2/revoke")
    register_url = property(lambda self: self.url + "/oauth2/register")

    # -- clock: real time plus an offset tests can move forward

    def now(self) -> float:
        return time.time() + self._offset

    def advance(self, seconds: float):
        """Move the fake's clock forward (tokens and codes expire by it; new resources are stamped by it)."""
        with self._lock:
            self._offset += seconds

    def _stamp(self) -> str:
        """A meta.lastUpdated strictly later than every one handed out before, Bundle.meta.lastUpdated included."""
        with self._lock:
            self._last_ms = max(int(self.now() * 1000), self._last_ms + 1)
            return iso_ms(self._last_ms)

    # -- clients and sign-in

    def register_client(self, client_id=None, redirect_uris=("http://127.0.0.1/connect/return",), secret=None) -> Client:
        """Register an app, as its owner would on the vendor's developer site. With `secret` it's confidential."""
        c = Client(client_id or "client-" + secrets.token_hex(4), list(redirect_uris), secret)
        with self._lock:
            self.clients[c.client_id] = c
        return c

    def authorize(self, url: str, *, patient=None, approve=True, scopes=None) -> Redirect:
        """Be the patient's browser: open the authorize URL the app built, sign in as `patient` (default
        `signin_patient`, else the first fixture patient), approve or deny, optionally untick scopes so only `scopes`
        are granted, and return where the fake redirected to. Redirects aren't followed."""
        extra = {"fake_decision": "approve" if approve else "deny"}
        if patient:
            extra["fake_patient"] = patient
        if scopes is not None:
            extra["fake_scopes"] = " ".join(scopes)
        u = urllib.parse.urlsplit(url)
        query = u.query + ("&" if u.query else "") + urllib.parse.urlencode(extra)
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        try:
            conn.request("GET", f"{u.path}?{query}")
            resp = conn.getresponse()
            body = resp.read().decode()
            location = resp.getheader("Location")
        finally:
            conn.close()
        r = Redirect(resp.status, location)
        if location:
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query))
            r.code, r.state, r.error, r.error_description = q.get("code"), q.get("state"), q.get("error"), q.get("error_description")
        else:
            r.body = body
        return r

    def revoke_all(self, patient=None):
        """What a patient revoking the app in their portal does: every grant (for `patient`, or everyone) ends."""
        with self._lock:
            for t in self._tokens.values():
                if patient is None or t.grant.patient == patient:
                    t.grant.revoked = True

    def expire_access_tokens(self):
        """Expire every access token now, leaving refresh tokens alone."""
        with self._lock:
            for t in self._tokens.values():
                if t.kind == "access":
                    t.expires = self.now() - 1

    def token_info(self, value: str):
        """The fake's record of a token (kind, grant with scopes and patient, expiry), or None."""
        return self._tokens.get(value)

    # -- data

    def add(self, resource: dict, patient=None) -> dict:
        """Add a resource now, stamped with a new meta.lastUpdated. `patient` fills in its subject/patient
        reference. Returns a copy of what's stored."""
        r = copy.deepcopy(resource)
        rtype = r["resourceType"]
        r.setdefault("id", rtype.lower() + "-" + secrets.token_hex(4))
        if patient and rtype in PATIENT_FIELD:
            r[PATIENT_FIELD[rtype]] = {"reference": f"Patient/{patient}"}
        with self._lock:
            r["meta"] = dict(r.get("meta") or {}, versionId="1", lastUpdated=self._stamp())
            self._store(r)
            return copy.deepcopy(r)

    def update(self, rtype: str, rid: str, changes: dict = None) -> dict:
        """Change top-level fields of a stored resource (a value of None removes the field); bumps versionId and
        meta.lastUpdated even when `changes` is empty."""
        with self._lock:
            r = self._res[(rtype, rid)]
            for k, v in (changes or {}).items():
                if v is None:
                    r.pop(k, None)
                else:
                    r[k] = copy.deepcopy(v)
            meta = r.setdefault("meta", {})
            meta["versionId"] = str(int(meta.get("versionId", "1")) + 1)
            meta["lastUpdated"] = self._stamp()
            self._owner[(rtype, rid)] = self._owner_of(r)
            return copy.deepcopy(r)

    def delete(self, rtype: str, rid: str):
        with self._lock:
            self._res.pop((rtype, rid), None)
            self._owner.pop((rtype, rid), None)

    def get(self, rtype: str, rid: str):
        with self._lock:
            r = self._res.get((rtype, rid))
            return copy.deepcopy(r) if r else None

    def resources(self, rtype=None, patient=None) -> list:
        with self._lock:
            return [copy.deepcopy(r) for (t, i), r in self._res.items()
                    if (rtype is None or t == rtype) and (patient is None or self._owner.get((t, i)) == patient)]

    @property
    def patients(self) -> list:
        return sorted(i for (t, i) in self._res if t == "Patient")

    # -- errors

    def fail(self, status=429, *, times=1, path=None, retry_after=None, delay=0.0):
        """Answer the next `times` requests (under `path`, if given) with `status`. 429 and 503 get a Retry-After
        header (`retry_after`, default 1)."""
        if retry_after is None and status in (429, 503):
            retry_after = 1
        with self._lock:
            self._faults.append(Fault(status, times, path, retry_after, delay))

    def slow(self, seconds, *, times=1, path=None):
        """Delay the next `times` requests (under `path`) by `seconds`, then answer normally."""
        with self._lock:
            self._faults.append(Fault(None, times, path, None, seconds))

    def clear_faults(self):
        with self._lock:
            self._faults.clear()

    def _fault_for(self, path):
        with self._lock:
            for f in self._faults:
                if (f.path is None or path.startswith(f.path)) and (f.times is None or f.times > 0):
                    if f.times is not None:
                        f.times -= 1
                    return f
        return None

    # -- request log

    @property
    def requests(self) -> list:
        with self._lock:
            return list(self._requests)

    def hosts(self) -> set:
        return {r.host for r in self.requests}

    def clear_requests(self):
        with self._lock:
            self._requests.clear()

    # -- loading and storing

    def _load(self, path):
        with open(path, encoding="utf-8") as f:
            spec = json.load(f)
        base = os.path.dirname(path)
        resources, urns = [], {}
        if spec.get("bundle"):
            with open(os.path.join(base, spec["bundle"]), encoding="utf-8") as f:
                bundle = json.load(f)
            for e in bundle.get("entry", []):
                r = e["resource"]
                resources.append(r)
                if e.get("fullUrl"):
                    urns[e["fullUrl"]] = f"{r['resourceType']}/{r['id']}"
        resources += copy.deepcopy(spec.get("resources", []))
        patient = next(r["id"] for r in resources if r["resourceType"] == "Patient")
        # relative attachment URLs are read from the bundle's folder (or the fixture's, for its own resources)
        attachments = os.path.dirname(os.path.join(base, spec["bundle"])) if spec.get("bundle") else base

        def relink(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k == "reference" and isinstance(v, str) and v in urns:
                        node[k] = urns[v]
                    else:
                        relink(v)
            elif isinstance(node, list):
                for v in node:
                    relink(v)
        relink(resources)
        for r in resources:
            rtype = r["resourceType"]
            if rtype in PATIENT_FIELD and PATIENT_FIELD[rtype] not in r:
                r[PATIENT_FIELD[rtype]] = {"reference": f"Patient/{patient}"}
            r["meta"] = dict(r.get("meta") or {}, versionId="1", lastUpdated=FIXTURE_UPDATED)
            self._binaries(r, patient, attachments)
            self._store(r)
        self._last_ms = max(self._last_ms, parse_instant(FIXTURE_UPDATED)[0])

    def _binaries(self, r, patient, attachments):
        """Move a DocumentReference's or DiagnosticReport's attachment content into Binary resources, linked by
        `url: Binary/<id>`, as Epic and others serve them."""
        if r["resourceType"] == "DocumentReference":
            atts = [c.get("attachment", {}) for c in r.get("content", [])]
        elif r["resourceType"] == "DiagnosticReport":
            atts = r.get("presentedForm", [])
        else:
            return
        for i, a in enumerate(atts):
            data = None
            if "data" in a:
                data = base64.b64decode(a.pop("data"))
            elif a.get("url") and not re.match(r"^(https?:|Binary/)", a["url"]) and attachments:
                with open(os.path.join(attachments, a["url"]), "rb") as f:
                    data = f.read()
            if data is None:
                continue
            bid = f"bin-{r['id']}-{i}"
            a["url"], a["size"] = f"Binary/{bid}", len(data)
            b = {"resourceType": "Binary", "id": bid, "contentType": a.get("contentType", "application/octet-stream"),
                 "data": base64.b64encode(data).decode(), "meta": {"versionId": "1", "lastUpdated": FIXTURE_UPDATED}}
            self._binary[bid] = data
            self._res[("Binary", bid)] = b
            self._owner[("Binary", bid)] = patient

    def _owner_of(self, r):
        rtype = r["resourceType"]
        if rtype == "Patient":
            return r["id"]
        if rtype == "Binary":
            return self._owner.get(("Binary", r["id"]))
        ref = (r.get(PATIENT_FIELD.get(rtype, "")) or {}).get("reference")
        return ref_id(ref) if ref else None

    def _store(self, r):
        key = (r["resourceType"], r["id"])
        if r["resourceType"] == "Binary" and "data" in r:
            self._binary[r["id"]] = base64.b64decode(r["data"])
        self._res[key] = r
        self._owner[key] = self._owner_of(r)

    # -- tokens

    def _issue(self, grant, kind, ttl):
        t = Token(secrets.token_urlsafe(24), kind, grant, None if ttl is None else self.now() + ttl)
        self._tokens[t.value] = t
        return t

    def _live(self, value, kind):
        t = self._tokens.get(value or "")
        if not t or t.kind != kind or t.revoked or t.grant.revoked or (t.expires is not None and t.expires <= self.now()):
            return None
        return t

    def smart_configuration(self) -> dict:
        caps = self.capabilities or (
            ["launch-standalone", "client-public", "client-confidential-symmetric", "context-standalone-patient",
             "permission-offline", "permission-online", "permission-patient", "permission-v1", "permission-v2"]
            + (["client-confidential-asymmetric"] if self.dynamic_registration else []))
        cfg = {
            "issuer": self.fhir_base,
            "authorization_endpoint": self.authorize_url,
            "token_endpoint": self.token_url,
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"]
                                                    + (["private_key_jwt"] if self.dynamic_registration else []),
            "grant_types_supported": ["authorization_code", "refresh_token"] + ([JWT_BEARER] if self.dynamic_registration else []),
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": self.scopes_supported or ["launch/patient", "openid", "fhirUser", "offline_access",
                                                          "online_access", "patient/*.read", "patient/*.rs"],
            "capabilities": caps,
        }
        if self.revocation:
            cfg["revocation_endpoint"] = self.revoke_url
        if self.dynamic_registration:
            cfg["registration_endpoint"] = self.register_url
        for k, v in self.smart_config.items():
            if v is None:
                cfg.pop(k, None)
            else:
                cfg[k] = v
        return cfg


# ---------------------------------------------------------------------------------------------------------- HTTP

def _handler(fake: FakeFHIR):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        # -- plumbing

        def _handle(self, method):
            u = urllib.parse.urlsplit(self.path)
            self.rpath = u.path
            self.query = urllib.parse.parse_qs(u.query, keep_blank_values=True)
            self.record = Request(method, self.headers.get("Host", ""), u.path,
                                  {k: v[0] if len(v) == 1 else v for k, v in self.query.items()},
                                  "Authorization" in self.headers, time=fake.now())
            with fake._lock:
                fake._requests.append(self.record)
            n = int(self.headers.get("Content-Length") or 0)
            self.body = self.rfile.read(n) if n else b""
            fault = fake._fault_for(u.path)
            if fault:
                if fault.delay:
                    time.sleep(fault.delay)
                if fault.status:
                    headers = {"Retry-After": str(fault.retry_after)} if fault.retry_after is not None else {}
                    return self._json(fault.status, outcome("throttled" if fault.status == 429 else "transient",
                                                            f"injected {fault.status}"), headers)
            try:
                self._route(method, u.path)
            except Exception as e:   # a bug in the fake shows up as a 500 with the reason, not a hung test
                self._json(500, outcome("exception", f"fakefhir: {type(e).__name__}: {e}"))

        def _send(self, status, body: bytes, ctype, headers=None):
            self.record.status = status
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj, headers=None, ctype="application/json"):
            self._send(status, json.dumps(obj).encode(), ctype, headers)

        def _fhir(self, status, obj, headers=None):
            self._json(status, obj, headers, "application/fhir+json")

        def _redirect(self, location):
            self.record.status = 302
            self.send_response(302)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _form(self):
            if self.command == "POST":
                return {k: v[0] for k, v in urllib.parse.parse_qs(self.body.decode(), keep_blank_values=True).items()}
            return {k: v[0] for k, v in self.query.items()}

        def _route(self, method, path):
            if path == "/fhir/.well-known/smart-configuration" and method == "GET":
                return self._json(200, fake.smart_configuration())
            if path == "/fhir/metadata" and method == "GET":
                return self._fhir(200, self._capability_statement())
            if path == "/oauth2/authorize":
                return self._authorize()
            if path == "/oauth2/token" and method == "POST":
                return self._token()
            if path == "/oauth2/revoke" and method == "POST" and fake.revocation:
                return self._revoke()
            if path == "/oauth2/register" and method == "POST" and fake.dynamic_registration:
                return self._register()
            if path.startswith("/fhir/") and method == "GET":
                return self._fhir_get(path[len("/fhir/"):].strip("/").split("/"))
            self._json(404, {"error": "not_found", "path": path})

        # -- OAuth

        def _oauth_error(self, status, error, description, headers=None):
            self._json(status, {"error": error, "error_description": description},
                       dict({"Cache-Control": "no-store"}, **(headers or {})))

        def _authorize(self):
            p = self._form()
            client = fake.clients.get(p.get("client_id", ""))
            if not client:
                return self._oauth_error(400, "invalid_client", "unknown client_id (not redirected)")
            redirect_uri = p.get("redirect_uri", "")
            if redirect_uri not in client.redirect_uris:   # exact string match, as SMART and every vendor require
                return self._oauth_error(400, "invalid_request", "redirect_uri isn't registered for this client (not redirected)")

            def back(**params):
                if p.get("state") is not None:
                    params["state"] = p["state"]
                sep = "&" if "?" in redirect_uri else "?"
                return self._redirect(redirect_uri + sep + urllib.parse.urlencode(params))

            if p.get("response_type") != "code":
                return back(error="unsupported_response_type", error_description="only response_type=code")
            if not p.get("code_challenge"):
                return back(error="invalid_request", error_description="PKCE code_challenge is required")
            if p.get("code_challenge_method") != "S256":
                return back(error="invalid_request", error_description="code_challenge_method must be S256")
            if fake.require_aud and p.get("aud", "").rstrip("/") != fake.fhir_base:
                return back(error="invalid_request", error_description=f"aud must be {fake.fhir_base}")
            requested = p.get("scope", "").split()
            if not requested:
                return back(error="invalid_scope", error_description="no scope requested")
            patient = p.get("fake_patient") or fake.signin_patient or fake.patients[0]
            if ("Patient", patient) not in fake._res:
                return self._oauth_error(400, "invalid_request", f"fakefhir: no fixture patient {patient!r}")
            if p.get("fake_decision", "approve" if fake.signin_approve else "deny") != "approve":
                return back(error="access_denied", error_description="the patient declined")
            granted = [s for s in requested if fake.scopes_supported is None or s in fake.scopes_supported]
            if "fake_scopes" in p:
                keep = set(p["fake_scopes"].split())
                granted = [s for s in granted if s in keep]
            if not client.secret and not fake.offline_for_public:
                granted = [s for s in granted if s != "offline_access"]
            code = secrets.token_urlsafe(24)
            with fake._lock:
                fake._codes[code] = {"client_id": client.client_id, "redirect_uri": redirect_uri, "patient": patient,
                                     "challenge": p["code_challenge"], "scopes": granted,
                                     "expires": fake.now() + fake.code_ttl, "used": False, "grant": None}
            back(code=code)

        def _client(self, form):
            """(client, None) or (None, error already sent)."""
            cid, secret = form.get("client_id"), form.get("client_secret")
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
                    cid, secret = urllib.parse.unquote_plus(cid), urllib.parse.unquote_plus(secret)
                except ValueError:
                    pass
            client = fake.clients.get(cid or "")
            if not client:
                self._oauth_error(401, "invalid_client", "unknown client", {"WWW-Authenticate": 'Basic realm="fakefhir"'})
                return None
            if client.secret and not hmac.compare_digest((secret or "").encode(), client.secret.encode()):
                self._oauth_error(401, "invalid_client", "client authentication failed",
                                  {"WWW-Authenticate": 'Basic realm="fakefhir"'})
                return None
            return client

        def _token_response(self, grant, refresh_value=None):
            access = fake._issue(grant, "access", fake.access_ttl)
            body = {"access_token": access.value, "token_type": "Bearer", "expires_in": fake.access_ttl,
                    "scope": " ".join(grant.scopes), "patient": grant.patient}
            if refresh_value:
                body["refresh_token"] = refresh_value
            self._json(200, body, {"Cache-Control": "no-store", "Pragma": "no-cache"})

        def _token(self):
            form = self._form()
            gt = form.get("grant_type")
            if gt == JWT_BEARER and fake.dynamic_registration:
                return self._jwt_bearer(form)
            client = self._client(form)
            if not client:
                return
            with fake._lock:
                if gt == "authorization_code":
                    c = fake._codes.get(form.get("code", ""))
                    if not c or c["expires"] <= fake.now():
                        return self._oauth_error(400, "invalid_grant", "unknown or expired code")
                    if c["used"]:   # a replayed code: refuse it and end what it was exchanged for (RFC 6749 §4.1.2)
                        c["grant"].revoked = True
                        return self._oauth_error(400, "invalid_grant", "code already used")
                    if c["client_id"] != client.client_id:
                        return self._oauth_error(400, "invalid_grant", "code was issued to another client")
                    if form.get("redirect_uri") != c["redirect_uri"]:
                        return self._oauth_error(400, "invalid_grant", "redirect_uri doesn't match the authorize request")
                    verifier = form.get("code_verifier", "")
                    if not VERIFIER.match(verifier):
                        return self._oauth_error(400, "invalid_grant", "code_verifier missing or malformed")
                    if not hmac.compare_digest(b64url(hashlib.sha256(verifier.encode()).digest()), c["challenge"]):
                        return self._oauth_error(400, "invalid_grant", "code_verifier doesn't match code_challenge")
                    grant = Grant(secrets.token_hex(6), client.client_id, c["patient"], c["scopes"])
                    c["used"], c["grant"] = True, grant
                    refresh = None
                    if {"offline_access", "online_access"} & set(grant.scopes):
                        refresh = fake._issue(grant, "refresh", fake.refresh_ttl).value
                    return self._token_response(grant, refresh)
                if gt == "refresh_token":
                    t = fake._tokens.get(form.get("refresh_token", ""))
                    if t and t.kind == "refresh" and t.rotated:   # reuse of a rotated token: end the whole grant
                        t.grant.revoked = True
                        return self._oauth_error(400, "invalid_grant", "refresh token already used")
                    if not fake._live(form.get("refresh_token"), "refresh"):
                        return self._oauth_error(400, "invalid_grant", "refresh token unknown, expired or revoked")
                    if t.grant.client_id != client.client_id:
                        return self._oauth_error(400, "invalid_grant", "refresh token was issued to another client")
                    if form.get("scope"):
                        narrower = form["scope"].split()
                        if not set(narrower) <= set(t.grant.scopes):
                            return self._oauth_error(400, "invalid_scope", "can't widen scopes on refresh")
                        t.grant.scopes = narrower
                    if fake.refresh == "rotate":
                        t.rotated = True
                        value = fake._issue(t.grant, "refresh", fake.refresh_ttl).value
                    else:
                        value = t.value if fake.refresh == "same" else None   # "omit": keep using the original
                    return self._token_response(t.grant, value)
            self._oauth_error(400, "unsupported_grant_type", f"grant_type {gt!r} isn't supported")

        def _revoke(self):
            form = self._form()
            client = self._client(form)
            if not client:
                return
            with fake._lock:
                t = fake._tokens.get(form.get("token", ""))
                if t and t.grant.client_id == client.client_id:
                    if t.kind == "refresh":
                        t.grant.revoked = True   # RFC 7009 §2.1: and the access tokens from the same grant
                    else:
                        t.revoked = True
            self._send(200, b"", "text/plain")   # unknown tokens too (RFC 7009 §2.2)

        def _register(self):
            """Epic-style dynamic client registration (RFC 7591): authorized by an access token from a standalone
            sign-in; registers the app's own public key, bound to that patient."""
            auth = self.headers.get("Authorization", "")
            t = fake._live(auth[7:] if auth.startswith("Bearer ") else None, "access")
            if not t:
                return self._oauth_error(401, "invalid_token", "an access token from a sign-in is required")
            try:
                req = json.loads(self.body or b"{}")
            except ValueError:
                return self._oauth_error(400, "invalid_client_metadata", "body isn't JSON")
            if req.get("software_id") != t.grant.client_id:
                return self._oauth_error(400, "invalid_client_metadata", "software_id must be the client ID that signed in")
            keys = (req.get("jwks") or {}).get("keys") or []
            if not keys or any(k.get("kty") != "RSA" or not k.get("n") or not k.get("e") for k in keys):
                return self._oauth_error(400, "invalid_client_metadata", "jwks must hold RSA public keys")
            c = Client("dyn-" + secrets.token_hex(6), [], None, {"keys": keys}, t.grant.client_id, t.grant.patient, t.grant)
            with fake._lock:
                fake.clients[c.client_id] = c
            self._json(201, {"client_id": c.client_id, "software_id": c.software_id, "jwks": c.jwks,
                             "token_endpoint_auth_method": "private_key_jwt", "grant_types": [JWT_BEARER]})

        def _jwt_bearer(self, form):
            try:
                head_b64, claims_b64, sig_b64 = form.get("assertion", "").split(".")
                head, claims = json.loads(b64url_decode(head_b64)), json.loads(b64url_decode(claims_b64))
                sig = b64url_decode(sig_b64)
            except ValueError:
                return self._oauth_error(400, "invalid_grant", "assertion isn't a JWT")
            client = fake.clients.get(claims.get("iss", ""))
            if not client or not client.jwks or claims.get("sub") != client.client_id:
                return self._oauth_error(400, "invalid_client", "iss/sub isn't a dynamically registered client")
            if form.get("client_id") and form["client_id"] != client.client_id:
                return self._oauth_error(400, "invalid_client", "client_id doesn't match the assertion")
            keys = [k for k in client.jwks["keys"] if not head.get("kid") or k.get("kid") == head["kid"]]
            msg = f"{head_b64}.{claims_b64}".encode()
            if not any(rsa_verify(k, msg, sig, head.get("alg", "")) for k in keys):
                return self._oauth_error(400, "invalid_client", "assertion signature doesn't verify")
            now = fake.now()
            if claims.get("aud") != fake.token_url:
                return self._oauth_error(400, "invalid_grant", f"aud must be {fake.token_url}")
            if not isinstance(claims.get("exp"), (int, float)) or not now < claims["exp"] <= now + 300:
                return self._oauth_error(400, "invalid_grant", "exp must be in the next 5 minutes")
            with fake._lock:
                if not claims.get("jti") or claims["jti"] in fake._jtis:
                    return self._oauth_error(400, "invalid_grant", "jti missing or already used")
                fake._jtis.add(claims["jti"])
                if client.grant.revoked:
                    return self._oauth_error(400, "invalid_grant", "access was revoked")
                self._token_response(client.grant)

        # -- FHIR

        def _capability_statement(self):
            types = sorted({t for (t, _) in fake._res})
            return {"resourceType": "CapabilityStatement", "status": "active", "kind": "instance", "fhirVersion": "4.0.1",
                    "format": ["json"], "software": {"name": "sanguity fakefhir (tests only)"},
                    "rest": [{"mode": "server",
                              "security": {"service": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/restful-security-service",
                                                                    "code": "SMART-on-FHIR"}]}]},
                              "resource": [{"type": t, "interaction": [{"code": "read"}, {"code": "search-type"}]} for t in types]}]}

        def _bearer(self):
            auth = self.headers.get("Authorization", "")
            t = fake._live(auth[7:] if auth.startswith("Bearer ") else None, "access")
            if not t:
                why = "invalid_token" if auth else "invalid_request"
                self._fhir(401, outcome("login", "access token missing, expired or revoked"),
                           {"WWW-Authenticate": f'Bearer realm="fakefhir", error="{why}"'})
            return t

        def _forbidden(self, message):
            self._fhir(403, outcome("forbidden", message))

        def _visible(self, token, rtype, rid):
            """Whether this token's patient may see the resource (its own compartment, or no compartment)."""
            owner = fake._owner.get((rtype, rid))
            return owner is None or owner == token.grant.patient

        def _fhir_get(self, parts):
            token = self._bearer()
            if not token:
                return
            scopes = token.grant.scopes
            if len(parts) == 1:
                return self._search(token, parts[0])
            if len(parts) == 3 and parts[0] == "Patient" and parts[2] == "$everything":
                return self._everything(token, parts[1])
            if len(parts) != 2:
                return self._fhir(404, outcome("not-found", "unknown path"))
            rtype, rid = parts
            allowed = scope_allows(scopes, rtype, "r") or (rtype == "Binary" and any(
                scope_allows(scopes, t, "r") for t in ("DocumentReference", "DiagnosticReport")))
            if not allowed:
                return self._forbidden(f"no scope to read {rtype}")
            with fake._lock:
                r = copy.deepcopy(fake._res.get((rtype, rid)))
            if not r:
                return self._fhir(404, outcome("not-found", f"{rtype}/{rid} not found"))
            if not self._visible(token, rtype, rid):
                return self._forbidden(f"{rtype}/{rid} is outside this patient's records")
            if rtype == "Binary" and "fhir+json" not in self.headers.get("Accept", ""):
                return self._send(200, fake._binary[rid], r["contentType"])
            self._fhir(200, r)

        def _matches(self, r, params):
            ms = parse_instant(r["meta"]["lastUpdated"])[0]
            for v in params.get("_lastUpdated", []):
                m = re.fullmatch(r"(eq|gt|ge|lt|le|ne)?(.+)", v)
                op, (lo, hi) = m.group(1) or "eq", parse_instant(m.group(2))
                if not {"eq": lo <= ms < hi, "ne": not lo <= ms < hi, "gt": ms >= hi, "ge": ms >= lo,
                        "lt": ms < lo, "le": ms < hi}[op]:
                    return False
            for name, fld in (("category", "category"), ("code", "code"), ("type", "type")):
                if name in params:
                    wanted = {w for v in params[name] for w in v.split(",")}
                    concepts = r.get(fld) or []
                    concepts = concepts if isinstance(concepts, list) else [concepts]
                    have = set()
                    for c in concepts:
                        for cd in c.get("coding", []):
                            have |= {cd.get("code"), f"{cd.get('system')}|{cd.get('code')}"}
                    if not have & wanted:
                        return False
            if "_id" in params and r["id"] not in {w for v in params["_id"] for w in v.split(",")}:
                return False
            return True

        def _page(self, entries, params, path):
            """A searchset Bundle for one page, with `link: next` when more remain."""
            try:
                count = min(int(params.get("_count", [fake.page_size])[0]), fake.max_count)
                offset = int(params.get("_offset", ["0"])[0])
            except ValueError:
                return self._fhir(400, outcome("invalid", "_count and _offset must be integers"))
            entries.sort(key=lambda r: (r["meta"]["lastUpdated"], r["resourceType"], r["id"]))
            page = entries[offset:offset + count] if count > 0 else []
            def link(off):
                q = [(k, v) for k, vs in params.items() if k != "_offset" for v in vs] + [("_offset", str(off))]
                return f"{fake.fhir_base}/{path}?{urllib.parse.urlencode(q)}"
            links = [{"relation": "self", "url": link(offset)}]
            if count > 0 and offset + count < len(entries):
                links.append({"relation": "next", "url": link(offset + count)})
            self._fhir(200, {"resourceType": "Bundle", "type": "searchset", "meta": {"lastUpdated": fake._stamp()},
                             "total": len(entries), "link": links,
                             "entry": [{"fullUrl": f"{fake.fhir_base}/{r['resourceType']}/{r['id']}", "resource": r,
                                        "search": {"mode": "match"}} for r in page]})

        def _search(self, token, rtype):
            if not scope_allows(token.grant.scopes, rtype, "s"):
                return self._forbidden(f"no scope to search {rtype}")
            params = self.query
            asked = params.get("patient", []) + params.get("subject", []) + params.get("beneficiary", [])
            if rtype == "Patient":
                asked += params.get("_id", [])
            if any(ref_id(a) != token.grant.patient for a in asked):
                return self._forbidden("patient= must be the signed-in patient")
            with fake._lock:
                found = [copy.deepcopy(r) for (t, i), r in fake._res.items()
                         if t == rtype and self._visible(token, t, i) and self._matches(r, params)]
            self._page(found, params, rtype)

        def _everything(self, token, pid):
            if pid != token.grant.patient:
                return self._forbidden("$everything is only for the signed-in patient")
            params = dict(self.query)
            if "_since" in params:
                params["_lastUpdated"] = ["ge" + v for v in params.pop("_since")]
            types = {w for v in params.pop("_type", []) for w in v.split(",")}
            with fake._lock:
                found = [copy.deepcopy(r) for (t, i), r in fake._res.items()
                         if fake._owner.get((t, i)) == pid and t != "Binary" and (not types or t in types)
                         and scope_allows(token.grant.scopes, t, "r") and self._matches(r, params)]
            self._page(found, self.query, f"Patient/{pid}/$everything")

    return Handler
